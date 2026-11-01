"""规则上下文构建 —— 把 ``Outfit`` / ``StyleProfile`` / ``Features`` 归一成扁平点号键空间。

职责
----
1. 把 contracts 的窄模型（``Slot`` 只有 upper/lower/outer/shoes/accessory/dress，
   ``Garment`` 只有 category/material/fit/... ）映射成 docs/03 §7 字段字典的口径
   （``garment.top`` / ``garment.bottom`` / ``garment.bag`` …）。
2. 按 §7.1/§7.2/§7.5/§7.6 的**确定性定义**计算派生量与聚合量。
3. 把 §7.7 允许的「引擎预计算聚合字段」登记进来 —— 因为冻结契约的 DSL
   （``contracts.OPS`` / ``contracts.Condition``）**没有**量词节点（count/for_all/
   any_garment）也没有 ``hue_dist_*`` / ``intersects`` / 算术表达式，docs/03 §6.2.1 里
   这些表达必须由引擎先算成可独立测量的量，规则只做阈值判断。

模型连续输出（style_cosine / occasion_fit_prob / visual_* / area_ratio / …）走
``Features.extra``（contracts.Features 显式允许 extra，这是不改契约的唯一通道），
键约定见 ``_SECTION_PREFIX`` 与 ``_garment_ctx`` 的 overrides 参数。

字段来源标注
------------
``[§7.x]``      docs/03 §7 字段字典原文
``[§7.7]``      文档已登记的预计算字段
``[EXT]``       本实现新增登记的预计算字段（全部是「可独立测量的量」，不含价值判断；
                新增理由与用到的规则逐条列在 docs/03 §7.7 的同一口径下，交付报告里同步给 lead）

本实现新增的 [EXT] 预计算字段（每条都可被独立测量/复核，不是价值判断结论）::

    outfit.colors.top_bottom_hue_dist / top_bottom_lightness_diff   两件单品之间的差值
    outfit.colors.max_sat_area_ratio / max_sat_hue                  最高饱和单品的面积与色相
    outfit.colors.n_metal / metal_hue_span                          金属单品计数与色相跨度
    outfit.colors.bottom_accent_hue_dist / bottom_accent_slot       下装与包/配饰的呼应距离
    outfit.style.forbidden_overlap_count / forbidden_tags           画像禁区交集
    outfit.style.top_overlap_count / top_overlap_style              画像 top 风格交集
    outfit.style.explore_overlap_count / explore_tags               画像「想尝试」交集
    outfit.style.conflict_pair_count / conflict_pair_a / _b         §9.2.1 冲突对命中
    outfit.style.primary_matches_profile_top1 / primary_style       主基调与画像 top-1
    outfit.style.profile_garment_count / trend_tag_count            同源单品数、潮流元素数
    outfit.sport.count / max_area_ratio                             运动属性单品
    outfit.occasion.required_formality / expected_tags /            §9.4.1 场合档案投影
        expected_tag_overlap_count / max_formality_dev
    outfit.structure.outer_top_length_gap                           外套比内搭短的量（>0 即倒挂）
    outfit.structure.metal_item_count / has_belt                    金属单品数、腰带是否存在
    outfit.materials.dominant_family                                材质家族聚合
    garment.{slot}.color.name                                       解析器给出的中文色名（解释文案用）

为什么必须预计算：冻结契约的 ``contracts.Condition`` 只有字段-字面量比较（无 count/for_all/
any_garment 量词、无 hue_dist_* / intersects / 算术），所以「两件单品差值」「计数」「分组最大组」
只能由引擎先算成登记字段。docs/03 §7.7 明确允许这一做法，并要求这些字段可独立测量。

已知口径简化（MVP）：
  - 同一 slot 有多件单品时，``garment.{slot}.*`` 取该 slot 的第一件；聚合量也基于各 slot 首件
    （``outfit.structure.accessory_count`` 例外，按 contracts 全量计数——R-DET-001 依赖它）；
  - 颜色/材质的「面积」在缺模型输入时用 ``_DEFAULT_AREA_RATIO`` 的确定性先验，可被
    ``Features.extra.garment.{slot}.area_ratio`` 覆盖。

确定性：不含随机、时间、字典/集合迭代顺序依赖；集合型字段一律排序后输出。
"""
from __future__ import annotations

import colorsys
from typing import Any, Iterable

from ..contracts import (
    Color,
    DimensionCode,
    Features,
    Garment,
    Outfit,
    Slot,
    StyleProfile,
)
from ..styles import families_from_text, families_of

__all__ = [
    "CTX_VARS",
    "KNOWN_FIELDS",
    "SLOT_KEYS",
    "SLOT_CN",
    "FORBIDDEN_PAIRS",
    "OCCASION_PROFILE",
    "build_context",
    "color_family",
    "material_family",
]

# --------------------------------------------------------------------------
# 槽位映射（contracts.Slot 是窄枚举，docs/03 §7.2 的路径空间更宽）
# --------------------------------------------------------------------------

SLOT_KEYS: tuple[str, ...] = ("top", "bottom", "outer", "dress", "shoes", "bag", "accessory")

SLOT_CN: dict[str, str] = {
    "top": "上装",
    "bottom": "下装",
    "outer": "外套",
    "dress": "连衣裙",
    "shoes": "鞋履",
    "bag": "包",
    "accessory": "配饰",
}

# contracts.Slot → 语义 slot key（accessory 还要按品类再拆 bag / accessory）
_CONTRACT_SLOT_TO_KEY: dict[Slot, str] = {
    Slot.upper: "top",
    Slot.lower: "bottom",
    Slot.outer: "outer",
    Slot.dress: "dress",
    Slot.shoes: "shoes",
}

_BAG_KEYWORDS = ("包", "bag", "tote", "背包", "手袋", "托特", "斜挎", "单肩", "clutch")
_BELT_KEYWORDS = ("腰带", "belt", "腰封")

# 内置单品默认面积占比（无模型输入时的确定性先验；可在 Features.extra 覆盖）
_DEFAULT_AREA_RATIO: dict[str, float] = {
    "top": 0.30,
    "bottom": 0.35,
    "outer": 0.25,
    "dress": 0.50,
    "shoes": 0.12,
    "bag": 0.08,
    "accessory": 0.05,
}

# --------------------------------------------------------------------------
# 受控词表（docs/03 §7.5 / §7.6 / §9.2.1 / §9.4.1）
# --------------------------------------------------------------------------

