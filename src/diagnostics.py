"""解析诊断：把机器码告警翻译成「分级 + 归属照片 + 人话」。

为什么需要（docs/10 F-16）
--------------------------
一次 8 张参考的请求会产生约 30 条原始告警，原先被 ``；`` 拼成一坨塞进 UI
（``IMAGE_TOKENS:352；SUBJECT_CROPPED:vlm:area=0.6872:full_body=True；…``）。
用户看到的现象是「一屏红字 = 出事了」，但其中**大部分是成功信息**：

  · ``IMAGE_TOKENS:352``            → 已把图片缩放到 352 tokens（省钱，正常）
  · ``SUBJECT_CROPPED:vlm:area=…`` → 已裁出主体（去背景，正常）
  · ``VISUAL_EMBEDDING:fashionclip:1 ×8`` → 8 张都拿到了真视觉向量（正常，本该是绿的）

真正需要用户动手的只有少数几条（手机遮挡、没拍到鞋、主体太小）。
所以「告警」这个词本身用错了。诊断必须同时做三件事：

  ① **分级** info / warn / error —— 只有 warn/error 才配叫「告警」；
  ② **归属** 哪几张参考 —— 否则用户无从修图；
  ③ **影响** 对分数的后果 —— 否则用户会把「证据不足」误读成「我穿得不好」。

``warnings`` / ``parse_warnings`` 保持原样不动：它们是规则引擎的输入
（baseline R-01），也用于向后兼容。诊断只是同一批数据的**人可读投影**。
"""
from __future__ import annotations

import re
from collections import OrderedDict
from typing import Iterable, Sequence

from .contracts import Diagnostic

INFO = "info"
WARN = "warn"
ERROR = "error"

#: 参考穿搭超过这个张数会摊薄 top1 权重（docs/10 F-12 消融结论：3–5 张最优）
MAX_RECOMMENDED_REFS = 5

_SEVERITY_RANK = {ERROR: 0, WARN: 1, INFO: 2}

#: code → (severity, 中文标题, 对分数/结论的影响)
#: code 是"归并键"：同一 code 的多条原始告警合成一条诊断，附 refs 说明是哪几张。
_TABLE: dict[str, tuple[str, str, str]] = {
    # ---------- 图片输入与预处理（正常路径，全是 info）----------
    "IMAGE_TOKENS": (INFO, "图片已按 token 预算缩放",
                     "长边缩到 ≤1024 以控制成本；画质对服装识别足够"),
    "SUBJECT_CROPPED": (INFO, "已裁出主体",
                        "背景与旁人已剔除，模型只看主要人物"),
    "SUBJECT_QUALITY": (INFO, "输入质量分",
                        "低于 1.0 时，这一套的五个维度会等比降权（证据不足不下强判断）"),
    "VISUAL_EMBEDDING": (INFO, "已提取真实视觉向量（FashionCLIP）",
                         "双塔召回用的是真视觉，不是哈希兜底"),
    # ---------- 人形检测 / 主体裁剪 ----------
    "SUBJECT:NO_PERSON_DETECTED": (INFO, "未检测到人形（平铺/挂拍属正常）",
                                   "按 0.85 轻微降权，不会被当成证据不足"),
    "SUBJECT:CROP_SKIPPED_USE_FULL_IMAGE": (INFO, "未裁主体，直接用整图",
                                            "背景干扰未消除，可能误识别旁边的物品"),
    "SUBJECT:YOLO_ONNX_MISSING": (INFO, "未装 YOLOv8，改用 VLM 定位主体",
                                  "功能不受影响，只是慢一点"),
    "SUBJECT:VLM_GROUNDING_UNAVAILABLE": (INFO, "没有可用的定位模型，跳过主体裁剪",
                                          "直接用整图解析"),
    "SUBJECT:CROP_UPSCALED": (WARN, "主体太小，裁剪后被迫放大",
                              "细节会糊 → 材质/图案/鞋型识别可信度下降，建议靠近一点重拍"),
    "SUBJECT:SUBJECT_TOO_SMALL": (WARN, "主体占画面比例过小",
                                  "该套的维度分会整体偏低；建议重拍"),
    "SUBJECT:NOT_FULL_BODY": (WARN, "不是全身照",
                              "比例维度(D3)只作参考：看不到脚就判断不了裤长/裙长"),
    "SUBJECT:OCCLUDED": (WARN, "检测到遮挡",
                         "被挡住的部位识别不到，可能漏判单品或误判版型"),
    "SUBJECT:DEGENERATE_BBOX": (WARN, "主体框异常", "裁剪可能不准，建议换一张"),
    "SUBJECT:IMAGE_UNREADABLE": (ERROR, "图片读不出来", "该张被跳过，不参与画像与评审"),
    "SUBJECT:YOLO_FAIL": (WARN, "人形检测异常", "回退到整图解析"),
    "SUBJECT:GROUND_HTTP": (WARN, "主体定位请求失败", "回退到整图解析"),
    "SUBJECT:GROUND_FAIL": (WARN, "主体定位异常", "回退到整图解析"),
    # ---------- 图片解码 / 完整性 ----------
    "IMAGE_UNDECODABLE": (ERROR, "图片无法解码",
                          "HEIC / 文件损坏 / 链接失效会走到这里；该张不参与计算"),
    "INCOMPLETE_NO_LOWER_BODY": (WARN, "未识别到下装",
                                 "质量 ×0.75；多半是画面裁掉了下半身"),
    "INCOMPLETE_NO_SHOES_MAYBE_CROPPED": (WARN, "未识别到鞋类",
                                          "质量 ×0.80；可能没拍到脚，比例维度因此偏保守"),
    # ---------- VLM 解析 ----------
    "VLM_MODEL": (INFO, "解析模型", ""),
    "VLM_ITEM_DROPPED": (WARN, "有条目被丢弃",
                         "该单品字段不合法（槽位/颜色），未进入打分"),
    "VLM_COLOR_MISSING": (WARN, "单品颜色缺失", "色彩维度(D1)对这套的判断力下降"),
    "VLM_FIT_UNKNOWN": (INFO, "版型未知", "比例维度(D3)少一条证据"),
    "VLM_LENGTH_UNKNOWN": (INFO, "长度未知", "比例维度(D3)少一条证据"),
    "UNKNOWN_OCCASION": (INFO, "场合未声明", "场合维度(D4)按「未指定」处理，不做硬性要求"),
    "UNKNOWN_SEASON": (INFO, "季节未声明", "季节相关规则不触发"),
    # ---------- 降级（最严重：结论不可用）----------
    "FALLBACK_MOCK": (ERROR, "已降级为 mock —— 你的照片没有被真正解析",
                      "本页所有分数与结论只验证链路，不能用来评估"),
    # ---------- 产品建议 ----------
    "PROFILE_REFS_ABOVE_RECOMMENDED": (WARN, "参考张数超过推荐",
                                       "超过 5 张会摊薄 top1 风格权重；实测 3–5 张同一风格"
                                       "主线最优（docs/10 F-12）"),
}

