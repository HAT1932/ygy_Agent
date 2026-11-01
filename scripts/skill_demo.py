"""最小可运行 skill 脚本 —— 「识别衣服 → 判断是否适合用户 → 说明原因」。

定位：面试小任务的**附录**，不是生产代码。单文件、零第三方依赖（只用 stdlib），
目的是证明三件事可以复用：
  1. 用户塔 / 衣服塔 / 判断层三层是分开的，各自可替换；
  2. 判断靠规则引擎（确定性、可追溯），大模型只做「解析」和「润色」；
  3. 结构化输出必须校验，校验失败要显式降级（绝不静默假装成功）。

用法：
  python scripts/skill_demo.py --offline            # 不联网，用内置样例跑 5 件衣服
  python scripts/skill_demo.py --offline --literal  # 对照组：词面精确比对（复现"语义失真"）
  python scripts/skill_demo.py --online             # 读 .env 里的 key，真实调用 VLM/LLM
  python scripts/skill_demo.py --image user_picture/1.jpg --profile-json data/profile.json

产物：runs/skill_demo/records.jsonl（逐条记录）+ summary.md（人看的一页结论）
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "runs" / "skill_demo"


# =============================================================================
# 1. 常量：维度预算 / 判定阈值（与项目契约一致，改这里等于改产品口径）
# =============================================================================
DIMENSIONS = {
    "D1": {"name": "色彩和谐度", "max": 2000, "base": 1560, "boost": 440},
    "D2": {"name": "风格一致性", "max": 2500, "base": 1800, "boost": 700},
    "D3": {"name": "身形与比例", "max": 2000, "base": 1320, "boost": 680},
    "D4": {"name": "场合适配", "max": 2000, "base": 1360, "boost": 640},
    "D5": {"name": "细节完成度", "max": 1500, "base": 960, "boost": 540},
}
BASE_CENTI = sum(d["base"] for d in DIMENSIONS.values())          # 7000 == 70.00 分
VERDICT_CN = {"strongly_recommended": "强烈推荐", "suitable": "适合",
              "neutral": "一般", "unsuitable": "不适合"}

# 受控风格词表（衣服塔只能从这里选，避免模型自由发挥导致匹配不上）
STYLE_TAGS = ["极简", "通勤知性", "法式", "甜美", "运动休闲", "街头潮流", "商务正装", "机能风",
              "工装", "正式", "优雅", "经典", "简约", "商务", "浪漫", "甜酷", "华丽", "潮流",
              "休闲", "居家", "舒适", "洛丽塔", "硬核工装", "复古", "学院"]

# 风格家族：受控标签 + 别名 → 同一族。**这就是"语义失真"的修法**：
# 模型写 Y2K、用户说千禧、词表里只有甜酷，三者必须在同一族里被认出来。
FAMILY_DEF = [
    ("千禧辣妹", ["甜酷", "街头潮流"], ["y2k", "千禧", "古早", "原宿", "辣妹", "亚文化", "多巴胺"]),
    ("街头潮流", ["街头潮流", "潮流"], ["街头", "高街", "嘻哈", "朋克", "个性"]),
    ("复古怀旧", ["复古"], ["美式复古", "古着", "港风", "vintage"]),
    ("甜美少女", ["甜美", "浪漫"], ["日系少女", "少女感", "软妹", "仙气"]),
    ("极简通勤", ["极简", "简约", "通勤知性"], ["极简", "简约", "通勤", "知性", "性冷淡"]),
    ("商务正装", ["商务正装", "商务", "正式"], ["正装", "西装", "职业"]),
    ("法式优雅", ["法式", "优雅"], ["法式", "优雅", "慵懒", "chic"]),
    ("运动休闲", ["运动休闲", "休闲"], ["运动", "健身", "瑜伽", "sporty", "athleisure"]),
    ("机能工装", ["机能风", "工装", "硬核工装"], ["机能", "工装", "户外", "techwear"]),
    ("学院预科", ["学院", "经典"], ["学院", "jk", "制服", "预科", "preppy"]),
    ("居家舒适", ["居家", "舒适"], ["居家", "家居", "睡衣", "loungewear"]),
    ("华丽派对", ["华丽", "洛丽塔"], ["华丽", "亮片", "派对", "礼服"]),
]
TAG_TO_FAMILY: dict[str, str] = {}
for _fam, _core, _alias in FAMILY_DEF:
    for _w in _core + _alias:
        TAG_TO_FAMILY[_w.lower()] = _fam

# 正式度：用描述词而不是裸数字（数字没有方向，实测会把卫衣标成"最正式"）
FORMALITY_DESC = {"loungewear": 1, "家居": 1, "居家": 1, "睡衣": 1,
                  "casual": 2, "休闲": 2, "日常": 2,
                  "smart_casual": 3, "半正式": 3, "商务休闲": 3,
                  "business": 4, "商务": 4, "正装": 4,
                  "formal": 5, "礼服": 5, "晚宴": 5}

# 主观评价词黑名单：报告里不许出现（出现了就是模型在编，不是在解释）
SUBJECTIVE_BLACKLIST = ("高级感", "显瘦", "气质", "百搭", "上档次", "质感", "利落",
                        "完美", "惊艳", "绝了", "氛围感", "回头率")


# =============================================================================
# 2. 输入契约校验（衣服塔 / 用户塔的产物都从这里进）
# =============================================================================
def _num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def color_of(raw) -> dict | None:
    """{'hex': '#AABBCC'} → 补齐 hue/saturation/lightness（确定性推导，不需要模型）。"""
    import colorsys

    if isinstance(raw, str):
        raw = {"hex": raw}
    if not isinstance(raw, dict):
        return None
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(raw.get("hex") or "").strip())
    if not m:
        return None
    v = m.group(1)
    r, g, b = (int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return {"hex": f"#{v.upper()}", "name": raw.get("name") or "",
            "hue": round(h * 360, 1), "saturation": round(s, 3), "lightness": round(l, 3)}


def garment_of(raw: dict, warnings: list[str]) -> dict:
    gid = str(raw.get("garment_id") or f"g{len(warnings)}")
    slot = str(raw.get("slot") or "").strip()
    if slot not in {"upper", "lower", "outer", "dress", "shoes", "accessory"}:
        warnings.append(f"{gid}: slot={slot!r} 不在枚举内，按 accessory 处理")
        slot = "accessory"

    tags = [t for t in (raw.get("style_tags") or []) if isinstance(t, str)]
    unknown = [t for t in tags if t not in STYLE_TAGS]
    if unknown:
        # 不丢弃：留在 style_words 里，家族层仍能认出来（F-17 的核心修法）
        warnings.append(f"{gid}: style_tags 含词表外词 {unknown}，已降级为 style_words")

    formality = raw.get("formality")
    if isinstance(formality, str):
        formality = FORMALITY_DESC.get(formality.strip().lower(), _num(formality))
    formality = _num(formality)
    if formality is not None:
        formality = int(max(1, min(5, round(formality))))

    return {
        "garment_id": gid, "slot": slot, "category": str(raw.get("category") or ""),
        "colors": [c for c in (color_of(x) for x in (raw.get("colors") or [])) if c],
        "formality": formality,
        "style_tags": tags, "style_words": [w for w in (raw.get("style_words") or []) if isinstance(w, str)],
        "pattern": raw.get("pattern") or "solid", "pattern_detail": raw.get("pattern_detail"),
        "merchant_title": raw.get("merchant_title") or "",
        "fit": raw.get("fit"), "length": raw.get("length"),
        "confidence": _num(raw.get("confidence"), 1.0),
    }


def outfit_of(raw: dict) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    items = [garment_of(g, warnings) for g in (raw.get("items") or []) if isinstance(g, dict)]
    if not items:
        raise ValueError("outfit.items 为空：衣服塔必须先产出至少 1 件单品")
    for g in items:
        if not g["colors"]:
            warnings.append(f"{g['garment_id']}: 无可用颜色，色彩维度对该件失去证据")
    slots = {g["slot"] for g in items}
    for need in ("shoes",):
        if need not in slots:
            warnings.append("解析告警 INCOMPLETE_NO_SHOES：缺少鞋履，比例维度证据不足")
    return {"outfit_id": raw.get("outfit_id") or "oft_unknown", "items": items,
            "occasion": raw.get("occasion"), "season": raw.get("season")}, warnings


def profile_of(raw: dict) -> dict:
    """用户塔产物。tags 可为空（冷启动），但必须显式标注 confidence。"""
    tags = [{"tag": str(t.get("tag")), "weight": _num(t.get("weight"), 0.0)}
            for t in (raw.get("tags") or []) if isinstance(t, dict)]
    tags.sort(key=lambda x: (-x["weight"], x["tag"]))
    fams = [t["tag"] for t in (raw.get("family_tags") or []) if isinstance(t, dict)] or \
           [TAG_TO_FAMILY.get(t["tag"].lower(), "") for t in tags]
    return {
        "profile_id": raw.get("profile_id") or "prf_unknown",
        "tags": tags,
        "family_tags": [f for f in fams if f][:5],
        "banned_styles": [str(s) for s in (raw.get("banned_styles") or [])],
        "explore_tags": [str(s) for s in (raw.get("explore_tags") or [])],
        "preferred_formality": _num(raw.get("preferred_formality")),
        "n_ref_outfits": int(_num(raw.get("n_ref_outfits"), 0)),
        "confidence": _num(raw.get("confidence"), 0.0),
    }


# =============================================================================
# 3. 特征层：把两层的东西压成规则能读的标量
# =============================================================================
def families_of(text: str) -> set[str]:
    low = (text or "").lower()
    return {fam for fam, core, alias in FAMILY_DEF
            if any(w.lower() in low for w in core + alias)}


def outfit_families(outfit: dict) -> set[str]:
    fams: set[str] = set()
    for g in outfit["items"]:
        for t in g["style_tags"]:
            if t in TAG_TO_FAMILY or t.lower() in TAG_TO_FAMILY:
                fams.add(TAG_TO_FAMILY.get(t.lower(), TAG_TO_FAMILY.get(t, "")))
        for text in (" ".join(g["style_words"]), g["merchant_title"], g["category"]):
            fams |= families_of(text)
    return {f for f in fams if f}


def weighted_formality(outfit: dict) -> tuple[float | None, float]:
    """按单品权重求整套正式度，并返回证据覆盖率（决定 D4 是否可判）。"""
    w = {"dress": 1.0, "upper": 1.0, "lower": 1.0, "outer": 0.8, "shoes": 0.6, "accessory": 0.45}
    total, weight, covered = 0.0, 0.0, 0.0
    for g in outfit["items"]:
        weight += w.get(g["slot"], 0.5)
        if g["formality"] is None:
            continue
        covered += w.get(g["slot"], 0.5)
        total += w.get(g["slot"], 0.5) * g["formality"]
    if covered <= 0:
        return None, 0.0
    return total / covered, covered / max(weight, 1e-6)


def _primary(g: dict) -> dict | None:
    return g["colors"][0] if g["colors"] else None


def _is_chromatic(c: dict) -> bool:
    """黑白灰没有色相：saturation 太低的一律排除（F-17 的假撞色就是漏了这一步）。"""
    return c["saturation"] >= 0.15


def _distinct_colors(colors: list[dict]) -> list[dict]:
    """把近似色视为同一个"主色"：三双黑鞋不该被算成三个颜色。"""
    reps: list[dict] = []
    for c in colors:
        same = False
        for r in reps:
            hue_gap = min(abs(c["hue"] - r["hue"]), 360 - abs(c["hue"] - r["hue"]))
            if c["hex"] == r["hex"]:
                same = True
            elif not _is_chromatic(c) and not _is_chromatic(r) \
                    and abs(c["lightness"] - r["lightness"]) < 0.18:
                same = True
            elif _is_chromatic(c) and _is_chromatic(r) \
                    and hue_gap < 20 and abs(c["lightness"] - r["lightness"]) < 0.2:
                same = True
            if same:
                break
        if not same:
            reps.append(c)
    return reps


# =============================================================================
# 4. 判断层：规则引擎（每条都能给出 rule_id + 证据 + 建议）
# =============================================================================
# 风险文案：扣分项单独回答"不改会怎样"。风险 ≠ 原因，原因是"哪里不对"，风险是"后果是什么"。
RISK_TEXT = {
    "R-COLOR-001": "上镜会显花，通勤/正式场合容易被读成不专业",
    "R-COLOR-002": "视觉抓不住重点，别人记不住你穿了什么",
    "R-COLOR-004": "整体发飘，鞋包更难选，容易越搭越乱",
    "R-STYLE-001": "直接踩用户雷区，信任一次就没了",
    "R-STYLE-002": "像穿了别人的衣服，画像越准这个反差越明显",
    "R-PROP-001": "显矮，小个子身上会被放大",
    "R-OCC-001": "场合判断错误，是要付社交代价的那类错",
    "R-OCC-003": "见客户/开会时显得不够重视",
    "R-OCC-004": "在休闲场合显得有距离感",
    "R-DET-001": "细节抢戏，整体显得用力",
}


def hit(rule_id, dim, delta, title, evidence, suggestion=None, severity=None):
    sev = severity or ("boost" if delta > 0 else "soft")
    return {"rule_id": rule_id, "dim": dim, "delta": delta, "title": title,
            "severity": sev, "evidence": evidence,
            "suggestion": suggestion, "expected_gain": round(max(-delta, 0.0), 2),
            "risk": RISK_TEXT.get(rule_id) if delta < 0 else None}


def evaluate(profile: dict, outfit: dict, match_mode: str = "family") -> dict:
    """match_mode='literal' 用于复现"词表精确比对"导致的语义失真（对照组）。"""
    fams = outfit_families(outfit)
    pfams = set(profile["family_tags"])
    tags = {t["tag"] for t in profile["tags"]}
    outfit_tags = {t for g in outfit["items"] for t in g["style_tags"]}
    occasion = (outfit["occasion"] or "casual").lower()
    hits: list[dict] = []
    gates: dict[str, bool] = {}

    # ---- D1 色彩 ----------------------------------------------------------
    colors = [c for g in outfit["items"] for c in g["colors"]]
    chromatic = [c for c in colors if _is_chromatic(c)]
    if len(chromatic) >= 2:
        clash = [(a, b) for i, a in enumerate(chromatic) for b in chromatic[i + 1:]
                 if a["saturation"] >= 0.6 and b["saturation"] >= 0.6
                 and 120 < abs(a["hue"] - b["hue"]) % 360 < 240]
        if clash:
            a, b = clash[0]
            hits.append(hit("R-COLOR-001", "D1", -7.8, "高饱和撞色",
                            f"{a['hex']} 与 {b['hex']} 色相差 {abs(a['hue']-b['hue']):.0f}° 且都高饱和",
                            "把其中一件换成无彩色（黑/白/灰/米），冲突即可消解", severity="hard"))
    unique_colors = _distinct_colors(colors)
    if len(unique_colors) >= 4:
        hits.append(hit("R-COLOR-002", "D1", -4.0, "主色超过三个",
                        f"出现 {len(unique_colors)} 个不同颜色：{[c['hex'] for c in unique_colors]}",
                        "收敛到三个以内：一个主色、一个辅色、一个中性锚点"))
    neutrals = [c for c in colors if not _is_chromatic(c)]
    if colors and not neutrals:
        hits.append(hit("R-COLOR-004", "D1", -3.0, "缺少中性锚点",
                        f"{len(colors)} 件全部是有彩色，没有黑白灰米做缓冲",
                        "加一件中性色单品（鞋/包/内搭）压住整体"))
    same = [c["hex"] for c in colors]
    if len(same) - len(set(same)) >= 1:
        hits.append(hit("R-COLOR-011", "D1", 3.0, "色彩呼应",
                        "上下装或配饰出现同一色值，形成呼应", None))
    gates["D1"] = len(colors) >= 2

    # ---- D2 风格 ----------------------------------------------------------
    banned = {b for b in profile["banned_styles"]}
    banned_hit = {f for f in fams if f in banned} | {t for t in outfit_tags if t in banned}
    if banned_hit:
        hits.append(hit("R-STYLE-001", "D2", -9.0, "命中禁用风格",
                        f"画像声明不穿 {sorted(banned)},本套落在 {sorted(banned_hit)}",
                        "换掉带该风格标签的单品", severity="hard"))
    if match_mode == "literal":
        overlap = outfit_tags & tags
        pfam_overlap = set()
    else:
        overlap = outfit_tags & tags
        pfam_overlap = fams & pfams
    if not overlap and not pfam_overlap:
        hits.append(hit("R-STYLE-002", "D2", -5.0, "与画像风格无交集",
                        f"画像风格 {sorted(tags)[:3]} / 本套风格 {sorted(outfit_tags)[:3]}",
                        "至少让一件单品落回画像的主风格，或用中性单品过渡"))
    top1 = profile["tags"][0]["tag"] if profile["tags"] else None
    if top1 and ((top1 in outfit_tags) or (match_mode == "family"
                                           and TAG_TO_FAMILY.get(top1.lower()) in fams)):
        hits.append(hit("R-STYLE-008", "D2", 4.0, "命中主风格",
                        f"画像主风格「{top1}」在本套中出现", None))
    core = [g for g in outfit["items"] if g["slot"] in {"upper", "lower", "dress", "shoes", "outer"}]
    core_fams = [outfit_families({"items": [g], "occasion": None}) for g in core]
    if len(core) >= 3 and len({frozenset(x) for x in core_fams if x}) == 1:
        hits.append(hit("R-STYLE-010", "D2", 3.0, "主体单品同源",
                        "上/下/鞋落在同一风格家族", None))
    gates["D2"] = bool(fams or outfit_tags)

    # ---- D3 比例 ----------------------------------------------------------
    up, low = next((g for g in outfit["items"] if g["slot"] == "upper"), None), \
        next((g for g in outfit["items"] if g["slot"] == "lower"), None)
    if up and low and up.get("length") and low.get("length"):
        if up["length"] == "long" and low["length"] == "long":
            hits.append(hit("R-PROP-001", "D3", -4.0, "上长下长压身高",
                            f"上装 {up['length']} + 下装 {low['length']}", "上装短一档，或把衣摆塞进腰头"))
        if up["length"] in {"crop", "short"} and low["length"] in {"long", "maxi"}:
            hits.append(hit("R-PROP-009", "D3", 4.0, "上短下长",
                            f"上装 {up['length']} + 下装 {low['length']}", None))
    gates["D3"] = bool(up or low) and any(g.get("length") for g in outfit["items"])

    # ---- D4 场合 ----------------------------------------------------------
    formal, coverage = weighted_formality(outfit)
    gates["D4"] = coverage >= 0.5 and formal is not None
    if gates["D4"]:
        if formal <= 2 and occasion in {"formal", "business", "date"}:
            hits.append(hit("R-OCC-001", "D4", -6.8, "休闲装进正式场合",
                            f"整套正式度 {formal:.1f}/5，场合 {occasion}",
                            "换一件有结构感的外套或衬衫，把整体正式度提到 3 以上",
                            severity="hard"))
        pref = profile["preferred_formality"]
        if pref:
            gap = formal - pref
            if gap <= -2:
                hits.append(hit("R-OCC-003", "D4", -4.0, "正式度不足",
                                f"本套 {formal:.1f} / 习惯 {pref:.1f}", "加一件半正式单品做过渡"))
            elif gap >= 2:
                hits.append(hit("R-OCC-004", "D4", -3.0, "正式度过剩",
                                f"本套 {formal:.1f} / 习惯 {pref:.1f}", "把西装换成针织或开衫"))
            elif abs(gap) <= 0.5:
                hits.append(hit("R-OCC-009", "D4", 4.0, "正式度精确匹配",
                                f"本套 {formal:.1f} 与习惯 {pref:.1f} 一致", None))

    # ---- D5 细节 ----------------------------------------------------------
    acc = [g for g in outfit["items"] if g["slot"] == "accessory"]
    if len(acc) >= 3:
        hits.append(hit("R-DET-001", "D5", -3.0, "配饰过载",
                        f"{len(acc)} 件配饰同时出现", "留两件，其余摘掉"))
    slots = {g["slot"] for g in outfit["items"]}
    closed = ({"upper", "lower", "shoes"} <= slots) or ({"dress", "shoes"} <= slots)
    if closed:
        hits.append(hit("R-DET-008", "D5", 4.0, "三点收口完整", "上/下/鞋齐备，视觉有收口", None))
    gates["D5"] = closed

    return {"hits": hits, "gates": gates}


# =============================================================================
# 5. 打分：整数厘点、预算守恒、可加性可验证（INV-1 / INV-2 / INV-4）
# =============================================================================
def score(hits: list[dict], gates: dict[str, bool]) -> dict:
    pen = {d: 0 for d in DIMENSIONS}
    boo = {d: 0 for d in DIMENSIONS}
    applied = []
    for h in hits:
        spec = DIMENSIONS[h["dim"]]
        raw = int(round(h["delta"] * 100))
        if raw >= 0:
            room = max(spec["boost"] - boo[h["dim"]], 0)
            used = min(raw, room)
            boo[h["dim"]] += used
            applied.append({**h, "delta_applied": used / 100, "clipped": used != raw})
        else:
            need = -raw
            room = max(spec["base"] - pen[h["dim"]], 0)
            used = min(need, room)
            pen[h["dim"]] += used
            applied.append({**h, "delta_applied": -used / 100, "clipped": used != need})

    dims, total = [], 0
    for code, spec in DIMENSIONS.items():
        sc = spec["base"] - pen[code] + boo[code]
        sc = max(0, min(sc, spec["max"]))
        total += sc
        dims.append({"code": code, "name": spec["name"], "score": sc / 100,
                     "max": spec["max"] / 100, "base": spec["base"] / 100,
                     "penalty": pen[code] / 100, "boost": boo[code] / 100,
                     "gated": not gates.get(code, True)})

    vetoed = any(h["severity"] == "hard" for h in applied)
    if vetoed:
        verdict = "unsuitable"
    elif total >= 8000:
        verdict = "strongly_recommended"
    elif total >= 6500:
        verdict = "suitable"
    elif total >= 5000:
        verdict = "neutral"
    else:
        verdict = "unsuitable"
    return {"total_centi": total, "base_centi": BASE_CENTI, "vetoed": vetoed,
            "verdict": verdict, "dimensions": dims, "hits": applied,
            "risks": [{"rule_id": h["rule_id"], "risk": h["risk"],
                       "suggestion": h["suggestion"], "expected_gain": h["expected_gain"]}
                      for h in applied if h.get("risk")]}


# =============================================================================
# 6. 输出校验：结构化输出 / 文案 / 不变量，三样都要查
# =============================================================================
def validate(result: dict) -> list[str]:
    problems: list[str] = []
    if result["total_centi"] != result["base_centi"] + sum(
            int(round(h["delta_applied"] * 100)) for h in result["hits"]):
        problems.append("INV-2 违反：总分 != 基准分 + Σ实际生效分")
    if sum(int(round(d["score"] * 100)) for d in result["dimensions"]) != result["total_centi"]:
        problems.append("维度分之和 != 总分")
    if result["vetoed"] and result["verdict"] != "unsuitable":
        problems.append("veto 必须判「不适合」")
    for h in result["hits"]:
        if not h["rule_id"] or not h["evidence"]:
            problems.append(f"{h['rule_id']} 缺少 rule_id 或证据（违反可追溯）")
        if h["severity"] == "soft" and not h["suggestion"]:
            problems.append(f"{h['rule_id']} 是扣分项但没有建议")
    return problems


def check_text(text: str, facts: dict) -> list[str]:
    """LLM 润色后必须过这一关：不许新增数字、不许主观评价、不许出现内部术语。"""
    problems = []
    nums = set(re.findall(r"\d+(?:\.\d+)?", json.dumps(facts, ensure_ascii=False)))
    for n in re.findall(r"\d+(?:\.\d+)?", text):
        if n not in nums and f"{float(n):.1f}" not in nums and n.rstrip("0").rstrip(".") not in nums:
            problems.append(f"出现输入里没有的数字：{n}")
    for w in SUBJECTIVE_BLACKLIST:
        if w in text:
            problems.append(f"出现主观评价词：{w}")
    if re.search(r"R-[A-Z]{2,10}-\d+", text) or re.search(r"(?<![A-Za-z])D[1-5](?![0-9])", text):
        problems.append("出现内部规则编号/维度代号")
    return problems


# =============================================================================
# 7. 模型调用：两个角色分开，都走 OpenAI 兼容接口
# =============================================================================
PROFILE_PROMPT = """你是用户风格画像聚合器。输入是同一用户 3-5 套参考穿搭的结构化解析结果。
只输出一个 JSON 对象，不要解释、不要 Markdown：
{
  "tags": [{"tag": "受控词表里的风格", "weight": 0到1的小数}],
  "family_tags": ["风格家族名"],
  "preferred_formality": 1到5的整数,
  "banned_styles": [],
  "explore_tags": [],
  "signature_colors": ["最多3个主色名"],
  "silhouette_habits": ["最多2条版型习惯，如 上短下长 / 宽松为主"],
  "confidence": 0到1的小数
}
规则：
1. tags 必须从给定受控词表里选，最多 5 个，weight 归一化后和为 1，按权重降序。
2. 参考数量 <3 时 confidence 必须 <0.6。
3. 只在证据里出现过的风格才能写；拿不准就不写，不要凑数。
4. family_tags 用来表达受控词表覆盖不到的说法（如 Y2K/千禧/古早 都归「千禧辣妹」）。"""

EXPLAIN_PROMPT = """你是中文穿搭报告文案润色器，只能改写给定事实，禁止新增任何事实。
硬约束：不得新增数字；不得使用主观评价词（高级感/显瘦/气质/百搭/质感/完美等）；
判定词只能用一个给定值；不得出现规则编号或维度代号；1-3 句，以「。」结尾。
只输出 JSON：{"summary": "..."}"""

# 衣服塔：图像 → 结构化单品。字段与 contracts.Garment 对齐，枚举必须闭集。
PARSE_PROMPT = """你是服装搭配解析器。只输出一个 JSON 对象，不要解释、不要 Markdown。
{"items":[{"slot":"upper|lower|outer|dress|shoes|accessory","category":"中文品类",
"color":{"name":"中文色名","hex":"#RRGGBB"},"material":"棉/羊毛/真丝/牛仔/牛皮/针织",
"fit":"tight|slim|regular|loose|oversized","length":"crop|short|regular|long|maxi",
"formality":"loungewear|casual|smart_casual|business|formal","style_tags":["受控词表1-2个"],
"style_words":["细粒度线索，如 低腰/露脐/厚底/波点/豹纹/金属配件"],
"pattern":"solid|stripe|check|floral|graphic","pattern_detail":"波点/豹纹/千鸟格，纯色留空",
"merchant_title":"<=20字商家口吻标题","confidence":0到1,"evidence":"一句视觉证据"}]}
规则：1) 看不见的字段给 null，不要编造；2) 每件 style_tags 最多 2 个且只给主导风格；
3) 低腰+露脐/短款+厚底+波点/豹纹+金属配件 通常属 Y2K/千禧/古早 谱系，受控词写 甜酷 或 街头潮流，
把 千禧感/古早感/波点 写进 style_words；4) 下缘只露鞋头也要输出 shoes；
5) 明显的包/袜/腰带各列一条 accessory；6) 整套不超过 6 件。」"""


def _env(key: str) -> str | None:
    if os.environ.get(key):
        return os.environ[key]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def call_json(system: str, user: str, *, model: str, api_key: str, base_url: str,
              images: list[str] | None = None, timeout: float = 40.0) -> dict | None:
    """一次调用 + 一次重试；失败返回 None，由调用方显式降级（绝不假装成功）。"""
    content: list | str = user
    if images:
        content = [{"type": "text", "text": user}]
        for path in images:
            data = base64.b64encode(Path(path).read_bytes()).decode()
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{data}"}})
    payload = {"model": model, "temperature": 0, "response_format": {"type": "json_object"},
               "enable_thinking": False,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": content}]}
    req = urllib.request.Request(f"{base_url.rstrip('/')}/chat/completions",
                                 data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers={"Authorization": f"Bearer {api_key}",
                                          "Content-Type": "application/json"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode())
            text = body["choices"][0]["message"]["content"].strip()
            if text.startswith("```"):
                text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
            start, end = text.find("{"), text.rfind("}")
            return json.loads(text[start:end + 1]) if start >= 0 else None
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as exc:
            print(f"  [warn] 第 {attempt} 次调用失败：{exc}")
            time.sleep(0.5)
    return None


# =============================================================================
# 8. 兜底画像（离线/失败时用：确定性聚合，且**显式标注**来源）
# =============================================================================
def fallback_profile(refs: list[dict]) -> dict:
    w = {"dress": 1.0, "upper": 1.0, "lower": 1.0, "outer": 0.8, "shoes": 0.6, "accessory": 0.45}
    votes: dict[str, float] = {}
    fams: dict[str, float] = {}
    formality, fw = 0.0, 0.0
    for outfit in refs:
        per = sum(w.get(g["slot"], 0.5) * g["confidence"] for g in outfit["items"])
        for g in outfit["items"]:
            share = w.get(g["slot"], 0.5) * g["confidence"] / max(per, 1e-6)
            for t in g["style_tags"]:
                votes[t] = votes.get(t, 0.0) + share
                fam = TAG_TO_FAMILY.get(t.lower()) or TAG_TO_FAMILY.get(t)
                if fam:
                    fams[fam] = fams.get(fam, 0.0) + share
            for f in families_of(" ".join(g["style_words"]) + " " + g["merchant_title"]):
                fams[f] = fams.get(f, 0.0) + share
            if g["formality"] is not None:
                formality += w.get(g["slot"], 0.5) * g["formality"]
                fw += w.get(g["slot"], 0.5)
    n = max(len(refs), 1)
    tags = [{"tag": k, "weight": round(v / n, 4)}
            for k, v in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]
    return {"profile_id": "prf_offline", "tags": tags,
            "family_tags": [k for k, _ in sorted(fams.items(), key=lambda kv: (-kv[1], kv[0]))[:5]],
            "banned_styles": [], "explore_tags": [],
            "preferred_formality": round(formality / fw) if fw else None,
            "n_ref_outfits": len(refs),
            "confidence": round(min(0.95, 0.30 + 0.13 * len(refs)), 2),
            "_source": "rule_aggregate"}


def build_profile(refs: list[dict], online: bool) -> tuple[dict, str]:
    if online:
        key = _env("DEEPSEEK_API_KEY") or _env("DASHSCOPE_API_KEY")
        if key:
            payload = json.dumps([{k: v for k, v in o.items() if k != "items"} |
                                  {"items": [{kk: vv for kk, vv in g.items() if kk != "colors"} |
                                             {"colors": [c["hex"] for c in g["colors"]]}
                                             for g in o["items"]]} for o in refs],
                                 ensure_ascii=False)
            got = call_json(PROFILE_PROMPT, f"受控词表：{' / '.join(STYLE_TAGS)}\n参考穿搭：{payload}",
                            model=_env("LLM_MODEL") or "deepseek-chat", api_key=key,
                            base_url=_env("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1")
            if got and got.get("tags"):
                return {**profile_of(got), "_source": "llm"}, "llm"
            print("  [warn] 画像 LLM 输出不可用，降级为确定性聚合")
    return fallback_profile(refs), "rule_aggregate"


def explain(result: dict, online: bool) -> tuple[str, str]:
    """先拼模板事实，再让 LLM 只做润色；校验不过就退回模板。"""
    issues = [h for h in result["hits"] if h["severity"] != "boost"]
    facts = {"verdict_cn": VERDICT_CN[result["verdict"]],
             "total_score": round(result["total_centi"] / 100, 1),
             "dimensions": [{"name": d["name"], "score": d["score"], "max": d["max"]}
                            for d in result["dimensions"]],
             "issues": [{"title": h["title"], "evidence": h["evidence"]} for h in issues][:4]}
    template = (f"这套搭配得 {facts['total_score']} 分，判定为「{facts['verdict_cn']}」。"
                + ("主要问题：" + "；".join(f"{i['title']}（{i['evidence']}）" for i in issues[:3]) + "。"
                   if issues else "没有发现明显扣分项。")
                + (f"建议：{issues[0]['suggestion']}。" if issues and issues[0]["suggestion"] else ""))
    if not online:
        return template, "template"
    key = _env("DEEPSEEK_API_KEY")
    if not key:
        return template, "template"
    got = call_json(EXPLAIN_PROMPT, json.dumps({**facts, "改写基线": template}, ensure_ascii=False),
                    model=_env("LLM_MODEL") or "deepseek-chat", api_key=key,
                    base_url=_env("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1")
    text = (got or {}).get("summary", "").strip()
    if text and not check_text(text, facts):
        return text, "llm_polished"
    if text:
        print(f"  [warn] 润色文案校验未过（{check_text(text, facts)[:2]}），回退模板")
    return template, "template"


# =============================================================================
# 9. 批量跑 + 落盘
# =============================================================================
def sample_cases() -> tuple[list[dict], list[dict]]:
    """3 套参考（Y2K 街头主线）+ 5 套待评审。第 1 套是 F-17 的真实案例。"""
    def g(gid, slot, cat, hexv, formality, tags, **kw):
        return {"garment_id": gid, "slot": slot, "category": cat,
                "colors": [{"hex": hexv, "name": kw.pop("name", "")}],
                "formality": formality, "style_tags": tags, **kw}

    refs = [
        {"outfit_id": "ref_1", "occasion": "casual", "items": [
            g("r1a", "upper", "短款印花T恤", "#F2E8DA", 2, ["甜酷", "街头潮流"], length="crop"),
            g("r1b", "lower", "低腰阔腿牛仔", "#8FA9C4", 2, ["甜酷"], length="long"),
            g("r1c", "shoes", "厚底运动鞋", "#EDEDED", 2, ["街头潮流"])]},
        {"outfit_id": "ref_2", "occasion": "casual", "items": [
            g("r2a", "upper", "露脐针织背心", "#C9D8C5", 2, ["甜酷"], length="crop"),
            g("r2b", "lower", "百褶短裙", "#2B2B2B", 2, ["甜酷"], length="short"),
            g("r2c", "shoes", "白袜厚底玛丽珍", "#F7F7F7", 3, ["甜酷"]),
            g("r2d", "accessory", "豹纹腋下包", "#B08D57", 2, ["街头潮流"])]},
        {"outfit_id": "ref_3", "occasion": "casual", "items": [
            g("r3a", "upper", "做旧水洗卫衣", "#6E6A65", 2, ["甜酷", "街头潮流"], length="regular"),
            g("r3b", "lower", "工装直筒裤", "#3A3A3A", 2, ["街头潮流"], length="long"),
            g("r3c", "shoes", "厚底板鞋", "#FFFFFF", 2, ["街头潮流"])]},
    ]
    outfits = [
        {"outfit_id": "case_y2k", "occasion": "casual", "items": [   # F-17 真实案例
            # 注意 style_tags 是模型给的"错词"（甜美/学院），真正的千禧感藏在
            # style_words 与 merchant_title 里 —— 这正是"看对了、却在词表处被丢掉"。
            g("c1a", "upper", "薄荷绿白波点连帽衫", "#9FE0CD", 2, ["甜美"], length="regular",
              style_words=["千禧感", "波点", "宽松"], merchant_title="Y2K辣妹 波点连帽卫衣"),
            g("c1b", "lower", "黑波点短裤", "#1A1A1A", 2, ["甜美"], length="short",
              style_words=["波点", "低腰"]),
            g("c1c", "shoes", "黑乐福鞋白袜", "#111111", 3, ["学院"]),
            g("c1d", "accessory", "棕色托特包", "#7A5230", 3, ["通勤知性"]),
            g("c1e", "accessory", "豹纹小包", "#B08D57", 2, ["华丽"], style_words=["豹纹"])]},
        {"outfit_id": "case_commute", "occasion": "business", "items": [
            g("c2a", "upper", "藏青针织衫", "#2E3A50", 4, ["通勤知性"], length="regular"),
            g("c2b", "lower", "燕麦直筒裤", "#D8CBB4", 4, ["通勤知性"], length="long"),
            g("c2c", "shoes", "黑乐福鞋", "#141414", 4, ["通勤知性"])]},
        {"outfit_id": "case_clash", "occasion": "date", "items": [
            g("c3a", "upper", "荧光绿短袖", "#3BFF6A", 2, ["运动休闲"], length="regular"),
            g("c3b", "lower", "玫红紧身裙", "#E4117A", 3, ["甜美"], length="short"),
            g("c3c", "shoes", "厚底运动鞋", "#FFFFFF", 2, ["运动休闲"])]},
        {"outfit_id": "case_banned", "occasion": "casual", "items": [
            g("c4a", "upper", "机能冲锋衣", "#3F4A45", 2, ["机能风"], length="regular"),
            g("c4b", "lower", "工装裤", "#4A4A3F", 2, ["工装"], length="long"),
            g("c4c", "shoes", "战术靴", "#2A2A2A", 3, ["硬核工装"])]},
        {"outfit_id": "case_formal", "occasion": "formal", "items": [
            g("c5a", "upper", "真丝衬衫", "#F6F2E8", 4, ["商务正装"], length="regular"),
            g("c5b", "lower", "西装直筒裤", "#25252B", 4, ["商务正装"], length="long"),
            g("c5c", "outer", "西装外套", "#1F1F26", 4, ["商务正装"], length="regular"),
            g("c5d", "shoes", "黑色牛津鞋", "#101010", 4, ["商务正装"])]},
    ]
    return refs, outfits


def run(offline: bool, match_mode: str) -> int:
    refs_raw, outfits_raw = sample_cases()
    refs = [outfit_of(o)[0] for o in refs_raw]
    profile, profile_src = build_profile(refs, online=not offline)
    profile["banned_styles"] = ["机能风", "工装"]   # 用户声明：绝对不穿这两类
    print(f"\n用户塔：{len(refs)} 套参考 · 主风格 {[t['tag'] for t in profile['tags'][:3]]} · "
          f"家族 {profile['family_tags'][:3]} · 习惯正式度 {profile['preferred_formality']} "
          f"· 来源 {profile_src}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records, rows = [], []
    print(f"\n{'case':14} {'判定':6} {'总分':>6}  {'D1':>5}{'D2':>6}{'D3':>6}{'D4':>6}{'D5':>6}  主要依据")
    print("-" * 108)
    for raw in outfits_raw:
        outfit, warn = outfit_of(raw)
        result = evaluate(profile, outfit, match_mode=match_mode)
        scored = score(result["hits"], result["gates"])
        problems = validate(scored)
        summary, text_src = explain(scored, online=not offline)
        top = sorted(scored["hits"], key=lambda h: h["delta_applied"])[:2]
        record = {
            "case_id": outfit["outfit_id"], "timestamp": datetime.now().isoformat(timespec="seconds"),
            "data_source": {"profile": profile_src, "parse": "structured_json",
                            "text": text_src},
            "input": {"profile": profile, "outfit": outfit},
            "output": {**scored, "summary": summary},
            "warnings": warn, "validation_problems": problems,
        }
        records.append(record)
        rows.append((outfit["outfit_id"], VERDICT_CN[scored["verdict"]],
                     scored["total_centi"] / 100, scored["dimensions"], top, summary, problems))
        dims = "".join(f"{d['score']:>6.1f}" for d in scored["dimensions"])
        basis = "；".join(h["title"] for h in top)
        print(f"{outfit['outfit_id']:14} {VERDICT_CN[scored['verdict']]:6} "
              f"{scored['total_centi']/100:>6.1f}  {dims}  {basis}")
        if problems:
            print(f"  [x] 校验未过：{problems}")

    (OUT_DIR / "records.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")

    lines = [f"# 批量结果（{datetime.now():%Y-%m-%d %H:%M}）", "",
             f"- 模式：{'离线（规则+模板）' if offline else '在线（VLM/LLM）'}｜比对方式：{match_mode}",
             f"- 用户画像：{profile['profile_id']}｜主风格 {'/'.join(t['tag'] for t in profile['tags'][:3])}"
             f"｜禁用 {'/'.join(profile['banned_styles']) or '无'}", "",
             "| case | 判定 | 总分 | 主要依据 | 校验 |", "|---|---|---|---|---|"]
    for cid, vd, total, _d, top, _s, probs in rows:
        lines.append(f"| {cid} | {vd} | {total:.1f} | "
                     f"{'；'.join(h['title'] for h in top)} | {'通过' if not probs else '未过'} |")
    (OUT_DIR / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n已写入 {OUT_DIR / 'records.jsonl'} 与 summary.md")
    return 0


def parse_image(path: str, online: bool) -> dict | None:
    """衣服塔的入口：图片 → 结构化单品。无 key / 离线时**明确拒绝**，不静默造数据。"""
    key = _env("DASHSCOPE_API_KEY")
    if not online or not key:
        print("[x] 离线或未配置 DASHSCOPE_API_KEY：拒绝解析图片。"
              "拿一份结构化 JSON 代替（这就是'模拟图像'路径），不要把假数据当结论。")
        return None
    got = call_json(PARSE_PROMPT, "请解析这套穿搭中的全部单品。",
                    model=_env("VLM_MODEL") or "qwen-vl-max", api_key=key,
                    base_url=_env("DASHSCOPE_BASE_URL")
                    or "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    images=[path], timeout=60)
    if not got or not got.get("items"):
        print("[x] VLM 返回不可用（无 items）：请检查图片是否为可解码人像，不要继续打分。")
        return None
    return got


def run_single_image(path: str, online: bool, match_mode: str = "family") -> int:
    """真图路径：解析 → 判断 → 说明。每一步失败都要停下来说清楚，不要硬着头皮给分。"""
    if not Path(path).is_file():
        print(f"[x] 找不到图片：{path}")
        return 2
    parsed = parse_image(path, online)
    if parsed is None:
        return 2

    refs = [outfit_of(o)[0] for o in sample_cases()[0]]
    profile, src = build_profile(refs, online=online)
    outfit, warn = outfit_of({**parsed, "outfit_id": Path(path).stem})
    result = evaluate(profile, outfit, match_mode=match_mode)
    scored = score(result["hits"], result["gates"])
    summary, text_src = explain(scored, online=online)
    problems = validate(scored)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    record = {"case_id": outfit["outfit_id"],
              "timestamp": datetime.now().isoformat(timespec="seconds"),
              "data_source": {"profile": src, "parse": "vlm", "text": text_src},
              "input": {"image": str(path), "profile": profile, "outfit": outfit},
              "output": {**scored, "summary": summary},
              "warnings": warn, "validation_problems": problems}
    with (OUT_DIR / "records.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps({"verdict": VERDICT_CN[scored["verdict"]],
                      "total": scored["total_centi"] / 100,
                      "summary": summary,
                      "hits": [{k: h[k] for k in ("rule_id", "title", "delta_applied", "evidence")}
                               for h in scored["hits"]]},
                     ensure_ascii=False, indent=1))
    if problems:
        print(f"[x] 校验未过：{problems}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="不联网（默认）")
    ap.add_argument("--online", action="store_true", help="真实调用 VLM/LLM，读 .env")
    ap.add_argument("--literal", action="store_true", help="对照组：词面精确比对")
    ap.add_argument("--image", help="单张图片路径：走 VLM 解析 → 判断 → 说明")
    args = ap.parse_args()
    if args.image:
        return run_single_image(args.image, online=not args.offline,
                               match_mode="literal" if args.literal else "family")
    return run(offline=not args.online, match_mode="literal" if args.literal else "family")


if __name__ == "__main__":
    raise SystemExit(main())
