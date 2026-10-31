"""确定性评分器的单元测试 —— 守护 INV-1 / INV-2 / INV-4。

这些用例不依赖模型、不依赖规则内容，是 CI 的第一道闸门（docs/08 §4.2）。
"""
from __future__ import annotations

import pytest

from src.contracts import (
    BASE_SCORE_CENTI,
    DIM_BY_CODE,
    TOTAL_BOOST_BUDGET_CENTI,
    Category,
    DimensionCode,
    Features,
    RuleHit,
    Severity,
    Verdict,
)
from src.scoring import score


def hit(rule_id: str, dim: DimensionCode, delta: float, *, severity: Severity = Severity.soft,
        category: Category | None = None) -> RuleHit:
    cat = category or {
        DimensionCode.D1: Category.COLOR,
        DimensionCode.D2: Category.STYLE,
        DimensionCode.D3: Category.PROPORTION,
        DimensionCode.D4: Category.OCCASION,
        DimensionCode.D5: Category.DETAIL,
    }[dim]
    return RuleHit(
        rule_id=rule_id,
        title=f"test-{rule_id}",
        category=cat,
        severity=severity,
        dimension=dim,
        delta_schema=delta,
        explanation="测试规则",
    )


def feats(**kw) -> Features:
    return Features(**kw)


# ---------------------------------------------------------------- 基准分


def test_base_score_is_70_and_verdict_suitable():
    r = score([], feats())
    assert r.total_centi == BASE_SCORE_CENTI == 7000
    assert r.verdict == Verdict.suitable          # 70 落在 65–79 区间
    assert r.vetoed is False
    assert [d.score_centi for d in r.dimensions] == [d.base_centi for d in r.dimensions]


def test_dimension_budgets_sum_to_max():
    # INV-4：base_d + boost 预算 == max_d
    total_max = 0
    for spec in DIM_BY_CODE.values():
        assert spec.base_centi + spec.boost_budget_centi == spec.max_centi
        total_max += spec.max_centi
    assert total_max == 10000
    assert TOTAL_BOOST_BUDGET_CENTI == 3000


# ---------------------------------------------------------------- 扣分与截断


def test_single_penalty_applies_exactly():
    r = score([hit("R-COLOR-001", DimensionCode.D1, -5.0)], feats())
    assert r.total_centi == 6500
    assert r.verdict == Verdict.suitable
    assert r.hits[0].delta_applied == -5.0
    assert r.hits[0].clipped is False


def test_penalty_is_clipped_at_base_budget():
    """同一维度疯狂扣分，最多只能扣到 0 分（预算 = base_d），超出部分必须标记 clipped。"""
    hits = [hit(f"R-COLOR-90{i}", DimensionCode.D1, -10.0) for i in range(6)]
    r = score(hits, feats())
    d1 = next(d for d in r.dimensions if d.code == DimensionCode.D1)
    assert d1.penalty_centi == d1.base_centi == 1560      # 最多扣到 0
    assert d1.score_centi == 0
    # 第一条 10 分 < 15.6 预算，不截断；之后全部截断
    assert r.hits[0].clipped is False
    assert all(h.clipped for h in r.hits[1:])
    # 生效总额必须等于预算，不能多扣
    assert sum(int(round(h.delta_applied * 100)) for h in r.hits) == -1560
    # 账本必须记录截断原因
    assert any("预算耗尽" in e.reason for e in r.ledger)


def test_boost_is_clipped_at_boost_budget():
    """加分不能突破 max_d（预算 = max_d - base_d）。"""
    hits = [hit(f"R-COLOR-B0{i}", DimensionCode.D1, +3.0, severity=Severity.boost)
            for i in range(5)]
    r = score(hits, feats())
    d1 = next(d for d in r.dimensions if d.code == DimensionCode.D1)
    assert d1.boost_centi == d1.max_centi - d1.base_centi == 440
    assert d1.score_centi == d1.max_centi == 2000
    assert r.total_centi == 7000 + 440        # 只有 D1 的 4.4 分加分生效
    assert sum(1 for h in r.hits if h.clipped) >= 1


def test_boost_and_penalty_net_out():
    hits = [
        hit("R-COLOR-001", DimensionCode.D1, -6.0),
        hit("R-COLOR-B01", DimensionCode.D1, +2.0, severity=Severity.boost),
    ]
    r = score(hits, feats())
    d1 = next(d for d in r.dimensions if d.code == DimensionCode.D1)
    assert d1.penalty_centi == 600
    assert d1.boost_centi == 200
    assert d1.score_centi == 1560 - 600 + 200