#: 兜底：未登记的新 code 按 warn 处理，并保留原 code 让人看得见
_UNKNOWN = (WARN, "", "未登记的诊断码；补 docs/10 F-16 的映射表")

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


# --------------------------------------------------------------------------
# 解析原始告警
# --------------------------------------------------------------------------
def code_of(warning: str) -> str:
    """``SUBJECT:OCCLUDED:手机`` → ``SUBJECT:OCCLUDED``；``IMAGE_TOKENS:352`` → ``IMAGE_TOKENS``。"""
    raw = str(warning)
    parts = raw.split(":")
    if parts[0] == "SUBJECT" and len(parts) > 1:
        head = re.sub(r"_\d+$", "", parts[1])   # GROUND_HTTP_404 → GROUND_HTTP
        return f"SUBJECT:{head}"
    code = parts[0]
    if "FALLBACK_MOCK" in code:                 # 四种降级原因合并成一条
        return "FALLBACK_MOCK"
    if code.startswith("VLM_") and code.endswith("_RETRY"):
        return "VLM_RETRY"
    return code


def severity_of(code: str) -> str:
    return _TABLE.get(code, _UNKNOWN)[0]


def label_of(code: str) -> str:
    sev, label, _ = _TABLE.get(code, _UNKNOWN)
    return label or code


def impact_of(code: str) -> str:
    return _TABLE.get(code, _UNKNOWN)[2]


def is_registered(code: str) -> bool:
    return code in _TABLE


# --------------------------------------------------------------------------
# detail：同一 code 的多条原始值 → 一句人话
# --------------------------------------------------------------------------
def _short(v: float) -> str:
    return f"{v:g}"


