"""Report 文案模板层（template_only 模式的自足实现）。

为什么不把文案交给 LLM（docs/03 §9.7.1 / docs/05 §10.4）：
    文本 LLM 是**润色器**，不是判断力来源。它不可用时产品必须"降级而不失效"，
    因此 ``summary`` 与 ``suggestions`` 必须能纯模板拼装、脱离任何模型直接呈现。
    这也是 baseline INV-5「模板自足」的落点。

硬约束（docs/00 §8 / docs/03 §9.7.7）：
    * 不引入 ``rule_hits`` 之外的新判断；建议必须来自命中的
      ``suggestion`` / ``suggestion_template`` 渲染结果。
    * 建议按 ``expected_gain`` 降序、最多 3 条、"把 X 换成 Y" 级可执行。
    * 面向用户绝不出现 ``rule_id`` / ``D1..D5`` / "维度" / "delta" 等内部术语。
    * 句子里的数字只能来自结构化事实——LLM 改写后由 :func:`check_facts` 校验。
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Iterable

from pydantic import BaseModel

from ..contracts import RenderMode, Suggestion
from ..towers.encoder import STYLE_TAGS

MAX_SUGGESTIONS: int = 3

VERDICT_CN: dict[str, str] = {
    "strongly_recommended": "强烈推荐",
    "suitable": "适合",
    "neutral": "一般",
    "unsuitable": "不适合",
}

DIM_CN: dict[str, str] = {
    "D1": "色彩和谐度",
    "D2": "风格一致性",
    "D3": "身形与比例",
    "D4": "场合适配",
    "D5": "细节完成度",
}

OCCASION_CN: dict[str, str] = {
    "business": "通勤", "formal": "正式", "date": "约会", "casual": "休闲",
    "sport": "运动", "party": "派对", "travel": "旅行", "home": "居家",
    "unspecified": "未声明",
}

# 禁止出现在用户可见文本中的主观评价词（docs/03 §9.7.7 校验 3）
SUBJECTIVE_BLACKLIST: tuple[str, ...] = (
    "高级感", "不够高级", "显瘦", "气质", "百搭", "上档次", "质感很好", "质感",
    "利落", "完美", "惊艳", "绝了", "氛围感", "回头率", "时髦精", "高级审美",
)

# 颜色词表：用于"LLM 不得编造未出现过的颜色"的粗粒度校验
COLOR_TERMS: tuple[str, ...] = (
    "黑色", "白色", "灰色", "米白", "米色", "藏青", "燕麦", "炭灰", "驼色",
    "棕色", "焦糖", "金色", "银色", "裸色", "红色", "玫红", "亮绿", "军绿",
    "深灰", "牛仔蓝", "粉色", "紫色", "蓝色", "绿色", "黄色", "橙色", "卡其",
)

_INTERNAL_RULE_RE = re.compile(r"(?<![A-Za-z0-9])R-[A-Z]{2,10}-\d{1,3}(?![0-9])")
_INTERNAL_DIM_RE = re.compile(r"(?<![A-Za-z0-9])D[1-5](?![0-9])")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


# ---------------------------------------------------------------------------
# 通用归一化
# ---------------------------------------------------------------------------
def _plain(value: Any) -> Any:
    """pydantic / Enum / list / dict → 纯 Python 结构（便于模板函数消费）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return _plain(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    return value


def normalize_report_ctx(report_ctx: dict[str, Any] | Any) -> dict[str, Any]:
    data = _plain(report_ctx)
    if not isinstance(data, dict):
        raise TypeError(f"report_ctx 必须是 dict 或可序列化对象，收到 {type(report_ctx)!r}")
    return data


def _get(data: Any, key: str, default: Any = None) -> Any:
    if isinstance(data, dict):
        return data.get(key, default)
    return getattr(data, key, default)


def _as_dicts(items: Iterable[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items or []:
        plain = _plain(item)
        if isinstance(plain, dict):
            out.append(plain)
    return out


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_score(value: Any) -> str:
    number = _num(value, 0.0)
    text = f"{number:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _first_sentence(text: str, limit: int = 90) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    for index, char in enumerate(cleaned):
        if char in "。！":
            return cleaned[: index + 1]
    return cleaned[:limit]


def _is_boost(hit: dict[str, Any]) -> bool:
    severity = str(hit.get("severity") or "")
    delta = _num(hit.get("delta_schema"), 0.0)
    return severity == "boost" or delta > 0


def _hit_gain(hit: dict[str, Any]) -> float:
    """该命中项**实际生效**的分数绝对值。

    注意：`delta_applied == 0` 是合法值（预算耗尽被完全截断），此时可回收收益就是 0，
    不能回退到 `delta_schema`，否则 expected_gain 会虚报（违反 docs/04 §5.8.2 自洽要求）。
    """
    applied = hit.get("delta_applied")
    value = applied if applied is not None else hit.get("delta_schema")
    return abs(_num(value, 0.0))


def _penalty_hits(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    hits = [h for h in _as_dicts(_get(ctx, "rule_hits", []))
            if str(h.get("severity") or "") != "boost" and not _is_boost(h)]
    hits.sort(key=lambda h: (-round(_hit_gain(h), 6), str(h.get("rule_id") or "")))
    return hits


def _boost_hits(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    hits = [h for h in _as_dicts(_get(ctx, "rule_hits", [])) if _is_boost(h)]
    hits.sort(key=lambda h: (-_hit_gain(h), str(h.get("rule_id") or "")))
    return hits


# ---------------------------------------------------------------------------
# 建议（"把 X 换成 Y"级，按 expected_gain 降序）
# ---------------------------------------------------------------------------
def _fallback_action(ctx: dict[str, Any]) -> tuple[str, str]:
    """无扣分命中时的兜底建议：取最弱维度，给一条确定性可执行动作。"""
    dims = _as_dicts(_get(ctx, "dimension_scores", []))
    target_code = "D5"
    weakest_ratio = 1.0
    for dim in dims:
        code = str(dim.get("code") or "")
        maximum = _num(dim.get("max"), 0.0)
        score = _num(dim.get("score"), 0.0)
        ratio = (score / maximum) if maximum > 0 else 1.0
        if ratio < weakest_ratio:
            weakest_ratio = ratio
            target_code = code or "D5"
    profile = _plain(_get(ctx, "profile", {}) or {})
    top_tags = [str(t.get("tag")) for t in _as_dicts(_get(profile, "tags", []))
                if isinstance(t, dict) and t.get("tag")]
    top_style = top_tags[0] if top_tags else "中性基础"
    outfit = _plain(_get(ctx, "outfit", {}) or {})
    occasion_cn = OCCASION_CN.get(str(_get(outfit, "occasion") or ""), "当前场合")

    actions: dict[str, tuple[str, str]] = {
        "D1": ("配色", "把配饰换成与上装同色系的款式，让整身主色收敛到三种以内。"),
        "D2": ("风格", f"保留主基调单品，把偏离风格的那件换成{top_style}风格的基础款。"),
        "D3": ("版型比例", "把上装塞入并换高腰下装，把腰线提到身高 0.42 以上。"),
        "D4": ("场合", f"把外套或鞋换成更贴合{occasion_cn}场合正式度的款式。"),
        "D5": ("细节", "加一条与鞋同色的细腰带，或一对金属耳饰，补上细节收口。"),
    }
    target, action = actions.get(target_code, actions["D5"])
    return target, action


def _fallback_gain(ctx: dict[str, Any], suggestions_exist: bool = False) -> float:
    dims = _as_dicts(_get(ctx, "dimension_scores", []))
    if not dims:
        return 1.0
    gaps = [max(0.0, _num(d.get("max"), 0.0) - _num(d.get("score"), 0.0)) for d in dims]
    gap = max(gaps) if suggestions_exist else min(gaps)
    return round(gap if gap > 0 else 1.0, 2)


def build_suggestions(ctx: dict[str, Any]) -> list[Suggestion]:
    """从命中的 penalty/hard 规则生成可执行建议；无命中时给最弱维度兜底一条。"""
    rows: list[tuple[float, str, dict[str, Any]]] = []
    for hit in _penalty_hits(ctx):
        action = str(hit.get("suggestion") or "").strip()
        if not action:
            # 规则没给 suggestion（不应发生，契约强制）时用维度兜底动作
            _, action = _fallback_action(ctx)
        rule_id = str(hit.get("rule_id") or "")
        rows.append((round(_hit_gain(hit), 6), rule_id, hit))

    suggestions: list[Suggestion] = []
    for index, (gain, rule_id, hit) in enumerate(rows[:MAX_SUGGESTIONS]):
        dimension = str(hit.get("dimension") or "")
        suggestions.append(Suggestion(
            priority=index + 1,
            target=DIM_CN.get(dimension, "整体搭配"),
            action=str(hit.get("suggestion") or "").strip() or _fallback_action(ctx)[1],
            reason_rule_ids=[rule_id] if rule_id else [],
            expected_gain=round(gain, 2),
        ))

    if not suggestions:
        target, action = _fallback_action(ctx)
        suggestions.append(Suggestion(
            priority=1,
            target=target,
            action=action,
            reason_rule_ids=[],
            expected_gain=_fallback_gain(ctx, suggestions_exist=True),
        ))
    return suggestions


# ---------------------------------------------------------------------------
# 摘要（template_only 的完整交付物）
# ---------------------------------------------------------------------------
def build_summary(ctx: dict[str, Any], suggestions: list[Suggestion] | None = None) -> str:
    """拼装一句完整总评：得分 + 判定 + 主要问题 + 最该先改的动作。"""
    if suggestions is None:
        suggestions = build_suggestions(ctx)
    score = _fmt_score(_get(ctx, "total_score", 0.0))
    verdict = verdict_key(_get(ctx, "verdict", "neutral"))
    verdict_cn = VERDICT_CN.get(verdict, "一般")
    parts: list[str] = [f"这套搭配得 {score} 分（{verdict_cn}）。"]

    penalties = _penalty_hits(ctx)
    hard = [h for h in penalties if str(h.get("severity") or "") == "hard"]
    if bool(_get(ctx, "vetoed", False)) or hard:
        issue = hard[0] if hard else penalties[0]
        text = _first_sentence(issue.get("explanation") or issue.get("evidence") or "")
        if text:
            parts.append(f"触发硬性否决：{text}")

    issues = [h for h in penalties if h not in hard]
    if issues:
        for hit in issues[:2]:
            text = _first_sentence(hit.get("explanation") or hit.get("evidence") or "")
            if text and text not in "".join(parts):
                parts.append(text)
    elif not hard:
        boosts = _boost_hits(ctx)
        if boosts:
            titles = [str(h.get("title") or "").strip() for h in boosts[:2]]
            titles = [t for t in titles if t]
            if titles:
                parts.append("、".join(titles) + "做得不错。")

    if suggestions:
        parts.append(f"最值得先改的是：{suggestions[0].action}")
    summary = "".join(part if part.endswith(("。", "！")) else part + "。" for part in parts)
    return summary


# ---------------------------------------------------------------------------
# 事实一致性校验（防 LLM 编造结论，baseline R-03 的落地）
# ---------------------------------------------------------------------------
def _collect_numbers(value: Any, out: set[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.add(round(float(value), 2))
        return
    if isinstance(value, str):
        for match in _NUMBER_RE.findall(value):
            out.add(round(float(match), 2))
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_numbers(item, out)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_numbers(item, out)


def _allowed_texts(ctx: dict[str, Any]) -> str:
    pieces: list[str] = []
    for hit in _as_dicts(_get(ctx, "rule_hits", [])):
        for key in ("explanation", "suggestion", "evidence", "title", "reinforce"):
            if hit.get(key):
                pieces.append(str(hit[key]))
    return "".join(pieces)


def _allowed_color_terms(ctx: dict[str, Any]) -> set[str]:
    allowed: set[str] = set()
    outfit = _plain(_get(ctx, "outfit", {}) or {})
    for garment in _as_dicts(_get(outfit, "items", [])):
        for color in _as_dicts(_get(garment, "colors", [])):
            name = str(color.get("name") or "").strip()
            if name:
                allowed.add(name)
    text = _allowed_texts(ctx)
    for term in COLOR_TERMS:
        if term in text:
            allowed.add(term)
    return allowed


def verdict_key(value: Any) -> str:
    """把 Verdict 枚举/字符串统一取成**枚举值**。

    坑：``Verdict`` 是 ``(str, Enum)``，``str(Verdict.suitable)`` 得到
    ``'Verdict.suitable'`` 而不是 ``'suitable'``。任何走 ``str()`` 的查表都会落空，
    导致判定词静默退化成「一般」（实测踩过，见 docs/10 F-15）。
    """
    if value is None:
        return "neutral"
    raw = getattr(value, "value", None)
    if isinstance(raw, str):
        return raw
    text = str(value)
    return text.rsplit(".", 1)[-1] if text.startswith("Verdict.") else text


def sanitize_verdict_terms(text: str) -> str:
    """把中文稿里泄漏的英文枚举判定词替换成中文。

    实测：润色模型会写出"这套搭配总分为 76.0，判定为 suitable。"——因为上下文里
    曾把枚举当判定词传进去。这里做一层兜底，避免用户看到英文，也避免整段被判废。
    """
    if not text:
        return text
    out = text
    # 长词优先，避免 unsuitable 被 suitable 先匹配
    for enum in ("strongly_recommended", "unsuitable", "suitable", "neutral"):
        out = out.replace(enum, VERDICT_CN[enum])
    return out


def check_facts(text: str, ctx: dict[str, Any],
                reference_text: str | None = None) -> list[str]:
    """校验 LLM 润色稿；返回违规码列表（空列表 = 通过）。

    对应 docs/03 §9.7.7 四条校验：
      1. 实体白名单（颜色/风格标签）
      2. 数值封闭
      3. 禁新增主观评价
      4. 长度 ≤ 1.5 × 模板长度
    外加：判定词一致性、内部术语泄漏。
    """
    violations: list[str] = []
    polished = str(text or "").strip()
    if not polished:
        return ["EMPTY"]

    reference = str(reference_text or "")
    allowed_numbers: set[float] = set()
    _collect_numbers(ctx, allowed_numbers)
    for match in _NUMBER_RE.findall(reference):
        allowed_numbers.add(round(float(match), 2))

    for match in _NUMBER_RE.findall(polished):
        number = round(float(match), 2)
        if not any(abs(number - allowed) <= 0.011 for allowed in allowed_numbers):
            violations.append(f"NEW_NUMBER:{match}")

    for word in SUBJECTIVE_BLACKLIST:
        if word in polished:
            violations.append(f"SUBJECTIVE:{word}")

    actual_verdict = VERDICT_CN.get(verdict_key(_get(ctx, "verdict", "")), "")
    for verdict_cn in VERDICT_CN.values():
        if verdict_cn in polished and verdict_cn != actual_verdict:
            violations.append(f"VERDICT_MISMATCH:{verdict_cn}")

    if _INTERNAL_RULE_RE.search(polished):
        violations.append("INTERNAL_RULE_ID_LEAK")
    if _INTERNAL_DIM_RE.search(polished):
        violations.append("INTERNAL_DIM_CODE_LEAK")

    allowed_colors = _allowed_color_terms(ctx)
    for term in COLOR_TERMS:
        if term in polished and term not in allowed_colors and term not in reference:
            violations.append(f"NEW_COLOR:{term}")

    allowed_tags = set()
    outfit = _plain(_get(ctx, "outfit", {}) or {})
    for garment in _as_dicts(_get(outfit, "items", [])):
        for tag in _get(garment, "style_tags", []) or []:
            allowed_tags.add(str(tag))
    profile = _plain(_get(ctx, "profile", {}) or {})
    for tag_score in _as_dicts(_get(profile, "tags", [])):
        allowed_tags.add(str(tag_score.get("tag") or ""))
    text_scope = _allowed_texts(ctx) + reference
    for tag in STYLE_TAGS:
        if tag in polished and tag not in allowed_tags and tag not in text_scope:
            violations.append(f"NEW_STYLE_TAG:{tag}")

    if reference and len(polished) > 1.5 * len(reference) + 2:
        violations.append("TOO_LONG")

    # 去重但保持首次出现顺序
    seen: set[str] = set()
    unique: list[str] = []
    for code in violations:
        if code not in seen:
            seen.add(code)
            unique.append(code)
    return unique


__all__ = [
    "COLOR_TERMS",
    "DIM_CN",
    "MAX_SUGGESTIONS",
    "OCCASION_CN",
    "SUBJECTIVE_BLACKLIST",
    "VERDICT_CN",
    "build_suggestions",
    "build_summary",
    "check_facts",
    "normalize_report_ctx",
]