COLOR_FAMILY_NAMES: tuple[str, ...] = (
    "red", "orange", "yellow", "yellow_green", "green", "spring_green",
    "cyan", "azure", "blue", "violet", "magenta", "rose",
)
COLOR_FAMILY_CN: dict[str, str] = {
    "neutral": "中性色", "red": "红", "orange": "橙", "yellow": "黄",
    "yellow_green": "黄绿", "green": "绿", "spring_green": "春绿",
    "cyan": "青", "azure": "天蓝", "blue": "蓝", "violet": "紫",
    "magenta": "洋红", "rose": "玫红",
}

# 细粒度材质 → 家族（docs/03 §7.6 映射表，键按长度降序匹配子串）
_MATERIAL_FAMILY_MAP: dict[str, str] = {
    "牛津纺": "cotton", "府绸": "cotton", "斜纹棉": "cotton", "棉": "cotton",
    "粗花呢": "wool", "法兰绒": "wool", "羊绒": "wool", "羊毛": "wool", "毛呢": "wool",
    "真丝": "silk", "缎面": "silk", "醋酸": "silk", "雪纺": "silk", "丝绸": "silk",
    "亚麻": "linen", "苎麻": "linen",
    "牛仔": "denim",
    "牛皮": "leather", "羊皮": "leather", "皮革": "leather", "皮质": "leather", "pu革": "leather",
    "罗纹": "knit", "针织": "knit", "毛线": "knit",
    "涤纶": "synthetic", "锦纶": "synthetic", "尼龙": "synthetic", "氨纶": "synthetic",
    "聚酯": "synthetic", "网布": "synthetic", "羽绒": "synthetic",
    "金属": "metal",
    # 英文细粒度（io-layer / fixtures 也可能直接给枚举）
    "cotton": "cotton", "wool": "wool", "cashmere": "wool", "silk": "silk",
    "satin": "silk", "linen": "linen", "denim": "denim", "leather": "leather",
    "knit": "knit", "synthetic": "synthetic", "polyester": "synthetic",
    "nylon": "synthetic", "metal": "metal", "other": "other",
}
_MATERIAL_KEYS: tuple[str, ...] = tuple(sorted(_MATERIAL_FAMILY_MAP, key=lambda k: (-len(k), k)))
_MATERIAL_FAMILIES: frozenset[str] = frozenset(_MATERIAL_FAMILY_MAP.values())

_SUMMER_KEYWORDS = ("雪纺", "真丝", "亚麻", "苎麻", "缎面", "丝")
_WINTER_KEYWORDS = ("羽绒", "羊毛", "羊绒", "毛呢", "粗花呢", "法兰绒", "绒")

_FIT_MAP: dict[str, str] = {
    "tight": "tight", "slim": "slim", "regular": "regular", "loose": "loose",
    "oversized": "oversized",
    "紧身": "tight", "修身": "slim", "合身": "regular", "常规": "regular",
    "直筒": "regular", "标准": "regular", "宽松": "loose", "廓形": "oversized",
    "落肩": "oversized", "超大": "oversized", "oversize": "oversized",
}
_LENGTH_MAP: dict[str, str] = {
    "crop": "crop", "short": "short", "regular": "regular", "long": "long", "maxi": "maxi",
    "短款": "crop", "短": "crop", "常规": "regular", "及膝": "regular", "中长": "long",
    "长款": "long", "长": "long", "过膝": "long", "及踝": "maxi", "超长": "maxi",
}
_LENGTH_RATIO: dict[str, float] = {
    "crop": 0.35, "short": 0.42, "regular": 0.50, "long": 0.62, "maxi": 0.75,
}
_PATTERN_MAP: dict[str, str] = {
    "solid": "solid", "stripe": "stripe", "check": "check", "floral": "floral",
    "graphic": "graphic", "geometric": "geometric",
    "纯色": "solid", "条纹": "stripe", "格纹": "check", "格子": "check",
    "碎花": "floral", "花卉": "floral", "印花": "floral", "图案": "graphic",
    "几何": "geometric", "波点": "geometric",
}
_WAIST_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("高腰", "high"), ("中腰", "mid"), ("低腰", "low"),
)
_SPORT_KEYWORDS = ("运动", "卫衣", "卫裤", "跑鞋", "训练", "jersey", "sport", "track")
_CLOSED_SHOE_KEYWORDS = (
    "乐福鞋", "牛津鞋", "德比鞋", "单鞋", "及踝靴", "运动鞋", "靴",
    "loafer", "oxford", "derby", "pump", "sneaker", "boot",
)
_OPEN_SHOE_KEYWORDS = ("拖鞋", "凉鞋", "穆勒", "sandal", "slipper", "mule")

# 潮流元素受控词表（R-STYLE-007 的「潮流元素数」口径：去重后的标签数）
TREND_TAGS: frozenset[str] = frozenset({
    "洛丽塔", "机能风", "街头潮流", "甜酷", "华丽", "潮流", "复古",
    "硬核工装", "解构", "金属感", "亮片",
})

# 冲突风格对（docs/03 §9.2.1，随 ruleset 冻结；双向等价）
FORBIDDEN_PAIRS: tuple[tuple[str, str], ...] = (
    ("极简", "洛丽塔"),
    ("商务正装", "运动休闲"),
    ("法式", "机能风"),
    ("甜美", "硬核工装"),
    ("通勤知性", "街头潮流"),
)

# 场合档案（docs/03 §9.4.1）
OCCASION_PROFILE: dict[str, dict[str, Any]] = {
    "business": {"required_formality": 0.70, "range": (0.58, 0.82),
                 "expected_tags": ("通勤知性", "简约", "商务")},
    "formal": {"required_formality": 0.85, "range": (0.75, 1.00),
               "expected_tags": ("正式", "优雅", "经典")},
    "date": {"required_formality": 0.45, "range": (0.30, 0.62),
             "expected_tags": ("法式", "甜酷", "浪漫")},
    "casual": {"required_formality": 0.30, "range": (0.10, 0.45),
               "expected_tags": ("休闲", "极简", "街头")},
    "sport": {"required_formality": 0.15, "range": (0.00, 0.30),
              "expected_tags": ("运动休闲", "机能")},
    "party": {"required_formality": 0.55, "range": (0.35, 0.80),
              "expected_tags": ("甜酷", "华丽", "潮流")},
    "travel": {"required_formality": 0.25, "range": (0.10, 0.45),
               "expected_tags": ("休闲", "机能", "舒适")},
    "home": {"required_formality": 0.10, "range": (0.00, 0.25),
             "expected_tags": ("居家", "舒适")},
}

