"""风格画像构建（StyleProfile）。

职责（docs/00 §7.3 / docs/03 §7.3 / task-5）：
    从若干套参考穿搭聚合出
      * ``tags``：按权重降序的 StyleTag 分布（top-5）；
      * ``tag_evidence``：每个标签 → 来源 outfit_id 列表（US-2 可回溯）；
      * ``preferred_formality``：加权平均正式度（映射到 1..5）；
      * ``n_ref_outfits`` / ``confidence``：样本量与置信度（n_ref < 3 → < 0.6）；
      * ``preference_vector``：用户塔向量 u（参考穿搭向量的完整度加权平均，
        docs/02 §6.3），供 :class:`src.towers.TowerEncoder` 的 ``encode_profile`` 复用。

确定性：所有聚合只依赖输入内容；排序键为 ``(-weight, tag)``，不用内建 hash。
"""
from __future__ import annotations

import math
from typing import Iterable

from ..contracts import Outfit, Slot, StyleProfile, StyleTagScore
from ..styles import families_from_text, family_name
from ..towers import TowerEncoder
from ..utils import make_id, round_half_up

# 单品对"整身风格"的贡献权重（越靠近视觉主体越重；常量，不随数据变）
SLOT_WEIGHTS: dict[Slot, float] = {
    Slot.dress: 1.0,
    Slot.upper: 1.0,
    Slot.lower: 1.0,
    Slot.outer: 0.8,
    Slot.shoes: 0.6,
    Slot.accessory: 0.45,
}

MAX_TAGS: int = 5
CONFIDENCE_BASE: float = 0.30
CONFIDENCE_PER_REF: float = 0.13
CONFIDENCE_CAP: float = 0.95


def _unique_outfits(outfits: Iterable[Outfit]) -> list[Outfit]:
    """按 outfit_id 去重（保持确定性顺序：排序后使用）。"""
    by_id: dict[str, Outfit] = {}
    for outfit in outfits:
        if outfit is None:
            continue
        by_id[outfit.outfit_id] = outfit
    return [by_id[oid] for oid in sorted(by_id)]


def _completeness(outfit: Outfit) -> float:
    """``slot_coverage × mean(garment.confidence)`` ∈ (0, 1]（docs/02 §6.3）。"""
    slots = {g.slot for g in outfit.items}
    if Slot.dress in slots:
        required = {Slot.dress, Slot.shoes}
    else:
        required = {Slot.upper, Slot.lower, Slot.shoes}
    coverage = len(slots & required) / len(required)
    confidences = [g.confidence for g in outfit.items]
    mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return max(0.0, min(1.0, coverage * mean_conf))


def _tag_votes(outfit: Outfit) -> dict[str, float]:
    """单套穿搭的标签票数（按 slot 权重 × 单品置信度加权）。"""
    votes: dict[str, float] = {}
    for garment in outfit.items:
        weight = SLOT_WEIGHTS.get(garment.slot, 0.5) * garment.confidence
        for tag in garment.style_tags:
            votes[tag] = votes.get(tag, 0.0) + weight
    return votes


def _l2_normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    if norm <= 1e-12:
        return [0.0 for _ in values]
    return [v / norm for v in values]


def _confidence(n_ref: int, mean_parse_confidence: float) -> float:
    """样本量主导、解析质量微调：n<3 时必然 < 0.6（task-5 硬要求）。"""
    by_size = min(CONFIDENCE_CAP, CONFIDENCE_BASE + CONFIDENCE_PER_REF * n_ref)
    quality = 0.5 + 0.5 * max(0.0, min(1.0, mean_parse_confidence))
    return round(max(0.0, min(1.0, by_size * quality)), 4)


def _preferred_formality(outfits: list[Outfit]) -> int | None:
    total_weight = 0.0
    total = 0.0
    for outfit in outfits:
        for garment in outfit.items:
            if garment.formality is None:
                continue
            weight = SLOT_WEIGHTS.get(garment.slot, 0.5) * garment.confidence
            total += weight * float(garment.formality)
            total_weight += weight
    if total_weight <= 0.0:
        return None
    value = total / total_weight
    return max(1, min(5, int(round_half_up(value, 0))))


def _preference_vector(outfits: list[Outfit], encoder: TowerEncoder) -> list[float] | None:
    """用户塔 u = L2norm(Σ w_i·v_i / Σ w_i)，w_i = 完整度（docs/02 §6.3）。"""
    weighted = [0.0] * encoder.dim
    total_weight = 0.0
    for outfit in outfits:
        weight = _completeness(outfit)
        if weight <= 0.0:
            continue
        vector = encoder.encode_outfit(outfit)
        for i, value in enumerate(vector):
            weighted[i] += weight * value
        total_weight += weight
    if total_weight <= 0.0:
        return None
    averaged = [value / total_weight for value in weighted]
    return _l2_normalize(averaged)