def test_total_never_below_zero_or_above_100():
    heavy = [hit(f"R-COLOR-1{i:02d}", DimensionCode.D1, -20.0) for i in range(10)]
    r = score(heavy, feats())
    assert 0 <= r.total_centi <= 10000
    boosty = [hit(f"R-COLOR-B{i:02d}", DimensionCode.D1, +20.0, severity=Severity.boost)
              for i in range(10)]
    r2 = score(boosty, feats())
    assert r2.total_centi <= 10000


# ---------------------------------------------------------------- veto 与判定


def test_hard_rule_vetoes_even_with_high_score():
    hits = [
        hit("R-COLOR-001", DimensionCode.D1, -1.0, severity=Severity.hard),
        *(hit(f"R-COLOR-B{i:02d}", DimensionCode.D1, +2.0, severity=Severity.boost) for i in range(2)),
    ]
    r = score(hits, feats())
    assert r.vetoed is True
    assert r.verdict == Verdict.unsuitable


@pytest.mark.parametrize("hits,expected", [
    # 0 -> 7000 -> 70.0 适合
    ([], Verdict.suitable),
    # 加分跨维度凑到 8000（D2 加分预算满 7.0 + D3 3.0），恰好 强烈推荐
    ([hit("R-STYLE-B01", DimensionCode.D2, +7.0, severity=Severity.boost),
      hit("R-PROP-B01", DimensionCode.D3, +3.0, severity=Severity.boost)],
     Verdict.strongly_recommended),
    # 7999 -> 适合（边界）
    ([hit("R-STYLE-B01", DimensionCode.D2, +7.0, severity=Severity.boost),
      hit("R-PROP-B01", DimensionCode.D3, +2.99, severity=Severity.boost)],
     Verdict.suitable),
    # 6500 -> 适合
    ([hit("R-STYLE-001", DimensionCode.D2, -5.0)], Verdict.suitable),
    # 6499 -> 一般（边界）
    ([hit("R-STYLE-001", DimensionCode.D2, -5.01)], Verdict.neutral),
    # 5000 -> 一般
    ([hit("R-STYLE-001", DimensionCode.D2, -18.0), hit("R-COLOR-001", DimensionCode.D1, -2.0)],
     Verdict.neutral),
    # 4999 -> 不适合（边界）
    ([hit("R-STYLE-001", DimensionCode.D2, -18.0), hit("R-COLOR-001", DimensionCode.D1, -2.01)],
     Verdict.unsuitable),
])
def test_verdict_thresholds(hits, expected):
    r = score(hits, feats())
    assert r.verdict == expected, f"total={r.total_centi} 期望 {expected}"


# ---------------------------------------------------------------- 确定性


def test_input_order_does_not_change_result():
    hits = [
        hit("R-STYLE-003", DimensionCode.D2, -3.0),
        hit("R-COLOR-002", DimensionCode.D1, -4.0),
        hit("R-DET-001", DimensionCode.D5, -2.0),
        hit("R-COLOR-001", DimensionCode.D1, -5.0),
    ]
    a = score(list(hits), feats())
    b = score(list(reversed(hits)), feats())
    assert a.total_centi == b.total_centi
    assert [h.rule_id for h in a.hits] == [h.rule_id for h in b.hits]
    assert [e.rule_id for e in a.ledger] == [e.rule_id for e in b.ledger]


def test_repeated_runs_are_bit_identical():
    hits = [hit("R-COLOR-001", DimensionCode.D1, -7.8),
            hit("R-STYLE-002", DimensionCode.D2, +5.0, severity=Severity.boost)]
    outs = [score(hits, feats()).model_dump() for _ in range(3)]
    assert outs[0] == outs[1] == outs[2]


def test_category_then_rule_id_ordering():
    hits = [
        hit("R-DET-002", DimensionCode.D5, -1.0),
        hit("R-OCC-001", DimensionCode.D4, -1.0),
        hit("R-PROP-001", DimensionCode.D3, -1.0),
        hit("R-STYLE-001", DimensionCode.D2, -1.0),
        hit("R-COLOR-002", DimensionCode.D1, -1.0),
    ]
    r = score(hits, feats())
    assert [h.rule_id for h in r.hits] == [
        "R-COLOR-002", "R-STYLE-001", "R-PROP-001", "R-OCC-001", "R-DET-002"]


# ---------------------------------------------------------------- 账本一致性


def test_ledger_reconciles_with_total():
    hits = [
        hit("R-COLOR-001", DimensionCode.D1, -7.8),
        hit("R-STYLE-004", DimensionCode.D2, -6.4),
        hit("R-OCC-001", DimensionCode.D4, -6.8, severity=Severity.hard),
        hit("R-DET-003", DimensionCode.D5, +1.5, severity=Severity.boost),
    ]
    r = score(hits, feats())
    applied_sum = sum(int(round(e.delta_applied * 100)) for e in r.ledger)
    assert r.base_centi + applied_sum == r.total_centi
    assert sum(d.score_centi for d in r.dimensions) == r.total_centi