# §9.7.2.1 的 ctx.* 引擎注入变量白名单（封闭集合）
CTX_VARS: tuple[str, ...] = (
    "ctx.slot_cn",
    "ctx.current_item",
    "ctx.alt_item",
    "ctx.excess_item",
    "ctx.dominant_item",
    "ctx.top_style_cn",
    "ctx.occasion_cn",
)

# §7.2 garment.{slot}.* 的字段后缀全集（也是 Features.extra garment 覆盖的合法键）
GARMENT_FIELD_SUFFIXES: tuple[str, ...] = (
    "present",
    "color.hue", "color.saturation", "color.lightness", "color.temperature",
    "color.family", "color.is_neutral", "color.confidence", "color.name",
    "fit", "length", "length_ratio", "waist", "material", "material_family",
    "pattern", "style_tags", "formality", "area_ratio", "is_sport_derived",
    "is_structured", "trend_tags", "metal", "confidence",
)

_SECTION_PREFIX: dict[str, str] = {
    "outfit": "outfit.",
    "colors": "outfit.colors.",
    "materials": "outfit.materials.",
    "structure": "outfit.structure.",
    "style": "outfit.style.",
    "occasion": "outfit.occasion.",
    "sport": "outfit.sport.",
    "parse": "outfit.parse.",
    "derived": "derived.",
    "profile": "profile.",
}


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------


