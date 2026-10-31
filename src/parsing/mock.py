"""离线 mock 解析器（无 API key / OFFLINE_MODE 时的默认实现）。

设计目标（task-5 硬要求）：
    1. **确定性**：同一 ``image_ref`` 永远得到同一套 ``Outfit``——
       seed 由 sha256 派生（不用内建 ``hash()``，它受 PYTHONHASHSEED 随机化），
       因此**跨进程、跨运行**结果一致。
    2. **可读的显式选择**：``demo/commute-1`` / ``demo/date-2`` 这类带关键词的
       ref 会稳定命中对应"剧本"；没有关键词时按 sha256 取模选择。
    3. **覆盖多种搭配形态**：10 个剧本覆盖通勤 / 约会 / 运动 / 正式 / 休闲 /
       撞色 / 同色系 / 季节冲突 / 配饰过载 / 正式缺件，保证规则引擎能走到
       不同分支（含 hard veto 分支）。
    4. **零网络**：不读文件、不访问网络；``demo/...`` 这类纯标识可直接解析。

词表契约（与 docs/03 §7 对齐，已同步 rules-author）：
    ``fit`` ∈ tight|slim|regular|loose|oversized
    ``length`` ∈ crop|short|regular|long|maxi
    ``pattern`` ∈ solid|stripe|check|floral|graphic
    ``material`` 用中文细粒度（docs/03 §7.6 左列）
    ``style_tags`` 用中文受控词（极简 / 通勤知性 / 法式 …）
    ``Color.name`` 中文色名，``Color.family`` 用 docs/03 §7.5 家族名
    ``occasion`` / ``season`` 用 docs/04 英文枚举
"""
from __future__ import annotations

from dataclasses import dataclass

from ..contracts import Color, Garment, Outfit, Slot
from ..towers.encoder import color_family_from_hsv
from ..utils import make_id, stable_hash
from .base import BaseParser, ParseResult


# ---------------------------------------------------------------------------
# 剧本定义
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Item:
    slot: Slot
    category: str
    name: str
    hex: str
    material: str
    fit: str
    length: str
    formality: int  # contracts.Garment.formality ∈ 1..5
    style_tags: tuple[str, ...]
    pattern: str = "solid"
    silhouette: str | None = None
    confidence: float = 0.9


@dataclass(frozen=True)
class _Script:
    key: str
    aliases: tuple[str, ...]
    items: tuple[_Item, ...]