def build_profile(outfits: list[Outfit],
                  profile_id: str | None = None) -> StyleProfile:
    """从参考穿搭聚合 :class:`StyleProfile`（纯确定性，无网络）。

    Args:
        outfits: 已解析的参考穿搭（同一 outfit_id 去重）。
        profile_id: 可选画像 ID；缺省由参考穿搭 ID 的 sha256 派生（可复现）。
    """
    ordered = _unique_outfits(outfits or [])
    source_ids = [o.outfit_id for o in ordered]

    # --- 标签分布：每套先归一，再跨套平均（避免单品多的搭配主导） ---
    tag_scores: dict[str, float] = {}
    tag_evidence: dict[str, list[str]] = {}
    for outfit in ordered:
        votes = _tag_votes(outfit)
        total = sum(votes.values())
        if total <= 0.0:
            continue
        for tag, raw in votes.items():
            tag_scores[tag] = tag_scores.get(tag, 0.0) + raw / total
            tag_evidence.setdefault(tag, []).append(outfit.outfit_id)

    n_ref = len(ordered)
    if ordered:
        for tag in tag_scores:
            tag_scores[tag] = tag_scores[tag] / n_ref

    ranked = sorted(
        ((round(score, 6), tag) for tag, score in tag_scores.items()),
        key=lambda pair: (-pair[0], pair[1]),
    )
    tags = [StyleTagScore(tag=tag, weight=max(0.0, min(1.0, score)))
            for score, tag in ranked[:MAX_TAGS]]

    # --- 风格家族分布（docs/10 F-17）---
    # 为什么单独给一层：模型现在只在 style_tags 里写受控词（甜酷/街头潮流），
    # 把 Y2K/千禧/古早 放进 style_words 与 merchant_title。用户想看到的是后者。
    # 家族层只用于**展示与解释**，不参与向量（向量仍吃 STYLE_TAGS 受控词）。
    family_scores: dict[str, float] = {}
    for outfit in ordered:
        votes = _tag_votes(outfit)
        total = sum(votes.values())
        if total <= 0.0:
            continue
        fam_votes: dict[str, float] = {}
        for tag, weight in votes.items():
            name = family_name(tag)
            if name:
                fam_votes[name] = fam_votes.get(name, 0.0) + weight
        # style_words 与商家标题里也藏着家族（"Y2K辣妹 波点卫衣"）
        for garment in outfit.items:
            for text in (garment.merchant_title, " ".join(garment.style_words or [])):
                for name in families_from_text(text):
                    fam_votes.setdefault(name, 0.0)
        for name, raw in fam_votes.items():
            family_scores[name] = family_scores.get(name, 0.0) + raw / total
    for name in family_scores:
        family_scores[name] = family_scores[name] / n_ref if n_ref else 0.0
    family_tags = [StyleTagScore(tag=name, weight=max(0.0, min(1.0, round(score, 6))))
                   for name, score in sorted(family_scores.items(),
                                             key=lambda kv: (-kv[1], kv[0]))[:MAX_TAGS]]

    mean_parse_confidence = (
        sum(o.confidence for o in ordered) / n_ref if n_ref else 0.0
    )
    encoder = TowerEncoder()
    preference_vector = _preference_vector(ordered, encoder) if ordered else None

    resolved_id = profile_id.strip() if isinstance(profile_id, str) and profile_id.strip() else None
    if resolved_id is None:
        resolved_id = make_id("prf", *source_ids) if source_ids else make_id("prf", "empty")

    return StyleProfile(
        profile_id=resolved_id,
        tags=tags,
        family_tags=family_tags,
        # MVP 的冻结契约里没有 forbidden_styles 输入（CreateProfileRequest 只有
        # image_refs/note/persist），因此这里不臆断"禁用风格"；上层如需可用
        # model_copy(update={"banned_styles": [...]}) 注入，避免误触发 hard 规则。
        banned_styles=[],
        preferred_formality=_preferred_formality(ordered),
        preference_vector=preference_vector,
        n_ref_outfits=n_ref,
        confidence=_confidence(n_ref, mean_parse_confidence),
        source_outfit_ids=source_ids,
        tag_evidence={tag: sorted(ids) for tag, ids in sorted(tag_evidence.items())},
    )