def _norm(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


def _r(value: float, nd: int = 4) -> float:
    return round(float(value), nd)


def _first(items: Iterable[Garment]) -> Garment | None:
    return next(iter(items), None)


def _hue_dist(left: float | None, right: float | None) -> float | None:
    """环形色相差（§6.2.2 hue_dist 语义）：min(|a−b|, 360−|a−b|)。"""
    if left is None or right is None:
        return None
    diff = abs(float(left) - float(right)) % 360.0
    return _r(min(diff, 360.0 - diff), 2)


def _contains_any_keyword(text: str, keywords: Iterable[str]) -> bool:
    lowered = _norm(text)
    return any(keyword.lower() in lowered for keyword in keywords)


def color_family(color: Color) -> str:
    """docs/03 §7.5 的确定性 color_family（不做 neutral 三档细分，全部归 "neutral"）。

    契约 ``Color.family`` 若已被上游填好（如 io-layer 的 neutral_black），优先采纳并归一。
    """
    provided = _norm(color.family)
    if provided.startswith("neutral"):
        return "neutral"
    if provided in COLOR_FAMILY_NAMES:
        return provided
    if color.saturation <= 0.20 or color.lightness >= 0.88 or color.lightness <= 0.12:
        return "neutral"
    bucket = int(((color.hue + 15.0) % 360.0) // 30.0)
    return COLOR_FAMILY_NAMES[max(0, min(11, bucket))]


def material_family(material: str | None) -> str:
    """docs/03 §7.6 的细粒度材质 → 材质家族。"""
    text = _norm(material)
    if not text:
        return "other"
    for key in _MATERIAL_KEYS:
        if key in text:
            return _MATERIAL_FAMILY_MAP[key]
    return "other"


def _map_fit(value: Any) -> str | None:
    text = _norm(value)
    if not text:
        return None
    if text in _FIT_MAP:
        return _FIT_MAP[text]
    for key in sorted(_FIT_MAP, key=lambda k: (-len(k), k)):
        if key in text:
            return _FIT_MAP[key]
    return None


def _map_length(value: Any) -> str | None:
    text = _norm(value)
    if not text:
        return None
    if text in _LENGTH_MAP:
        return _LENGTH_MAP[text]
    for key in sorted(_LENGTH_MAP, key=lambda k: (-len(k), k)):
        if key in text:
            return _LENGTH_MAP[key]
    return None


def _map_pattern(value: Any) -> str:
    text = _norm(value)
    if not text:
        return "solid"
    if text in _PATTERN_MAP:
        return _PATTERN_MAP[text]
    for key in sorted(_PATTERN_MAP, key=lambda k: (-len(k), k)):
        if key in text:
            return _PATTERN_MAP[key]
    return text


def _map_waist(garment: Garment | None, override: Any = None) -> str:
    if override is not None:
        return str(override)
    if garment is None:
        return "na"
    text = _norm(garment.category) + " " + _norm(garment.length) + " " + _norm(garment.silhouette)
    for keyword, value in _WAIST_KEYWORDS:
        if keyword in text:
            return value
    return "na"


def _is_bag(garment: Garment) -> bool:
    return _contains_any_keyword(garment.category, _BAG_KEYWORDS) or _contains_any_keyword(
        garment.material, _BAG_KEYWORDS)


def _is_belt(garment: Garment) -> bool:
    return _contains_any_keyword(garment.category, _BELT_KEYWORDS)


def _is_sport(garment: Garment) -> bool:
    text = f"{garment.category} {garment.style_tags} {garment.material} {garment.silhouette}"
    return _contains_any_keyword(text, _SPORT_KEYWORDS)


def _is_metal(garment: Garment, family: str, color_txt: str) -> bool:
    if family == "metal":
        return True
    if _contains_any_keyword(garment.material, ("金属", "metal")):
        return True
    if _contains_any_keyword(garment.category, ("金属", "金属感")):
        return True
    return "金属" in color_txt


def _is_summer(fields: dict[str, Any]) -> bool:
    if fields["material_family"] in {"linen", "silk"}:
        return True
    return _contains_any_keyword(fields["material"], _SUMMER_KEYWORDS)


def _is_winter(fields: dict[str, Any]) -> bool:
    if fields["material_family"] == "wool":
        return True
    return _contains_any_keyword(fields["material"], _WINTER_KEYWORDS)


# --------------------------------------------------------------------------
# 单品级上下文
# --------------------------------------------------------------------------


def _empty_garment_ctx() -> dict[str, Any]:
    return {
        "present": False,
        "color.hue": None,
        "color.saturation": None,
        "color.lightness": None,
        "color.temperature": "neutral",
        "color.family": "",
        "color.is_neutral": False,
        "color.confidence": 0.0,
        "color.name": "",
        "fit": None,
        "length": None,
        "length_ratio": None,
        "waist": "na",
        "material": "",
        "material_family": "other",
        "pattern": "solid",
        "style_tags": [],
        "formality": None,
        "area_ratio": 0.0,
        "is_sport_derived": False,
        "is_structured": False,
        "trend_tags": [],
        "metal": False,
        "confidence": 0.0,
    }


def _garment_ctx(
    slot_key: str,
    garment: Garment | None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """单个单品的字段字典（含 Features.extra 覆盖；覆盖键必须是已登记后缀）。"""
    fields = _empty_garment_ctx()
    if garment is None:
        fields["waist"] = "na"
    else:
        color = garment.primary_color
        family = ""
        if color is not None:
            family = color_family(color)
            saturation = _r(color.saturation)
            lightness = _r(color.lightness)
            hue = _r(color.hue, 2)
            if family == "neutral":
                temperature = "neutral"
            elif hue < 60.0 or hue >= 300.0:
                temperature = "warm"
            else:
                temperature = "cool"
            fields.update({
                "present": True,
                "color.hue": hue,
                "color.saturation": saturation,
                "color.lightness": lightness,
                "color.temperature": temperature,
                "color.family": family,
                "color.is_neutral": family == "neutral",
                "color.confidence": _r(color.confidence),
                "color.name": color.name or "",
            })
            color_txt = f"{color.hex} {color.name} {color.family}"
        else:
            color_txt = ""
            fields["present"] = True

        fit = _map_fit(garment.fit) or _map_fit(garment.silhouette)
        length = _map_length(garment.length)
        fam = material_family(garment.material)
        fields.update({
            "fit": fit,
            "length": length,
            "length_ratio": _LENGTH_RATIO.get(length) if length else None,
            "waist": _map_waist(garment),
            "material": garment.material or "",
            "material_family": fam,
            "pattern": _map_pattern(garment.pattern),
            "style_tags": sorted(str(tag) for tag in garment.style_tags),
            # 细粒度风格线索 / 图案细节 / 商家标题（docs/10 F-17）：
            # 不进向量维度，但能补足 style_tags 表达不了的信号与证据。
            "style_words": [str(w) for w in (garment.style_words or [])],
            "pattern_detail": str(garment.pattern_detail or ""),
            "merchant_title": str(garment.merchant_title or ""),
            "formality": _r((garment.formality - 1) / 4.0) if garment.formality is not None else None,
            "area_ratio": _DEFAULT_AREA_RATIO.get(slot_key, 0.0),
            "is_sport_derived": _is_sport(garment),
            "is_structured": slot_key in {"outer", "bag"} or _contains_any_keyword(
                garment.category, ("西装", "大衣", "风衣", "夹克", "结构", "blazer", "coat")),
            "trend_tags": [],
            "metal": _is_metal(garment, fam, color_txt),
            "confidence": _r(garment.confidence),
        })

    for key, value in (overrides or {}).items():
        if key not in GARMENT_FIELD_SUFFIXES:
            raise ValueError(
                f"Features.extra garment 覆盖键非法: {slot_key}.{key}；"
                f"允许: {sorted(GARMENT_FIELD_SUFFIXES)}")
        fields[key] = value
    return fields


def _split_garments(outfit: Outfit) -> dict[str, Garment | None]:
    by_slot: dict[Slot, list[Garment]] = {slot: [] for slot in Slot}
    for garment in outfit.items:
        by_slot[garment.slot].append(garment)
    accessory_items = by_slot[Slot.accessory]
    bags = [g for g in accessory_items if _is_bag(g)]
    accessories = [g for g in accessory_items if not _is_bag(g)]
    return {
        "top": _first(by_slot[Slot.upper]),
        "bottom": _first(by_slot[Slot.lower]),
        "outer": _first(by_slot[Slot.outer]),
        "dress": _first(by_slot[Slot.dress]),
        "shoes": _first(by_slot[Slot.shoes]),
        "bag": bags[0] if bags else None,
        "accessory": accessories[0] if accessories else None,
    }


# --------------------------------------------------------------------------
# 套装级聚合
# --------------------------------------------------------------------------


def _color_section(gf: dict[str, dict[str, Any]]) -> dict[str, Any]:
    present = [gf[key] for key in SLOT_KEYS if gf[key]["present"]]
    colored = [(key, gf[key]) for key in SLOT_KEYS
               if gf[key]["present"] and gf[key]["color.hue"] is not None]
    colored_area = sum(float(fields["area_ratio"] or 0.0) for _, fields in colored) or 1.0

    family_area: dict[str, float] = {}
    family_max_sat: dict[str, float] = {}
    for _, fields in colored:
        family = fields["color.family"]
        family_area[family] = family_area.get(family, 0.0) + float(fields["area_ratio"] or 0.0)
        family_max_sat[family] = max(family_max_sat.get(family, 0.0),
                                     float(fields["color.saturation"] or 0.0))
    main_families = sorted(family_area, key=lambda name: (-_r(family_area[name], 6), name))

    # 色相跨度只算**有彩色**：黑色/白色/灰色没有色相，把它们算进去会让
    # "亮绿 + 黑" 变成 168° 撞色（实测 R-COLOR-009 因此误扣 2 分，docs/10 F-17）。
    chromatic = [fields for _, fields in colored
                 if float(fields["color.saturation"] or 0.0) >= 0.15]
    hue_source = chromatic or [fields for _, fields in colored]
    hues = [float(fields["color.hue"]) for fields in hue_source]
    hue_span = 0.0
    for i in range(len(hues)):
        for j in range(i + 1, len(hues)):
            dist = _hue_dist(hues[i], hues[j])
            hue_span = max(hue_span, dist or 0.0)

    light = [float(fields["color.lightness"]) for _, fields in colored]
    saturations = [float(fields["color.saturation"]) for _, fields in colored]
    max_saturation = max(saturations) if saturations else 0.0
    mean_saturation = (
        sum(float(fields["color.saturation"]) * float(fields["area_ratio"] or 0.0)
            for _, fields in colored) / colored_area
    ) if colored else 0.0

    warm_area = sum(float(fields["area_ratio"] or 0.0) for _, fields in colored
                    if fields["color.temperature"] == "warm")
    cool_area = sum(float(fields["area_ratio"] or 0.0) for _, fields in colored
                    if fields["color.temperature"] == "cool")
    warm_ratio = _r(warm_area / colored_area)
    cool_ratio = _r(cool_area / colored_area)
    neutral_ratio = _r(max(0.0, 1.0 - warm_ratio - cool_ratio))
    if warm_ratio > 0.25 and cool_ratio > 0.25:
        temperature_mix = "conflicting" if neutral_ratio <= 0.25 else "balanced"
    elif warm_ratio > 0.10 and cool_ratio <= 0.10:
        temperature_mix = "warm_only"
    elif cool_ratio > 0.10 and warm_ratio <= 0.10:
        temperature_mix = "cool_only"
    else:
        temperature_mix = "balanced"

    # 图案（solid 不计入 n_patterns，见 §7.1）
    pattern_types = sorted({fields["pattern"] for fields in present
                            if fields["pattern"] and fields["pattern"] != "solid"})

    # 金属
    metal_items = [(key, gf[key]) for key in SLOT_KEYS
                   if gf[key]["present"] and gf[key]["metal"] and gf[key]["color.hue"] is not None]
    metal_hues = sorted(float(fields["color.hue"]) for _, fields in metal_items)
    metal_hue_span = 0.0
    for i in range(len(metal_hues)):
        for j in range(i + 1, len(metal_hues)):
            metal_hue_span = max(metal_hue_span, _hue_dist(metal_hues[i], metal_hues[j]) or 0.0)

    top, bottom = gf["top"], gf["bottom"]
    top_bottom_hue_dist = (
        _hue_dist(top["color.hue"], bottom["color.hue"])
        if top["present"] and bottom["present"] else None
    )
    top_bottom_lightness_diff = (
        _r(abs(float(top["color.lightness"]) - float(bottom["color.lightness"])))
        if top["present"] and bottom["present"]
        and top["color.lightness"] is not None and bottom["color.lightness"] is not None else None
    )

    # 最高饱和度的那件（并列：面积大者 → 槽位顺序）
    max_sat_item: tuple[str, dict[str, Any]] | None = None
    for key in SLOT_KEYS:
        fields = gf[key]
        if not fields["present"] or fields["color.hue"] is None:
            continue
        if max_sat_item is None:
            max_sat_item = (key, fields)
            continue
        current_sat = float(max_sat_item[1]["color.saturation"])
        candidate_sat = float(fields["color.saturation"])
        if (candidate_sat, float(fields["area_ratio"] or 0.0)) > (
                current_sat, float(max_sat_item[1]["area_ratio"] or 0.0)):
            max_sat_item = (key, fields)
    max_sat_area_ratio = float(max_sat_item[1]["area_ratio"] or 0.0) if max_sat_item else 0.0
    max_sat_hue = float(max_sat_item[1]["color.hue"]) if max_sat_item else None

    # 同家族分组（§7.7 n_same_family_max / luminance_monotonic）
    groups: dict[str, list[tuple[str, float]]] = {}
    for key, fields in colored:
        groups.setdefault(fields["color.family"], []).append(
            (key, float(fields["color.lightness"])))
    n_same_family_max = max((len(members) for members in groups.values()), default=0)
    monotonic = False
    if n_same_family_max >= 3:
        family = sorted(
            (name for name, members in groups.items() if len(members) == n_same_family_max),
            key=lambda name: name,
        )[0]
        sequence = sorted(groups[family], key=lambda pair: (pair[1], SLOT_KEYS.index(pair[0])))
        lightness_seq = [item[1] for item in sequence]
        monotonic = all(
            lightness_seq[i + 1] - lightness_seq[i] >= 0.08 - 1e-9
            for i in range(len(lightness_seq) - 1))

    dominant_family = main_families[0] if main_families else ""
    non_neutral = [name for name in main_families if name != "neutral"]
    smallest_family = min(non_neutral, key=lambda name: (_r(family_area[name], 6), name)) \
        if non_neutral else ""

    # 下装与包/配饰的呼应
    accent_dist: float | None = None
    accent_slot = ""
    if bottom["present"] and bottom["color.hue"] is not None:
        for key in ("bag", "accessory"):
            fields = gf[key]
            if fields["present"] and fields["color.hue"] is not None:
                dist = _hue_dist(bottom["color.hue"], fields["color.hue"])
                if dist is not None and (accent_dist is None or dist < accent_dist):
                    accent_dist, accent_slot = dist, SLOT_CN[key]

    return {
        "outfit.colors.main_colors": [COLOR_FAMILY_CN[name] for name in main_families],
        "outfit.colors.n_main_colors": len(main_families),
        "outfit.colors.n_chromatic": sum(1 for name in family_area if family_max_sat[name] > 0.20),
        "outfit.colors.hue_span": _r(hue_span, 2),
        "outfit.colors.max_saturation": _r(max_saturation),
        "outfit.colors.mean_saturation": _r(mean_saturation),
        "outfit.colors.lightness_range": _r(max(light) - min(light)) if len(light) >= 2 else 0.0,
        "outfit.colors.has_neutral_anchor": "neutral" in family_area,
        "outfit.colors.temperature_mix": temperature_mix,
        "outfit.colors.warm_area_ratio": warm_ratio,
        "outfit.colors.cool_area_ratio": cool_ratio,
        "outfit.colors.neutral_area_ratio": neutral_ratio,
        "outfit.colors.pattern_types": pattern_types,
        "outfit.colors.n_patterns": len(pattern_types),
        "outfit.colors.n_metal": len(metal_items),
        "outfit.colors.metal_hues": metal_hues,
        "outfit.colors.metal_hue_span": _r(metal_hue_span, 2),
        "outfit.colors.top_bottom_hue_dist": top_bottom_hue_dist,
        "outfit.colors.top_bottom_lightness_diff": top_bottom_lightness_diff,
        "outfit.colors.max_sat_area_ratio": _r(max_sat_area_ratio),
        "outfit.colors.max_sat_hue": max_sat_hue,
        "outfit.colors.n_same_family_max": n_same_family_max,
        "outfit.colors.luminance_monotonic": monotonic,
        "outfit.colors.dominant_family": dominant_family,
        "outfit.colors.smallest_family": smallest_family,
        "outfit.colors.bottom_accent_hue_dist": accent_dist,
        "outfit.colors.bottom_accent_slot": accent_slot,
    }


def _material_section(gf: dict[str, dict[str, Any]]) -> dict[str, Any]:
    present = [gf[key] for key in SLOT_KEYS if gf[key]["present"]]
    families = sorted({fields["material_family"] for fields in present})
    grouped: dict[str, list[str]] = {}
    for key in SLOT_KEYS:
        fields = gf[key]
        if fields["present"] and fields["material_family"] != "other":
            grouped.setdefault(fields["material_family"], []).append(key)
    if grouped:
        dominant = sorted(grouped, key=lambda name: (-len(grouped[name]), name))[0]
        max_family_count = len(grouped[dominant])
        family_slot_count = len(set(grouped[dominant]))
    else:
        dominant, max_family_count, family_slot_count = "", 0, 0
    return {
        "outfit.materials.families": families,
        "outfit.materials.n_families": len(families),
        "outfit.materials.has_summer_fabric": any(_is_summer(fields) for fields in present),
        "outfit.materials.has_winter_item": any(_is_winter(fields) for fields in present),
        "outfit.materials.max_family_count": max_family_count,
        "outfit.materials.family_slot_count": family_slot_count,
        "outfit.materials.dominant_family": dominant,
    }


def _structure_section(
    outfit: Outfit,
    gf: dict[str, dict[str, Any]],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    shoes = gf["shoes"]
    has_outer = gf["outer"]["present"]
    has_shoes = shoes["present"]

    closed = False
    if has_shoes:
        shoes_category = ""
        for garment in outfit.items:
            if garment.slot == Slot.shoes:
                shoes_category = garment.category
                break
        category_text = _norm(shoes_category)
        if _contains_any_keyword(category_text, _OPEN_SHOE_KEYWORDS):
            closed = False
        elif _contains_any_keyword(category_text, _CLOSED_SHOE_KEYWORDS):
            closed = True
        else:
            closed = bool(shoes["material_family"] == "leather" and shoes["fit"] not in {"loose", "oversized"})

    layer_depth = int((1 if (gf["top"]["present"] or gf["dress"]["present"]) else 0)
                      + (1 if has_outer else 0))
    top_ratio, outer_ratio = gf["top"]["length_ratio"], gf["outer"]["length_ratio"]
    gap = None
    if top_ratio is not None and outer_ratio is not None:
        gap = _r(float(top_ratio) - float(outer_ratio))

    return {
        "outfit.structure.has_outer": has_outer,
        "outfit.structure.has_shoes": has_shoes,
        "outfit.structure.has_closed_shoes": closed,
        "outfit.structure.accessory_count": sum(
            1 for garment in outfit.items if garment.slot == Slot.accessory),
        "outfit.structure.layer_depth": layer_depth,
        "outfit.structure.visual_waist_y": overrides.get("visual_waist_y"),
        "outfit.structure.visual_center_y": overrides.get("visual_center_y"),
        "outfit.structure.is_tucked": bool(overrides.get("is_tucked", False)),
        "outfit.structure.hem_pooling": bool(overrides.get("hem_pooling", False)),
        "outfit.structure.outer_top_length_gap": gap,
        "outfit.structure.metal_item_count": sum(
            1 for key in SLOT_KEYS if gf[key]["present"] and gf[key]["metal"]),
        "outfit.structure.has_belt": any(_is_belt(garment) for garment in outfit.items),
    }


def _style_section(
    gf: dict[str, dict[str, Any]],
    profile_ctx: dict[str, Any],
) -> dict[str, Any]:
    votes: dict[str, list[float]] = {}
    for key in SLOT_KEYS:
        fields = gf[key]
        if not fields["present"]:
            continue
        area = float(fields["area_ratio"] or 0.0)
        for tag in sorted(set(fields["style_tags"])):
            entry = votes.setdefault(tag, [0.0, 0.0])
            entry[0] += 1.0
            entry[1] += area
    style_tags = [
        tag for tag, (count, area) in sorted(votes.items(), key=lambda kv: (-kv[1][0], kv[0]))
        if count >= 2 or area >= 0.30 - 1e-9
    ]

    # 同源度只算**主体单品**（上/下/外套/连衣裙/鞋）：
    # 包和饰品天生是"中性连接件"，一件棕色托特包不该让整套被判成风格分裂。
    # 实测：Y2K 波点套装配棕色通勤包 → 3 件里 1 件配饰不同源 → 直接被扣 4 分（F-17）。
    MAIN_SLOTS = ("top", "bottom", "outer", "dress", "shoes")
    tagged: list[set[str]] = []
    for key in MAIN_SLOTS:
        fields = gf[key]
        if fields["present"] and fields["style_tags"]:
            tagged.append(set(fields["style_tags"]))
    jaccard_min = 1.0
    for i in range(len(tagged)):
        for j in range(i + 1, len(tagged)):
            union = tagged[i] | tagged[j]
            if union:
                jaccard_min = min(jaccard_min, len(tagged[i] & tagged[j]) / len(union))

    tag_set = set(style_tags)
    conflicts = [(a, b) for a, b in FORBIDDEN_PAIRS if a in tag_set and b in tag_set]

    top_styles = list(profile_ctx["profile.top_styles"])
    forbidden = set(profile_ctx["profile.forbidden_styles"])
    explore = set(profile_ctx["profile.explore_tags"])

    expected_profile_garments = 0
    for key in SLOT_KEYS:
        fields = gf[key]
        if fields["present"] and set(fields["style_tags"]) & set(top_styles):
            expected_profile_garments += 1

    trend_tags = sorted({tag for key in SLOT_KEYS if gf[key]["present"]
                         for tag in gf[key]["trend_tags"] if tag in TREND_TAGS})

    top_overlap_style = ""
    for style in top_styles:
        if style in tag_set:
            top_overlap_style = style
            break

    # ---- 家族级重合（docs/10 F-17）----
    # 精确字符串比对会把「同一风格的不同说法」罚成「风格冲突」：
    # 画像 top 是 Y2K/运动休闲，搭配标签是 日系少女/居家/学院风 → overlap=0 → -9 分。
    # 家族层回答的是"是不是同一路风格"，字符串层回答"是不是同一个词"。
    outfit_families = families_of(style_tags)
    profile_families = families_of(top_styles)
    family_overlap = sorted(outfit_families & profile_families)

    fam_sets = [families_of(gf[key]["style_tags"]) for key in MAIN_SLOTS
                if gf[key]["present"] and gf[key]["style_tags"]]
    family_jaccard_min = 1.0
    for i in range(len(fam_sets)):
        for j in range(i + 1, len(fam_sets)):
            union = fam_sets[i] | fam_sets[j]
            if union:
                family_jaccard_min = min(family_jaccard_min,
                                         len(fam_sets[i] & fam_sets[j]) / len(union))

    # 商家标题 / 细粒度风格词 / 图案细节 里的家族补充：
    # 模型常把 Y2K 写成"日系少女"，但 merchant_title 往往写着"Y2K辣妹…"
    text_families: set[str] = set()
    for key in SLOT_KEYS:
        fields = gf[key]
        if not fields["present"]:
            continue
        text_families |= families_from_text(fields.get("merchant_title"))
        text_families |= families_from_text(" ".join(fields.get("style_words") or []))
        text_families |= families_from_text(fields.get("pattern_detail"))
    all_outfit_families = outfit_families | text_families
    family_overlap_all = sorted(all_outfit_families & profile_families)

    return {
        "outfit.style_tags": style_tags,
        "outfit.style_tag_jaccard_min": _r(jaccard_min),
        "outfit.style.families": sorted(all_outfit_families),
        "outfit.style.family_overlap_count": len(family_overlap_all),
        "outfit.style.family_overlap": family_overlap_all,
        "outfit.style.family_jaccard_min": _r(family_jaccard_min),
        # 同源度的宽口径：词面 jaccard 与家族 jaccard 取大者。
        # 只认字符串会让同义词被判成冲突；只认家族会让所有休闲系都算同源。
        "outfit.style.same_source_jaccard": _r(max(jaccard_min, family_jaccard_min)),
        "outfit.style.style_words": sorted({w for key in SLOT_KEYS
                                            if gf[key]["present"]
                                            for w in gf[key].get("style_words") or []}),
        "outfit.style.merchant_titles": [gf[key]["merchant_title"] for key in SLOT_KEYS
                                         if gf[key]["present"] and gf[key]["merchant_title"]],
        "outfit.style.primary_style": style_tags[0] if style_tags else "",
        "outfit.style.primary_matches_profile_top1": bool(
            style_tags and top_styles and style_tags[0] == top_styles[0]),
        "outfit.style.forbidden_overlap_count": len(tag_set & forbidden),
        "outfit.style.forbidden_tags": sorted(tag_set & forbidden),
        "outfit.style.top_overlap_count": len(tag_set & set(top_styles)),
        "outfit.style.top_overlap_style": top_overlap_style,
        "outfit.style.explore_overlap_count": len(tag_set & explore),
        "outfit.style.explore_tags": sorted(tag_set & explore),
        "outfit.style.conflict_pair_count": len(conflicts),
        "outfit.style.conflict_pair_a": conflicts[0][0] if conflicts else "",
        "outfit.style.conflict_pair_b": conflicts[0][1] if conflicts else "",
        "outfit.style.profile_garment_count": expected_profile_garments,
        "outfit.style.trend_tag_count": len(trend_tags),
    }


def _occasion_section(
    outfit_ctx: dict[str, Any],
    gf: dict[str, dict[str, Any]],
    style_tags: list[str],
) -> dict[str, Any]:
    occasion = str(outfit_ctx["outfit.occasion"])
    profile = OCCASION_PROFILE.get(occasion)
    required = profile["required_formality"] if profile else None
    expected_tags = list(profile["expected_tags"]) if profile else []

    formalities = [float(gf[key]["formality"]) for key in SLOT_KEYS
                   if gf[key]["present"] and gf[key]["formality"] is not None]
    max_dev = None
    if required is not None and formalities:
        max_dev = _r(max(abs(value - required) for value in formalities))

    return {
        "outfit.occasion.required_formality": required,
        "outfit.occasion.expected_tags": expected_tags,
        "outfit.occasion.expected_tag_overlap_count": len(set(style_tags) & set(expected_tags)),
        "outfit.occasion.max_formality_dev": max_dev,
    }


def _sport_section(outfit: Outfit, gf: dict[str, dict[str, Any]]) -> dict[str, Any]:
    items = [gf[key] for key in SLOT_KEYS if gf[key]["present"] and gf[key]["is_sport_derived"]]
    return {
        "outfit.sport.count": len(items),
        "outfit.sport.max_area_ratio": _r(max((float(f["area_ratio"] or 0.0) for f in items), default=0.0)),
    }


# --------------------------------------------------------------------------
# 上下文入口
# --------------------------------------------------------------------------


def _patch_section(ctx: dict[str, Any], extra: dict[str, Any], section: str) -> None:
    block = extra.get(section)
    if block is None:
        return
    if not isinstance(block, dict):
        raise ValueError(f"Features.extra[{section!r}] 必须是 dict")
    prefix = _SECTION_PREFIX[section]
    for key, value in block.items():
        ctx[f"{prefix}{key}"] = value


def build_context(
    outfit: Outfit,
    profile: StyleProfile,
    features: Features,
) -> dict[str, Any]:
    """构建规则求值上下文（扁平点号键；全量字段恒定存在，缺证据时为 None/空值）。"""
    extra = dict(features.model_extra or {})
    garment_overrides = extra.get("garment") or {}
    structure_overrides = extra.get("structure") or {}
    if not isinstance(garment_overrides, dict):
        raise ValueError("Features.extra['garment'] 必须是 dict")
    for slot_key in garment_overrides:
        if slot_key not in SLOT_KEYS:
            raise ValueError(f"Features.extra['garment'] 含未知槽位: {slot_key!r}")

    # ---- 0. 画布与单品 ----
    ctx: dict[str, Any] = {}
    picked = _split_garments(outfit)
    gf = {key: _garment_ctx(key, picked[key], garment_overrides.get(key))
          for key in SLOT_KEYS}
    for key in SLOT_KEYS:
        for suffix, value in gf[key].items():
            ctx[f"garment.{key}.{suffix}"] = value

    # ---- 1. 画像（style 段依赖 top_styles / forbidden / explore）----
    profile_ctx: dict[str, Any] = {
        "profile.top_styles": [tag.tag for tag in profile.tags],
        "profile.forbidden_styles": sorted(str(tag) for tag in profile.banned_styles),
        "profile.explore_tags": sorted(str(tag) for tag in (extra.get("profile") or {}).get(
            "explore_tags", []) or []),
        "profile.reference_count": int(profile.n_ref_outfits),
        "profile.style_entropy": _r((extra.get("profile") or {}).get("style_entropy", 0.0)),
        "profile.confidence": _r(profile.confidence),
        "profile.preferred_formality": profile.preferred_formality,
    }
    ctx.update(profile_ctx)
    _patch_section(ctx, extra, "profile")
    profile_ctx = {key: ctx[key] for key in profile_ctx}

    # ---- 2. 套装基础量 ----
    occasion = _norm(outfit.occasion) or "unspecified"
    season = _norm(outfit.season) or "unknown"
    slot_mask = 0
    bits = {"top": 0, "bottom": 1, "outer": 2, "dress": 3, "shoes": 4, "bag": 5, "accessory": 6}
    for key in SLOT_KEYS:
        if gf[key]["present"]:
            slot_mask |= 1 << bits[key]
    formalities = [float(gf[key]["formality"]) for key in SLOT_KEYS
                   if gf[key]["present"] and gf[key]["formality"] is not None]
    areas = [float(gf[key]["area_ratio"] or 0.0) for key in SLOT_KEYS if gf[key]["present"]]
    total_area = sum(areas)
    formality = (
        _r(sum(float(gf[key]["formality"]) * float(gf[key]["area_ratio"] or 0.0)
               for key in SLOT_KEYS
               if gf[key]["present"] and gf[key]["formality"] is not None) / total_area)
        if formalities and total_area > 0 else (None if not formalities else _r(sum(formalities) / len(formalities)))
    )
    formality_span = _r(max(formalities) - min(formalities)) if len(formalities) >= 2 else (
        0.0 if formalities else None)
    ctx.update({
        "outfit.garment_count": len(outfit.items),
        "outfit.slot_mask": slot_mask,
        "outfit.occasion": occasion,
        "outfit.season": season,
        "outfit.formality": formality,
        "outfit.formality_span": formality_span,
        "outfit.parse.confidence": _r(outfit.confidence),
        "outfit.parse.unknown_fields": sorted(outfit.parse_warnings),
    })

    # ---- 3. 颜色 / 材质 / 结构（§7.1 + §7.7）----
    ctx.update(_color_section(gf))
    ctx.update(_material_section(gf))
    ctx.update(_structure_section(outfit, gf, structure_overrides))
    _patch_section(ctx, extra, "outfit")
    _patch_section(ctx, extra, "colors")
    _patch_section(ctx, extra, "materials")
    _patch_section(ctx, extra, "structure")
    _patch_section(ctx, extra, "parse")

    # ---- 4. 风格（§9.2 的画像/风格交集量）----
    ctx.update(_style_section(gf, profile_ctx))
    _patch_section(ctx, extra, "style")

    # ---- 5. 场合（§9.4）----
    ctx.update(_occasion_section(ctx, gf, list(ctx["outfit.style_tags"])))
    _patch_section(ctx, extra, "occasion")

    # ---- 6. 运动属性（§9.4 R-OCC-001/006）----
    ctx.update(_sport_section(outfit, gf))
    _patch_section(ctx, extra, "sport")

    # ---- 7. 模型连续输出（§7.4）----
    center_y = ctx.get("outfit.structure.visual_center_y")
    required = ctx.get("outfit.occasion.required_formality")
    outfit_formality = ctx.get("outfit.formality")
    cosine = _r(features.style_cosine)
    derived: dict[str, Any] = {
        "derived.cosine_uv": cosine,
        "derived.occasion_fit_prob": (
            _r(extra["occasion_fit_prob"]) if extra.get("occasion_fit_prob") is not None else None),
        "derived.visual_center_offset": (
            _r(abs(float(center_y) - 0.5)) if center_y is not None else None),
        "derived.formality_gap": (
            _r(float(outfit_formality) - float(required))
            if outfit_formality is not None and required is not None else None),
    }
    ctx.update(derived)
    _patch_section(ctx, extra, "derived")

    # ---- 8. 维度证据可用度（§4.8；engine 门控读取）----
    for code in DimensionCode:
        ctx[f"assessability.{code.value}"] = _r(features.assess(code))

    # ---- 9. ctx.* 注入变量（§9.7.2.1 白名单）----
    ctx.update(_ctx_vars(ctx))
    return ctx


def _item_label(gf: dict[str, dict[str, Any]], key: str) -> str:
    """单品可读名：解析给出的中文色名 + 槽位名（拿不到色名时退回颜色家族中文名）。"""
    fields = gf[key]
    if not fields["present"]:
        return ""
    name = str(fields.get("color.name") or "").strip()
    if name:
        return f"{name}{SLOT_CN[key]}"
    family = str(fields.get("color.family") or "")
    if family and family != "neutral":
        return f"{COLOR_FAMILY_CN.get(family, family)}{SLOT_CN[key]}"
    return SLOT_CN[key]


def _ctx_vars(ctx: dict[str, Any]) -> dict[str, Any]:
    """§9.7.2.1 白名单变量的确定性填充（不含任何价值判断）。"""
    gf = {key: {suffix: ctx[f"garment.{key}.{suffix}"] for suffix in GARMENT_FIELD_SUFFIXES}
          for key in SLOT_KEYS}
    present = [key for key in SLOT_KEYS if gf[key]["present"]]
    dominant = max(
        present,
        key=lambda key: (float(gf[key]["area_ratio"] or 0.0), -SLOT_KEYS.index(key)),
    ) if present else "top"
    excess = min(
        present,
        key=lambda key: (float(gf[key]["area_ratio"] or 0.0), SLOT_KEYS.index(key)),
    ) if present else dominant
    top_styles = list(ctx.get("profile.top_styles") or [])
    return {
        "ctx.slot_cn": SLOT_CN[dominant],
        "ctx.current_item": _item_label(gf, dominant),
        "ctx.alt_item": _item_label(gf, excess),
        "ctx.excess_item": _item_label(gf, excess),
        "ctx.dominant_item": _item_label(gf, dominant),
        "ctx.top_style_cn": top_styles[0] if top_styles else "",
        "ctx.occasion_cn": _occasion_cn(str(ctx.get("outfit.occasion") or "unspecified")),
    }


def _occasion_cn(value: str) -> str:
    return {
        "business": "通勤", "formal": "正式", "date": "约会", "casual": "休闲",
        "sport": "运动", "party": "聚会", "travel": "出行", "home": "居家",
        "unspecified": "未指定",
    }.get(value, value)


# --------------------------------------------------------------------------
# 登记字段全集（loader 的 DSL_UNKNOWN_FIELD lint 用；由 probe 反推保证一致）
# --------------------------------------------------------------------------


def _probe() -> dict[str, Any]:
    return build_context(
        Outfit(outfit_id="__probe__"),
        StyleProfile(profile_id="__probe__"),
        Features(),
    )


KNOWN_FIELDS: frozenset[str] = frozenset(_probe()) | frozenset(CTX_VARS)