def _detail(code: str, raws: Sequence[str]) -> str:
    if code == "IMAGE_TOKENS":
        vals = [int(m.group()) for r in raws if (m := _NUM_RE.search(r.split(":", 1)[-1]))]
        if not vals:
            return ""
        return f"{min(vals)}–{max(vals)} tokens" if min(vals) != max(vals) else f"{vals[0]} tokens"

    if code == "SUBJECT_QUALITY":
        vals = [r.split(":", 1)[1] for r in raws if ":" in r]
        if not vals:
            return ""
        hist = OrderedDict()
        for v in vals:
            hist[v] = hist.get(v, 0) + 1
        if len(hist) == 1:
            return f"最低 {vals[0]}" if float(vals[0]) < 1.0 else "全部 1.0（无降权）"
        body = " / ".join(f"{v}×{c}" for v, c in hist.items())
        lo = _short(min(float(v) for v in hist))
        return f"最低 {lo}；{body}"

    if code == "VISUAL_EMBEDDING":
        return f"{len(raws)} 张已编码"

    if code == "SUBJECT_CROPPED":
        areas, full = [], []
        for r in raws:
            for seg in r.split(":")[2:]:
                if seg.startswith("area="):
                    areas.append(float(seg[5:]))
                elif seg.startswith("full_body="):
                    full.append(seg[10:].lower() == "true")
        bits = []
        if areas:
            bits.append(f"面积 {min(areas):.1%}–{max(areas):.1%}"
                        if min(areas) != max(areas) else f"面积 {areas[0]:.1%}")
        if full:
            bits.append(f"{sum(full)}/{len(full)} 张判为全身")
        return "，".join(bits)

    if code == "IMAGE_UNDECODABLE":
        tails = list(OrderedDict.fromkeys(r.split(":", 1)[-1] for r in raws))
        return "、".join(t[-40:] for t in tails[:3]) + ("…" if len(tails) > 3 else "")

    if code == "FALLBACK_MOCK":
        return raws[0].split(":", 1)[-1][:200] if ":" in raws[0] else ""

    if code == "PROFILE_REFS_ABOVE_RECOMMENDED":
        return f"{raws[0].split(':', 1)[-1]} 张"

    if code.startswith("SUBJECT:"):
        tails = list(OrderedDict.fromkeys(
            ":".join(r.split(":")[2:]) for r in raws if len(r.split(":")) > 2))
        return "、".join(t for t in tails if t)

    if code.startswith("VLM_") or code.startswith("UNKNOWN_"):
        tails = list(OrderedDict.fromkeys(r.split(":", 1)[-1] for r in raws if ":" in r))
        return "、".join(t[-40:] for t in tails[:3])

    return ""


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------
def build_diagnostics(per_photo: Sequence[Sequence[str]],
                      extras: Iterable[str] = ()) -> list[Diagnostic]:
    """``per_photo`` 是**按照片分组**的原始告警（下标 0 → 第 1 张）。

    分组而不是打平，才能回答"是哪几张图有问题"——这是修图的前提。
    """
    groups: "OrderedDict[str, dict]" = OrderedDict()
    for idx, warns in enumerate(per_photo, start=1):
        for w in warns or ():
            code = code_of(w)
            g = groups.get(code)
            if g is None:
                g = groups[code] = {"raws": [], "refs": []}
            g["raws"].append(str(w))
            if idx not in g["refs"]:
                g["refs"].append(idx)

    for w in extras or ():
        code = code_of(w)
        g = groups.get(code)
        if g is None:
            g = groups[code] = {"raws": [], "refs": []}
        g["raws"].append(str(w))

    items: list[tuple[int, Diagnostic]] = []
    for order, (code, g) in enumerate(groups.items()):
        sev = severity_of(code)
        items.append((order, Diagnostic(
            code=code,
            severity=sev,  # type: ignore[arg-type]
            label=label_of(code),
            detail=_detail(code, g["raws"]),
            impact=impact_of(code),
            count=len(g["raws"]),
            refs=g["refs"],
        )))

    # error → warn → info；同级保持首次出现顺序（确定性：INV-1）
    items.sort(key=lambda it: (_SEVERITY_RANK.get(it[1].severity, 9), it[0]))
    return [d for _, d in items]


def build_evidence_summary(per_photo: Sequence[Sequence[str]],
                           diags: Sequence[Diagnostic],
                           noun: str = "参考") -> str:
    """一行「证据质量」，放在报告最显眼处：几张成功、几张有问题。

    ``noun`` 区分场景：画像里是「参考」穿搭，报告里是待评审的「照片」。
    """
    n = len(per_photo)
    flat = [w for warns in per_photo for w in (warns or ())]
    visual = sum(1 for w in flat if str(w).startswith("VISUAL_EMBEDDING"))
    cropped = sum(1 for w in flat if str(w).startswith("SUBJECT_CROPPED"))
    bits = [f"{n} 张{noun}"]
    if visual:
        bits.append(f"视觉向量 {visual}/{n}")
    if cropped:
        bits.append(f"主体裁剪 {cropped}/{n}")
    problems = [d for d in diags if d.severity in (WARN, ERROR)]
    if problems:
        names = "、".join(d.label for d in problems[:3])
        more = f" 等 {len(problems)} 条" if len(problems) > 3 else ""
        bits.append(f"{len(problems)} 条需处理：{names}{more}")
    else:
        bits.append("无异常")
    return " · ".join(bits)


def analyze(per_photo: Sequence[Sequence[str]],
            extras: Iterable[str] = (),
            noun: str = "参考") -> tuple[list[Diagnostic], str]:
    diags = build_diagnostics(per_photo, extras)
    return diags, build_evidence_summary(per_photo, diags, noun=noun)