_SCRIPTS: tuple[_Script, ...] = (
    # 1) 通勤知性：中性色 + 结构单品，追求 R-STYLE-008 / R-OCC-009 等 boost
    _Script("commute_polished", ("commute", "business", "通勤", "办公"), (
        _Item(Slot.upper, "棉质衬衫", "米白", "#F0EBE1", "棉", "regular", "regular", 4,
              ("通勤知性", "极简"), confidence=0.94),
        _Item(Slot.lower, "直筒西裤", "藏青", "#1F2A44", "羊毛", "regular", "long", 4,
              ("通勤知性", "极简"), confidence=0.93),
        _Item(Slot.shoes, "乐福鞋", "黑色", "#1A1A1A", "牛皮", "regular", "regular", 4,
              ("极简",), confidence=0.92),
        _Item(Slot.accessory, "托特包", "黑色", "#1A1A1A", "牛皮", "regular", "regular", 4,
              ("极简",), confidence=0.88),
    )),
    # 2) 约会法式：偏柔 + 配饰收口（D4 交集场景词、R-DET-002 反例）
    _Script("date_french", ("date", "约会", "法式"), (
        _Item(Slot.upper, "真丝衬衫", "白色", "#FAF7F2", "真丝", "slim", "regular", 3,
              ("法式", "甜美"), confidence=0.92),
        _Item(Slot.lower, "半身裙", "黑色", "#1A1A1A", "缎面", "loose", "long", 3,
              ("法式", "浪漫"), confidence=0.9),
        _Item(Slot.shoes, "单鞋", "裸色", "#E8CDBB", "牛皮", "regular", "regular", 3,
              ("法式",), confidence=0.9),
        _Item(Slot.accessory, "金属耳饰", "金色", "#D4AF37", "金属", "regular", "regular", 3,
              ("法式", "优雅"), confidence=0.86),
    )),
    # 3) 运动休闲：运动属性单品（business/formal 场合会触发 R-OCC-001 hard）
    _Script("sport_active", ("sport", "运动", "gym", "健身"), (
        _Item(Slot.upper, "连帽卫衣", "灰色", "#808080", "棉", "oversized", "regular", 2,
              ("运动休闲", "机能风"), confidence=0.92),
        _Item(Slot.lower, "运动裤", "黑色", "#2A2A2A", "涤纶", "loose", "long", 1,
              ("运动休闲",), confidence=0.91),
        _Item(Slot.shoes, "运动鞋", "白色", "#F5F5F5", "涤纶", "regular", "regular", 1,
              ("运动休闲",), confidence=0.9),
        _Item(Slot.accessory, "棒球帽", "藏青", "#1F2A44", "棉", "regular", "regular", 1,
              ("运动休闲", "街头潮流"), confidence=0.85),
    )),
    # 4) 正式正装：多层 + 高正式度
    _Script("formal_suit", ("formal", "正式", "suit", "正装"), (
        _Item(Slot.outer, "西装外套", "黑色", "#1A1A1A", "羊毛", "regular", "regular", 5,
              ("商务正装", "正式"), confidence=0.94),
        _Item(Slot.upper, "衬衫", "白色", "#FAF7F2", "真丝", "slim", "regular", 4,
              ("商务正装", "正式"), confidence=0.93),
        _Item(Slot.lower, "西裤", "炭灰", "#3A3D42", "羊毛", "slim", "long", 5,
              ("正式", "商务正装"), confidence=0.93),
        _Item(Slot.shoes, "牛津鞋", "黑色", "#1A1A1A", "牛皮", "regular", "regular", 5,
              ("正式",), confidence=0.92),
        _Item(Slot.accessory, "金属手表", "银色", "#C0C0C0", "金属", "regular", "regular", 4,
              ("商务正装",), confidence=0.86),
    )),
    # 5) 街头休闲：图案 + 宽松叠穿（比例 / 图案 / 配饰多条规则可触发）
    _Script("casual_street", ("casual", "休闲", "street", "街头"), (
        _Item(Slot.upper, "印花T恤", "白色", "#FFFFFF", "棉", "oversized", "regular", 2,
              ("街头潮流", "潮流"), pattern="graphic", confidence=0.9),
        _Item(Slot.lower, "宽松牛仔裤", "牛仔蓝", "#4A6FA5", "牛仔", "loose", "long", 2,
              ("街头潮流", "休闲"), confidence=0.9),
        _Item(Slot.shoes, "厚底运动鞋", "白色", "#F5F5F5", "涤纶", "oversized", "regular", 1,
              ("运动休闲", "街头潮流"), confidence=0.88),
        _Item(Slot.accessory, "棒球帽", "藏青", "#1F2A44", "棉", "regular", "regular", 1,
              ("街头潮流",), confidence=0.84),
        _Item(Slot.accessory, "项链", "银色", "#C0C0C0", "金属", "regular", "regular", 2,
              ("街头潮流",), confidence=0.82),
    )),
    # 6) 高饱和撞色：R-COLOR-001 hard veto 分支（hue_span 146° / max_sat 1.0）
    _Script("color_clash", ("clash", "撞色", "color", "高饱和"), (
        _Item(Slot.upper, "短袖T恤", "玫红", "#F5006A", "棉", "regular", "regular", 2,
              ("甜美", "运动休闲"), confidence=0.93),
        _Item(Slot.lower, "工装裤", "亮绿", "#00E64D", "棉", "loose", "long", 2,
              ("运动休闲", "工装"), confidence=0.92),
        _Item(Slot.shoes, "运动鞋", "白色", "#F5F5F5", "涤纶", "regular", "regular", 1,
              ("运动休闲",), confidence=0.9),
    )),
    # 7) 同色系渐变：R-COLOR-011 / R-COLOR-012 boost 分支
    _Script("mono_gradient", ("mono", "同色", "渐变", "gradient"), (
        _Item(Slot.upper, "针织衫", "米白", "#F2EDE3", "针织", "regular", "regular", 4,
              ("极简", "通勤知性"), confidence=0.93),
        _Item(Slot.outer, "羊毛大衣", "驼色", "#C8A57B", "羊毛", "regular", "long", 4,
              ("通勤知性", "法式"), confidence=0.92),
        _Item(Slot.lower, "阔腿裤", "棕色", "#7A4A21", "羊毛", "loose", "long", 4,
              ("通勤知性", "极简"), confidence=0.92),
        _Item(Slot.shoes, "乐福鞋", "棕色", "#6B3F1D", "牛皮", "regular", "regular", 4,
              ("通勤知性",), confidence=0.9),
        _Item(Slot.accessory, "金属耳饰", "金色", "#D4AF37", "金属", "regular", "regular", 3,
              ("极简",), confidence=0.86),
    )),
    # 8) 季节材质冲突：亚麻(夏) + 羊毛(冬) → R-DET-003
    _Script("season_mismatch", ("season", "季节", "winter", "冬季", "冲突"), (
        _Item(Slot.upper, "亚麻衬衫", "米色", "#EFE7D8", "亚麻", "regular", "regular", 3,
              ("极简", "休闲"), confidence=0.9),
        _Item(Slot.lower, "阔腿裤", "深灰", "#4A4A4A", "羊毛", "loose", "long", 3,
              ("极简", "通勤知性"), confidence=0.9),
        _Item(Slot.shoes, "及踝靴", "黑色", "#1A1A1A", "牛皮", "regular", "regular", 3,
              ("极简",), confidence=0.89),
        _Item(Slot.accessory, "围巾", "红色", "#C8102E", "羊毛", "regular", "regular", 3,
              ("法式", "优雅"), confidence=0.86),
    )),
    # 9) 配饰过载：5 件配饰 → R-DET-001
    _Script("accessory_overload", ("accessory", "配饰", "overload", "party", "派对"), (
        _Item(Slot.upper, "缎面衬衫", "白色", "#FAF7F2", "缎面", "regular", "regular", 3,
              ("优雅", "浪漫"), confidence=0.92),
        _Item(Slot.lower, "半身裙", "黑色", "#1A1A1A", "醋酸", "loose", "long", 3,
              ("优雅", "法式"), confidence=0.91),
        _Item(Slot.shoes, "高跟单鞋", "裸色", "#E8CDBB", "牛皮", "slim", "regular", 3,
              ("优雅",), confidence=0.9),
        _Item(Slot.accessory, "金色项链", "金色", "#D4AF37", "金属", "regular", "regular", 3,
              ("优雅",), confidence=0.85),
        _Item(Slot.accessory, "银色手链", "银色", "#C0C0C0", "金属", "regular", "regular", 2,
              ("极简",), confidence=0.84),
        _Item(Slot.accessory, "手袋", "红色", "#C8102E", "牛皮", "regular", "regular", 3,
              ("潮流",), confidence=0.85),
        _Item(Slot.accessory, "贝雷帽", "黑色", "#1A1A1A", "针织", "regular", "regular", 2,
              ("法式",), confidence=0.84),
        _Item(Slot.accessory, "珍珠耳饰", "白色", "#F5F2E8", "金属", "regular", "regular", 3,
              ("优雅",), confidence=0.84),
    )),
    # 10) 正式缺件：无鞋无外套仅 2 件 → R-OCC-002 / R-OCC-007
    _Script("formal_missing", ("missing", "缺件", "formal_missing"), (
        _Item(Slot.upper, "白衬衫", "白色", "#FAF7F2", "棉", "regular", "regular", 4,
              ("正式", "商务正装"), confidence=0.9),
        _Item(Slot.lower, "西裤", "黑色", "#1A1A1A", "羊毛", "regular", "long", 4,
              ("正式",), confidence=0.9),
    )),
)

