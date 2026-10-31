"""阿里云百炼 Qwen-VL 解析器（OpenAI 兼容 ``/chat/completions``）。

接入要点（对应 task-5 硬要求 / docs/05 §3 / §10）：

* ``response_format={"type": "json_object"}`` —— 强约束结构化输出。
* **必须显式传 ``enable_thinking=False``**：百炼 qwen3.x 默认开思考模式，
  不关会让成本 +67% 且显著变慢（docs/05 §4.2 / §7.5.3）。
* 解析失败**重试 1 次**，仍失败则**降级到 mock parser**，并把原因写进
  ``ParseResult.warnings``（R-01：降级必须显式可见，不能静默）。
* **无 key / OFFLINE_MODE=true 时绝不发起任何网络请求**（直接 mock 兜底）。
* 业务层不见厂商术语：本模块是唯一允许出现 ``qwen`` / ``dashscope`` 的地方。
"""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings, get_settings
from ..contracts import Color, Garment, Outfit, Slot
from ..towers.encoder import color_family_from_hsv
from ..utils import make_id
from .base import BaseParser, ParseResult, normalize_occasion, normalize_season
from .mock import MockParser

MAX_IMAGE_REFS = 5
DEFAULT_TIMEOUT_S = 30.0

_SYSTEM_PROMPT = """你是服装搭配解析器。只输出一个 JSON 对象，不要输出任何解释、Markdown 或代码块。
JSON 结构：
{
  "items": [
    {
      "slot": "upper|lower|outer|dress|shoes|accessory",
      "category": "中文品类名，如 棉质衬衫 / 直筒西裤 / 乐福鞋 / 托特包",
      "color": {"name": "中文色名，如 米白 / 藏青 / 玫红", "hex": "#RRGGBB", "family": "颜色家族"},
      "material": "中文细粒度材质，如 棉 / 羊毛 / 真丝 / 亚麻 / 牛仔 / 牛皮 / 针织 / 涤纶 / 金属",
      "fit": "tight|slim|regular|loose|oversized",
      "length": "crop|short|regular|long|maxi",
      "silhouette": "可选中文描述，如 落肩",
      "formality": 1到5的整数,
      "style_tags": ["中文风格标签，如 极简 / 通勤知性 / 法式 / 运动休闲"],
      "pattern": "solid|stripe|check|floral|graphic",
      "confidence": 0到1的小数,
      "evidence": "一句话视觉证据"
    }
  ],
  "confidence": 0到1的小数
}
规则：
1. 看不见的字段不要编造：无法判断的 fit/length/pattern/material 用 null。
2. 颜色必须给真实 hex，不要用 #000000 之类的占位色；不确定时给置信度 < 0.6。
3. 每套至少 1 件；只输出 JSON 对象本身。"""

_USER_HINT = """请解析这套穿搭中的全部单品。
用户声明的场合：{occasion}
用户声明的季节：{season}
参考图片数量：{n}。
若参考图片只是标识而非真实图像，请按标识语义给出一个合理的最小结构，并把 confidence 设为 0.3 以下，同时在 evidence 中说明。"""

# 中文别名 → 受控枚举（与 rules-author 的 context 归一化保持一致）
_FIT_ALIASES = {
    "紧身": "tight", "修身": "slim", "合身": "slim", "常规": "regular",
    "直筒": "regular", "宽松": "loose", "落肩": "oversized", "超大": "oversized",
    "oversized": "oversized", "tight": "tight", "slim": "slim",
    "regular": "regular", "loose": "loose",
}
_LENGTH_ALIASES = {
    "短款": "crop", "常规": "regular", "中长": "regular", "长款": "long",
    "过膝": "long", "及踝": "maxi", "拖地": "maxi", "crop": "crop",
    "short": "short", "regular": "regular", "long": "long", "maxi": "maxi",
}
_PATTERN_ALIASES = {
    "纯色": "solid", "条纹": "stripe", "格纹": "check", "格子": "check",
    "碎花": "floral", "印花": "floral", "图案": "graphic", "solid": "solid",
    "stripe": "stripe", "check": "check", "floral": "floral", "graphic": "graphic",
}
_SLOT_ALIASES = {
    "upper": "upper", "top": "upper", "上装": "upper", "上衣": "upper",
    "lower": "lower", "bottom": "lower", "下装": "lower", "裤子": "lower", "裙": "lower",
    "outer": "outer", "coat": "outer", "外套": "outer",
    "dress": "dress", "连衣裙": "dress",
    "shoes": "shoes", "shoe": "shoes", "鞋履": "shoes", "鞋": "shoes",
    "accessory": "accessory", "bag": "accessory", "配饰": "accessory", "包": "accessory",
}


