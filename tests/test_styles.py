"""风格本体（家族/别名）与正式度方向兜底的测试（docs/10 F-17）。

真实故障链（用户 5 张参考 + 1 张待评审）：
  · 模型把 Y2K 写成「日系少女 / 居家 / 学院风」，而 struct 塔只认 25 个受控词
    → 个性化信号变成 0；
  · R-STYLE-002/003 用字符串精确比对 → top_overlap=0、jaccard=0
    → D2 从 18 掉到 5，报告判「一般 56 分」，但人看是同一路风格（Y2K 古早混搭）。
  · 同一批解析里，卫衣/卫裤/运动鞋的 formality 全被标成 5（模型把 5 当"最休闲"），
    归一化后成了"最正式"，把 D4 与 R-STYLE-006 带偏。
"""
from __future__ import annotations

import pytest

from src.parsing.qwen_vlm import _formality_to_contract
from src.styles import (FAMILIES, canonical_tags, expand_outfit_tags, families_from_text,
                        families_of, family_of, is_known, unmapped)
from src.towers.encoder import STYLE_TAGS


def test_same_style_different_words_share_a_family():
    """Y2K / 千禧辣妹 / 古早 / 原宿 必须落在同一家族。"""
    names = {family_of(t).name for t in ("Y2K", "千禧辣妹", "古早", "原宿", "辣妹风")}
    assert names == {"千禧辣妹"}


def test_family_canonical_tags_are_in_controlled_vocabulary():
    """家族的受控代表必须落在 STYLE_TAGS 里，否则 struct 塔仍然吃不到。"""
    for fam in FAMILIES:
        for tag in fam.canonical:
            assert tag in STYLE_TAGS, f"{fam.name} 的受控词 {tag} 不在 STYLE_TAGS"


def test_expand_keeps_original_and_adds_canonical():
    out = expand_outfit_tags(["Y2K", "街头风"])
    assert out[0] == "Y2K"                      # 原文保留（给展示）
    assert "甜酷" in out and "街头潮流" in out   # 受控展开（喂向量）
    assert len(out) == len(set(out))            # 去重


def test_canonical_tags_of_controlled_words_are_stable():
    """已是受控词 → 原样保留，不按家族扩张（否则「甜酷」会被放大成两个方向）。"""
    assert canonical_tags(["甜酷"]) == ["甜酷"]
    assert canonical_tags(["复古"]) == ["复古"]
    # 自由词才展开
    assert canonical_tags(["Y2K"]) == ["甜酷", "街头潮流"]


def test_controlled_vocabulary_matches_encoder():
    """styles 里的受控词表必须与 encoder 的 STYLE_TAGS 完全一致（防漂移）。"""
    from src.styles import CONTROLLED_STYLE_TAGS

    assert tuple(CONTROLLED_STYLE_TAGS) == tuple(STYLE_TAGS)


def test_families_of_ignores_unknown_words():
    assert families_of(["极简", "完全不存在的风格词"]) == {"通勤知性"}
    assert family_of("完全不存在的风格词") is None


def test_families_from_text_reads_merchant_titles():
    """商家标题是风格词的富矿——这是"接入商品标签"的零成本替代。"""
    fams = families_from_text("Y2K辣妹 低腰微喇牛仔裤 复古水洗 甜酷百搭")
    assert "千禧辣妹" in fams
    assert "复古怀旧" in fams


def test_report_case_y2k_outfit_overlaps_profile_families():
    """回归用户那次真实误判：日系少女 vs Y2K 应算同族（不再"毫无交集"）。"""
    outfit = families_of(["日系少女", "居家", "学院风"])
    profile = families_of(["Y2K", "运动休闲", "美式复古"])
    # 学院风/居家 不属千禧族，但"日系少女"归甜美少女族——两者不该被当成同一族。
    assert "千禧辣妹" not in outfit
    # 但 outfit 内部认出了 甜美少女，profile 内部认出了 复古怀旧/运动休闲
    assert "甜美少女" in outfit and "复古怀旧" in profile


def test_unmapped_lists_words_outside_ontology():
    assert unmapped(["Y2K", "量子玄学风"]) == ["量子玄学风"]


def test_is_known():
    assert is_known("甜酷") and is_known("y2k") and not is_known("量子玄学风")


# ---------------- 正式度方向兜底 ----------------

@pytest.mark.parametrize("desc,expected", [
    ("loungewear", 1), ("casual", 2), ("smart_casual", 3), ("business", 4), ("formal", 5),
])
def test_formality_accepts_descriptor_words(desc: str, expected: int) -> None:
    assert _formality_to_contract(desc) == expected


@pytest.mark.parametrize("value,category,expected", [
    (5, "连帽卫衣", 2),      # 模型把 5 当"最休闲" → 夹到休闲
    (5, "运动卫裤", 2),
    (5, "运动鞋", 2),
    (5, "睡衣", 1),
    (2, "西装", 4),          # 品类下限兜住模型的低值
    (2, "衬衫", 3),
    (3, "连衣裙", 3),        # 没命中关键词 → 原样保留
    (0.3, "T恤", 2),         # 0..1 口径兼容
])
def test_formality_is_clamped_by_category_keywords(value, category, expected) -> None:
    assert _formality_to_contract(value, category) == expected


# ---------------- 画像里的风格家族 ----------------

def test_profile_surfaces_family_from_merchant_title() -> None:
    """用户想看到「Y2K」，但受控词里没有 —— 家族层负责把它显示出来。"""
    from src.contracts import Color, Garment, Outfit, Slot
    from src.profiling.builder import build_profile

    outfit = Outfit(outfit_id="oft_fam", items=[
        Garment(garment_id="g1", slot=Slot.upper, category="连帽卫衣",
                colors=[Color(hex="#FFC0CB")], style_tags=["甜酷"],
                style_words=["露脐", "千禧感"],
                merchant_title="Y2K甜酷辣妹 粉色短款连帽卫衣"),
        Garment(garment_id="g2", slot=Slot.lower, category="波点短裤",
                colors=[Color(hex="#1A1A1A")], style_tags=["甜酷"],
                style_words=["低腰"], merchant_title="Y2K千禧风 波点短裤"),
    ])
    profile = build_profile([outfit], profile_id="prf_fam")
    names = [t.tag for t in profile.family_tags]
    assert "千禧辣妹" in names, names
    # 受控标签仍然是受控词（向量侧口径不变）
    assert "甜酷" in [t.tag for t in profile.tags]