SCRIPTS: tuple[str, ...] = tuple(s.key for s in _SCRIPTS)
N_SCRIPTS: int = len(_SCRIPTS)


# ---------------------------------------------------------------------------
# 选择规则
# ---------------------------------------------------------------------------
def _select_script(refs: list[str]) -> tuple[_Script, str]:
    """返回 ``(script, reason)``；reason ∈ {hint:<alias>, hash}。

    规则（确定性，可被评测用例稳定依赖）：
        1. 任一 ref 含剧本别名（不区分大小写）→ 取**最长别名**命中者；
           同长度并列时取剧本定义顺序靠前者。因此 ``demo/commute-1`` 必中通勤。
        2. 否则 ``sha256(refs) % N_SCRIPTS`` 选择。
    """
    lowered = [r.lower() for r in refs]
    best: tuple[int, int, _Script, str] | None = None  # (-alias_len, script_idx, …)
    for idx, script in enumerate(_SCRIPTS):
        for alias in script.aliases:
            if any(alias in ref for ref in lowered):
                candidate = (-len(alias), idx, script, alias)
                if best is None or candidate[:2] < best[:2]:
                    best = candidate
    if best is not None:
        return best[2], f"hint:{best[3]}"
    digest = int(stable_hash(*refs, size=16), 16)
    return _SCRIPTS[digest % N_SCRIPTS], "hash"