class VLMError(RuntimeError):
    """VLM 调用/解析失败（内部使用，不跨模块暴露）。"""


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _formality_to_contract(value: Any) -> int | None:
    """归一化到 contracts.Garment.formality 的 int 1..5（兼容 0..1 float）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 1.0:  # docs/03 的 0..1 口径
        number = number * 4.0 + 1.0
    return max(1, min(5, int(round(number))))


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise VLMError("响应中没有 JSON 对象")
        value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise VLMError("JSON 根节点必须是对象")
    return value


class QwenVLMParser(BaseParser):
    """百炼 Qwen-VL 适配器（失败自动降级 mock）。"""

    name = "qwen_vlm"
    mode = "remote"

    def __init__(self, settings: Settings | None = None,
                 *, timeout: float = DEFAULT_TIMEOUT_S,
                 fallback: BaseParser | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = self.settings.vlm_model
        self.base_url = self.settings.dashscope_base_url.rstrip("/")
        self.api_key = self.settings.dashscope_api_key
        self.timeout = float(timeout)
        self._fallback = fallback or MockParser()

    # -- 公开接口 ---------------------------------------------------------
    def parse_outfit(
        self,
        image_refs: list[str],
        occasion: str | None = None,
        season: str | None = None,
    ) -> ParseResult:
        refs = self._clean_refs(image_refs)

        # 无 key / 显式离线：绝不建连（这是"零网络"验收的关键分支）
        if not self.api_key or not self.settings.vlm_ready:
            return self._fallback_result(refs, occasion, season,
                                         "VLM_UNAVAILABLE_FALLBACK_MOCK")

        # 快速失败：若没有任何 ref 能解析为真实图片（本地文件 / http(s) / data URI），
        # 说明调用方用的是演示标识（如 demo/commute-1）。此时**不建连**直接降级，
        # 避免「2 次重试 × 超时」的无谓延迟与计费。
        fetchable = [r for r in refs if self._as_image_part(r) is not None]
        if not fetchable:
            return self._fallback_result(refs, occasion, season,
                                         "VLM_SKIPPED_NO_FETCHABLE_IMAGE_FALLBACK_MOCK")

        payload = self.build_payload(fetchable, occasion, season)
        last_error: str | None = None
        for attempt in (1, 2):  # 失败重试 1 次（docs/03 §9.7.8 同类策略）
            try:
                content = self._post(payload)
                outfit, warnings = self._to_outfit(content, refs, occasion, season)
                return ParseResult(outfit=outfit, warnings=warnings)
            except Exception as exc:  # noqa: BLE001 - 任何失败都必须降级而不是抛给上层
                last_error = f"attempt{attempt}:{type(exc).__name__}:{exc}"

        return self._fallback_result(refs, occasion, season,
                                     f"VLM_PARSE_FAILED_FALLBACK_MOCK:{last_error}")

    def probe(self) -> dict[str, Any]:
        """CapabilityReport（docs/05 §10.2 要求运行时可知能力边界）。"""
        return {
            "provider": "dashscope-qwen",
            "model": self.model,
            "max_images": MAX_IMAGE_REFS,
            "json_object": True,
            "thinking_disabled": True,
            "ready": bool(self.api_key) and self.settings.vlm_ready,
        }

    # -- 请求构造 ---------------------------------------------------------
    def build_payload(self, refs: list[str], occasion: str | None,
                      season: str | None) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": _USER_HINT.format(
                occasion=normalize_occasion(occasion) or "未声明",
                season=normalize_season(season) or "未声明",
                n=len(refs),
            ),
        }]
        for ref in refs:
            image = self._as_image_part(ref)
            if image is not None:
                content.append(image)
            else:
                content.append({"type": "text", "text": f"参考图标识：{ref}"})
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "temperature": 0,
            # 强约束 JSON（百炼 OpenAI 兼容模式支持）
            "response_format": {"type": "json_object"},
            # 百炼默认 enable_thinking=true；显式关闭，否则成本 +67%、延迟升高
            "enable_thinking": bool(self.settings.vlm_enable_thinking),
        }

    @staticmethod
    def _as_image_part(ref: str) -> dict[str, Any] | None:
        """把 image_ref 转成 OpenAI 多模态 image_url 片段；纯标识返回 None。"""
        text = str(ref).strip()
        if text.startswith(("http://", "https://", "data:image/")):
            return {"type": "image_url", "image_url": {"url": text}}
        path = Path(text)
        if path.is_file():
            mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            return {"type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}"}}
        return None

    def _post(self, payload: dict[str, Any]) -> str:
        url = f"{self.base_url}/chat/completions"
        response = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise VLMError(f"HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise VLMError(f"响应结构异常: {exc}") from exc

    # -- 后处理 -----------------------------------------------------------
    def _to_outfit(self, raw_text: str, refs: list[str], occasion: str | None,
                   season: str | None) -> tuple[Outfit, list[str]]:
        data = _extract_json(raw_text)
        raw_items = data.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise VLMError("items 为空")

        outfit_id = make_id("oft", *refs)
        warnings: list[str] = []
        garments: list[Garment] = []
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                warnings.append(f"VLM_ITEM_DROPPED:{index}")
                continue
            garment = self._to_garment(raw, outfit_id, index, warnings)
            if garment is not None:
                garments.append(garment)
        if not garments:
            raise VLMError("没有可用单品")

        model_confidence = _clamp(data.get("confidence"), 0.0, 1.0, 0.0)
        mean_conf = sum(g.confidence for g in garments) / len(garments)
        confidence = round((mean_conf + model_confidence) / 2.0, 4) if model_confidence else round(mean_conf, 4)
        outfit = Outfit(
            outfit_id=outfit_id,
            items=garments,
            occasion=self._declared(occasion, "unspecified", warnings, "OCCASION"),
            season=self._declared(season, "unknown", warnings, "SEASON"),
            image_ref=refs[0],
            confidence=confidence,
            parse_warnings=[],
        )
        return outfit, warnings

    @staticmethod
    def _declared(raw: str | None, default: str, warnings: list[str], kind: str) -> str:
        if raw is None or not str(raw).strip():
            return default
        normalized = normalize_occasion(raw) if kind == "OCCASION" else normalize_season(raw)
        if normalized is None:
            warnings.append(f"UNKNOWN_{kind}:{raw}")
            return default
        return normalized

    def _to_garment(self, raw: dict[str, Any], outfit_id: str, index: int,
                    warnings: list[str]) -> Garment | None:
        slot_raw = str(raw.get("slot") or "").strip().lower()
        slot = _SLOT_ALIASES.get(slot_raw)
        if slot is None:
            warnings.append(f"VLM_ITEM_DROPPED:slot={slot_raw or '?'}#{index}")
            return None

        color = self._to_color(raw.get("color"))
        if color is None:
            warnings.append(f"VLM_COLOR_MISSING:item#{index}")

        fit_raw = raw.get("fit")
        fit = _FIT_ALIASES.get(str(fit_raw).strip().lower()) if fit_raw else None
        if fit_raw and fit is None:
            warnings.append(f"VLM_FIT_UNKNOWN:{fit_raw}#{index}")
        length_raw = raw.get("length")
        length = _LENGTH_ALIASES.get(str(length_raw).strip().lower()) if length_raw else None
        if length_raw and length is None:
            warnings.append(f"VLM_LENGTH_UNKNOWN:{length_raw}#{index}")
        pattern_raw = raw.get("pattern")
        pattern = _PATTERN_ALIASES.get(str(pattern_raw).strip().lower()) if pattern_raw else None

        style_tags = raw.get("style_tags")
        tags = [str(t) for t in style_tags] if isinstance(style_tags, list) else []

        material = raw.get("material")
        category = str(raw.get("category") or "").strip()
        evidence = raw.get("evidence")
        return Garment(
            garment_id=make_id("gar", outfit_id, slot, index),
            slot=Slot(slot),
            category=category,
            colors=[color] if color is not None else [],
            material=str(material) if material else None,
            fit=fit,
            silhouette=str(raw["silhouette"]) if raw.get("silhouette") else None,
            length=length,
            formality=_formality_to_contract(raw.get("formality")),
            style_tags=tags,
            pattern=pattern,
            confidence=_clamp(raw.get("confidence"), 0.0, 1.0, 0.5),
            evidence=str(evidence) if evidence else None,
        )

    @staticmethod
    def _to_color(raw: Any) -> Color | None:
        if isinstance(raw, str) and raw.strip().startswith("#") and len(raw.strip()) == 7:
            color = Color(hex=raw.strip())
            color.family = color_family_from_hsv(color.hue, color.saturation, color.lightness)
            return color
        if not isinstance(raw, dict):
            return None
        hex_value = str(raw.get("hex") or "").strip()
        if len(hex_value.lstrip("#")) != 6:
            return None
        color = Color(hex=hex_value, name=str(raw.get("name") or ""),
                      confidence=_clamp(raw.get("confidence"), 0.0, 1.0, 0.9))
        family = str(raw.get("family") or "").strip()
        color.family = family or color_family_from_hsv(color.hue, color.saturation, color.lightness)
        return color

    # -- 降级 -------------------------------------------------------------
    def _fallback_result(self, refs: list[str], occasion: str | None,
                         season: str | None, warning: str) -> ParseResult:
        result = self._fallback.parse_outfit(refs, occasion, season)
        warnings = list(result.warnings)
        warnings.append(warning)
        warnings.append(f"VLM_MODEL:{self.model}")
        outfit = result.outfit.model_copy(update={"parse_warnings": list(warnings)})
        return ParseResult(outfit=outfit, warnings=warnings)
