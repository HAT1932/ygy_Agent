"""风格本体层：把模型随口写的中文风格词，收敛成**可计算的家族 + 受控标签**。

为什么需要（docs/10 F-17）
--------------------------
实测（用户的 5 张参考 + 1 张待评审）暴露了三处静默失真：

1. **struct 塔只认受控词**：``STYLE_TAGS`` 那 25 个词之外的标签直接变 0。
   模型对同一套搭配写 ``日系少女 / 居家 / 学院风 / JK制服``，全部不在词表里 → 个性化信号被丢掉。
2. **规则用字符串精确比对**：画像 top 是 ``Y2K / 运动休闲 / 美式复古``，
   搭配标签是 ``日系少女 / 居家 / 学院风`` → ``top_overlap_count=0``
   → R-STYLE-002（-5）+ R-STYLE-003（-4）直接把 D2 从 18 打到 5。
   但这两套明明是同一种风格的不同说法（Y2K ≈ 千禧辣妹 ≈ 古早原宿）。
3. **同义词无法互通**：``千禧``/``Y2K``/``古早``/``原宿``/``辣妹风`` 指的是一个东西。

做法：把「家族（family）」作为唯一语义层——

  · 每个家族 = 中文显示名 + **受控标签代表**（喂 struct 塔 / 现有规则）+ **别名表**（匹配自由文本）
  · encoder 用 ``expand_outfit_tags`` 把 Y2K 展开成 ``甜酷/街头潮流``，向量不再丢信号
  · context 用 ``families_of`` 算「家族级重合」，让"同一风格的两种说法"不再算冲突
  · 原文（Y2K）保留给展示与证据，用户看到的就是自己的词

这样既不破坏 256 维向量与既有 ruleset 的标定，又能让同义词互通。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Family:
    """一个风格家族。

    ``canonical`` 必须是 ``towers.encoder.STYLE_TAGS`` 里的词——它是喂给 struct 塔的
    受控标签；``aliases`` 是模型/商家标题里会出现的自由写法。
    """

    name: str
    canonical: tuple[str, ...]
    aliases: tuple[str, ...]


#: 家族表（顺序即优先级：别名长度相同时靠前者胜）
FAMILIES: tuple[Family, ...] = (
    Family("千禧辣妹", ("甜酷", "街头潮流"), (
        "y2k", "千禧", "千禧风", "千禧辣妹", "千禧甜心", "y2k辣妹", "古早", "古早味",
        "原宿", "亚文化", "亚逼", "辣妹", "辣妹风", "甜辣", "甜酷", "多巴胺",
        "复古运动", "日系复古", "古早混搭", "千禧混搭",
    )),
    Family("街头潮流", ("街头潮流", "潮流"), (
        "街头", "街头风", "高街", "嘻哈", "hiphop", "hip-hop", "滑板", "朋克", "朋克风",
        "潮流", "个性", "叠戴", "街头配饰", "硬核", "废土", "机能街头",
    )),
    Family("复古怀旧", ("复古",), (
        "复古", "美式复古", "古着", "港风", "怀旧", "vintage", "古董", "老派",
    )),
    Family("甜美少女", ("甜美", "浪漫"), (
        "甜美", "少女感", "日系少女", "可爱", "软妹", "甜妹", "仙气", "少女",
        "日系", "甜美风", "少女心", "初恋感",
    )),
    Family("学院制服", ("学院",), (
        "学院", "学院风", "jk", "jk制服", "制服", "预科生", "校服", "百褶",
    )),
    Family("通勤知性", ("通勤知性", "优雅", "极简"), (
        "通勤", "日常通勤", "知性", "优雅", "极简", "简约", "经典", "简洁",
        "商务", "商务正装", "正式", "职场", "老钱", "低调",
    )),
    Family("法式优雅", ("法式",), (
        "法式", "法式复古", "法式慵懒", "巴黎风", "慵懒法式",
    )),
    Family("运动休闲", ("运动休闲", "舒适"), (
        "运动", "运动休闲", "运动混搭", "健身", "瑜伽", "户外", "运动风", "舒适",
    )),
    Family("居家休闲", ("居家", "休闲"), (
        "居家", "家居", "慵懒", "放松", "睡衣", "休闲", "日常", "随性", "松弛",
    )),
    Family("工装机能", ("工装", "机能风", "硬核工装"), (
        "工装", "机能", "机能风", "军装", "战术", "户外机能", "硬核工装",
    )),
    Family("暗黑哥特", ("洛丽塔",), (
        "哥特", "哥特萝莉", "暗黑", "黑暗", "洛丽塔", "蕾丝", "铆钉", "神秘",
    )),
    Family("华丽礼服", ("华丽", "正式"), (
        "华丽", "亮片", "礼服", "派对", "晚宴", "礼服感", "盛装",
    )),
    Family("民族度假", ("浪漫",), (
        "民族", "波西米亚", "度假", "海岛", "旅行", "印花长裙", "异域",
    )),
)

FAMILY_NAMES: tuple[str, ...] = tuple(f.name for f in FAMILIES)

#: 受控风格词表（与 towers.encoder.STYLE_TAGS 保持一致；tests/test_styles.py 有断言防漂移）。
#: 放在这里而不是 import encoder，是为了避免 styles ←→ encoder 的循环依赖。
CONTROLLED_STYLE_TAGS: tuple[str, ...] = (
    "极简", "通勤知性", "法式", "甜美", "运动休闲", "街头潮流", "商务正装",
    "机能风", "工装", "正式", "优雅", "经典", "简约", "商务", "浪漫", "甜酷",
    "华丽", "潮流", "休闲", "居家", "舒适", "洛丽塔", "硬核工装", "复古", "学院",
)
_CONTROLLED = frozenset(CONTROLLED_STYLE_TAGS)

#: 家族 → 受控标签
CANONICAL_BY_FAMILY: dict[str, tuple[str, ...]] = {f.name: f.canonical for f in FAMILIES}

#: 别名 / 受控词 → 家族（小写索引；长度优先匹配）
_ALIAS_INDEX: dict[str, Family] = {}
for _fam in FAMILIES:
    for _word in (*_fam.canonical, *_fam.aliases):
        _ALIAS_INDEX.setdefault(_word.strip().lower(), _fam)

#: 受控标签 → 其所属家族（一个受控词只归一个家族，保证 partition）
_FAMILY_BY_CANONICAL: dict[str, Family] = {}
for _fam in FAMILIES:
    for _word in _fam.canonical:
        _FAMILY_BY_CANONICAL.setdefault(_word, _fam)


def _key(value: object) -> str:
    return str(value or "").strip().lower()


def family_of(tag: object) -> Family | None:
    """精确命中优先，其次做「包含」匹配（模型常写「千禧辣妹风」「美式复古街头」）。"""
    key = _key(tag)
    if not key:
        return None
    exact = _ALIAS_INDEX.get(key)
    if exact is not None:
        return exact
    best: Family | None = None
    best_len = 0
    for alias, fam in _ALIAS_INDEX.items():
        if len(alias) >= 2 and alias in key and len(alias) > best_len:
            best, best_len = fam, len(alias)
    return best


def family_name(tag: object) -> str | None:
    fam = family_of(tag)
    return fam.name if fam else None


def families_of(tags: Iterable[object]) -> set[str]:
    """标签集合 → 家族集合（无法识别的词被忽略）。"""
    out: set[str] = set()
    for tag in tags or ():
        fam = family_of(tag)
        if fam is not None:
            out.add(fam.name)
    return out


def families_from_text(text: str | None) -> set[str]:
    """从自由文本（商家标题 / 细粒度风格词 / 视觉证据）里扫出家族。

    这是「接入商品搜索看商家标签」的零成本替代：商家标题本身就是风格词的富矿
    （「Y2K辣妹 低腰微喇牛仔裤 复古水洗」），扫一遍就能补出模型漏掉的家族。
    """
    key = _key(text)
    if not key:
        return set()
    out: set[str] = set()
    for alias, fam in _ALIAS_INDEX.items():
        if len(alias) >= 2 and alias in key:
            out.add(fam.name)
    return out


def canonical_tags(tags: Iterable[object]) -> list[str]:
    """自由标签 → 受控标签（去重保序）。

    已经是受控词的**原样保留**（不再按家族扩张，否则「甜酷」会被放大成
    「甜酷 + 街头潮流」，把向量推向过宽的方向）；只有自由词才走家族展开。
    """
    out: list[str] = []
    for tag in tags or ():
        text = str(tag)
        if text in _CONTROLLED:
            reps: Sequence[str] = (text,)
        else:
            fam = family_of(text)
            reps = fam.canonical if fam is not None else (text,)
        for rep in reps:
            if rep not in out:
                out.append(rep)
    return out


def expand_outfit_tags(tags: Iterable[object]) -> list[str]:
    """原文 + 受控展开。给 encoder 用：原文保留语义，受控词让向量真正吃到信号。

    ``["Y2K", "街头风"]`` → ``["Y2K", "街头风", "甜酷", "街头潮流", "潮流"]``
    """
    out: list[str] = []
    for tag in tags or ():
        key = str(tag)
        if key and key not in out:
            out.append(key)
    for rep in canonical_tags(tags):
        if rep not in out:
            out.append(rep)
    return out


def is_known(tag: object) -> bool:
    return family_of(tag) is not None


def describe(tag: object) -> dict[str, object]:
    """调试/诊断用：一个标签的家族归属与受控展开。"""
    fam = family_of(tag)
    return {
        "tag": str(tag),
        "family": fam.name if fam else None,
        "canonical": list(fam.canonical) if fam else [],
    }


def all_aliases() -> dict[str, str]:
    return {alias: fam.name for alias, fam in _ALIAS_INDEX.items()}


def unmapped(tags: Sequence[object]) -> list[str]:
    """落在本体之外的词（用于诊断：提示该扩表了）。"""
    return [str(t) for t in tags or () if family_of(t) is None]
