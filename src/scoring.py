"""确定性评分器 —— INV-1 / INV-2 / INV-4 的唯一实现处。

职责边界（刻意收窄）：
  输入：已由 RuleEngine 完成「门控 + 抑制 + 互斥 + 确定性排序」的 RuleHit 列表
  输出：预算截断后的 RuleHit（含 delta_applied / clipped）+ 维度分 + 总分 + 账本

本模块**不含任何**：模型调用、随机数、时间依赖、字典序以外的顺序依赖。
同输入必得同输出（docs/03 §2.4）。
"""
from __future__ import annotations

from .contracts import (
    BASE_SCORE_CENTI,
    DIM_BY_CODE,
    DIMENSIONS,
    Category,
    DimensionCode,
    DimensionScore,
    RuleHit,
    ScoringResult,
    ScoreLedgerEntry,
    Severity,
    Features,
    verdict_of,
)
from .utils import centi_to_score, to_centi, to_points

# 分类的确定性执行顺序（禁止依赖加载顺序/哈希顺序）
CATEGORY_ORDER: dict[Category, int] = {
    Category.COLOR: 0,
    Category.STYLE: 1,
    Category.PROPORTION: 2,
    Category.OCCASION: 3,
    Category.DETAIL: 4,
}

# 证据可用度低于此阈值的维度，其规则不执行（保持 base_d），避免"证据不足却下判断"
MIN_ASSESSABILITY: float = 0.5


def sort_key(hit: RuleHit) -> tuple[int, str]:
    return (CATEGORY_ORDER[hit.category], hit.rule_id)


def score(hits: list[RuleHit], features: Features) -> ScoringResult:
    """核心打分。整数厘点运算，仅展示字段做一次 round_half_up。"""
    ordered = sorted(hits, key=sort_key)

    penalty_used: dict[DimensionCode, int] = {d.code: 0 for d in DIMENSIONS}
    boost_used: dict[DimensionCode, int] = {d.code: 0 for d in DIMENSIONS}

    applied_hits: list[RuleHit] = []
    ledger: list[ScoreLedgerEntry] = []

    for seq, hit in enumerate(ordered, start=1):
        spec = DIM_BY_CODE[hit.dimension]
        raw_centi = to_centi(hit.delta_schema)

        if raw_centi >= 0:  # boost
            remaining = max(spec.boost_budget_centi - boost_used[hit.dimension], 0)
            applied = min(raw_centi, remaining)
            clipped = applied != raw_centi
            boost_used[hit.dimension] += applied
            reason = "" if not clipped else f"加分预算耗尽（剩余 {to_points(remaining)}）"
        else:  # penalty / hard
            needed = -raw_centi
            remaining = max(spec.base_centi - penalty_used[hit.dimension], 0)
            used = min(needed, remaining)
            applied = -used
            clipped = used != needed
            penalty_used[hit.dimension] += used
            reason = "" if not clipped else f"扣分预算耗尽（剩余 {to_points(remaining)}）"

        applied_hits.append(hit.model_copy(update={
            "delta_applied": to_points(applied),
            "clipped": clipped,
        }))
        ledger.append(ScoreLedgerEntry(
            seq=seq,
            rule_id=hit.rule_id,
            dimension=hit.dimension,
            delta_schema=hit.delta_schema,
            delta_applied=to_points(applied),
            clipped=clipped,
            reason=reason,
        ))

    dims: list[DimensionScore] = []
    total_centi = 0
    for spec in DIMENSIONS:
        sc = spec.base_centi - penalty_used[spec.code] + boost_used[spec.code]
        sc = max(0, min(sc, spec.max_centi))
        total_centi += sc
        assess = features.assess(spec.code)
        dims.append(DimensionScore(
            code=spec.code,
            name=spec.name,
            score=centi_to_score(sc),
            max=to_points(spec.max_centi),
            weight=spec.weight,
            base=to_points(spec.base_centi),
            assessability=assess,
            penalty_used=to_points(penalty_used[spec.code]),
            boost_used=to_points(boost_used[spec.code]),
            score_centi=sc,
            base_centi=spec.base_centi,
            max_centi=spec.max_centi,
            penalty_centi=penalty_used[spec.code],
            boost_centi=boost_used[spec.code],
            gated=assess < MIN_ASSESSABILITY,
        ))

    vetoed = any(h.severity == Severity.hard for h in applied_hits)
    verdict = verdict_of(total_centi, vetoed)

    return ScoringResult(
        total_centi=total_centi,
        base_centi=BASE_SCORE_CENTI,
        vetoed=vetoed,
        verdict=verdict,
        dimensions=dims,
        hits=applied_hits,
        ledger=ledger,
    )