# ---------------------------------------------------------------------------
# MockParser
# ---------------------------------------------------------------------------
class MockParser(BaseParser):
    """确定性离线解析器（默认实现；无 key 时全链路走这里）。"""

    name = "mock"
    model = "mock-v1"
    mode = "offline"

    def parse_outfit(
        self,
        image_refs: list[str],
        occasion: str | None = None,
        season: str | None = None,
    ) -> ParseResult:
        refs = self._clean_refs(image_refs)
        warnings: list[str] = []

        script, _reason = _select_script(refs)
        # occasion / season 只反映**用户声明**；缺省保持 unspecified / unknown，
        # 让 D4 整组按规则跳过（rules-author 约定，不替用户臆断场合）。
        effective_occasion = self._resolve_occasion(occasion, "unspecified", warnings)
        effective_season = self._resolve_season(season, "unknown", warnings)

        outfit = self._build_outfit(refs, script, effective_occasion, effective_season)
        return ParseResult(outfit=outfit, warnings=warnings)

    # -- 内部 -------------------------------------------------------------
    @staticmethod
    def _build_outfit(refs: list[str], script: _Script,
                      occasion: str | None, season: str | None) -> Outfit:
        outfit_id = make_id("oft", *refs)
        garments: list[Garment] = []
        for index, item in enumerate(script.items):
            color = Color(hex=item.hex, name=item.name, confidence=0.95)
            # color family 只有一处实现（docs/03 §7.5），避免 mock 与特征工程漂移
            color.family = color_family_from_hsv(color.hue, color.saturation, color.lightness)
            garments.append(Garment(
                garment_id=make_id("gar", outfit_id, item.slot.value, index),
                slot=item.slot,
                category=item.category,
                colors=[color],
                material=item.material,
                fit=item.fit,
                silhouette=item.silhouette,
                length=item.length,
                formality=item.formality,
                style_tags=list(item.style_tags),
                pattern=item.pattern,
                confidence=item.confidence,
                evidence=f"mock:{script.key}",
            ))
        confidence = round(sum(g.confidence for g in garments) / len(garments), 4)
        return Outfit(
            outfit_id=outfit_id,
            items=garments,
            occasion=occasion,
            season=season,
            image_ref=refs[0],
            confidence=confidence,
            parse_warnings=[],
        )
