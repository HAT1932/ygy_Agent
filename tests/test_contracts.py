"""契约层不变量测试 —— 守护「分数可复现」与「Report 自洽」。

如果这些用例挂了，说明用户看到的分数无法被审计，属于产品事故（docs/00-baseline §5.3）。
"""
from __future__ import annotations

import pytest

from src.contracts import (
    BASE_SCORE_CENTI,
    Condition,
    Color,
    DimensionCode,
    DimensionScore,
    EffectType,
    Rule,
    RuleHit,
    Report,
    ScoreImpact,
    Severity,
    Category,
    Verdict,
    verdict_of,
)


def _dims(centi: list[int]) -> list[DimensionScore]:
    from src.contracts import DIMENSIONS
    out = []
    for spec, sc in zip(DIMENSIONS, centi):
        out.append(DimensionScore(
            code=spec.code, name=spec.name, score=sc / 100, max=spec.max_centi / 100,
            weight=spec.weight, base=spec.base_centi / 100, assessability=1.0,
            penalty_used=max(0, (spec.base_centi - sc)) / 100,
            boost_used=max(0, (sc - spec.base_centi)) / 100,
            score_centi=sc, base_centi=spec.base_centi, max_centi=spec.max_centi,
        ))
    return out


# ---------------------------------------------------------------- 颜色推导


@pytest.mark.parametrize("hexv,expect_hue", [
    ("#FF0000", 0.0),     # 红（hue 环绕：0 等价于 360）
    ("#00FF00", 120.0),   # 绿
    ("#0000FF", 240.0),   # 蓝
])
def test_color_derives_hsv_from_hex(hexv, expect_hue):
    c = Color(hex=hexv)
    # 允许环绕误差
    diff = abs(c.hue - expect_hue)
    assert min(diff, abs(diff - 360.0)) < 2.0
    assert c.hex.startswith("#")


def test_color_accepts_hex_without_hash():
    c = Color(hex="AABBCC")
    assert c.hex == "#AABBCC"
    assert 0.0 <= c.lightness <= 1.0


# ---------------------------------------------------------------- 判定阈值


@pytest.mark.parametrize("centi,vetoed,expected", [
    (8000, False, Verdict.strongly_recommended),
    (7999, False, Verdict.suitable),
    (6500, False, Verdict.suitable),
    (6499, False, Verdict.neutral),
    (5000, False, Verdict.neutral),
    (4999, False, Verdict.unsuitable),
    (10000, True, Verdict.unsuitable),   # veto 优先于分数
])
def test_verdict_boundaries(centi, vetoed, expected):
    assert verdict_of(centi, vetoed) == expected


# ---------------------------------------------------------------- 条件 DSL


def test_condition_leaf_and_combinators():
    leaf = Condition(field="outfit.colors.hue_span", op="gt", value=120)
    assert leaf.kind == "leaf"
    combo = Condition(all=[leaf, Condition(field="outfit.slots.count", op="gte", value=3)])
    assert combo.kind == "all"
    neg = Condition(**{"not": leaf})
    assert neg.kind == "not"


def test_condition_rejects_unknown_op():
    with pytest.raises(ValueError):
        Condition(field="x", op="wat", value=1)


def test_condition_rejects_mixed_form():
    with pytest.raises(ValueError):
        Condition(all=[Condition(field="a", op="eq", value=1)], field="b", op="eq", value=2)


def test_abs_diff_requires_other_field():
    with pytest.raises(ValueError):
        Condition(field="a.hue", op="abs_diff_gt", value=120)
    ok = Condition(field="a.hue", op="abs_diff_gt", other_field="b.hue", value=120)
    assert ok.kind == "leaf"


# ---------------------------------------------------------------- 规则一致性


def _rule_kwargs(**over) -> dict:
    base = dict(
        rule_id="R-COLOR-001", category=Category.COLOR, title="撞色",
        severity=Severity.soft, effect=EffectType.penalty,
        condition=Condition(field="outfit.colors.hue_span", op="gt", value=120),
        score_impact=ScoreImpact(dimension=DimensionCode.D1, delta=-7.8),
        explain_template="色相跨度达 {outfit.colors.hue_span|deg}，冲突明显。",
        suggestion_template="把下装换成{alt_color_family}系。",
    )
    base.update(over)
    return base


def test_rule_ok():
    r = Rule(**_rule_kwargs())
    assert r.severity == Severity.soft


def test_hard_rule_must_veto():
    with pytest.raises(ValueError):
        Rule(**_rule_kwargs(severity=Severity.hard, effect=EffectType.penalty))


def test_penalty_rule_needs_suggestion():
    with pytest.raises(ValueError):
        Rule(**_rule_kwargs(suggestion_template=None))


def test_boost_rule_needs_reinforce_and_positive_delta():
    with pytest.raises(ValueError):
        Rule(**_rule_kwargs(severity=Severity.boost, effect=EffectType.bonus,
                            score_impact=ScoreImpact(dimension=DimensionCode.D1, delta=2.0),
                            reinforce_template=None, suggestion_template=None))
    ok = Rule(**_rule_kwargs(severity=Severity.boost, effect=EffectType.bonus,
                             score_impact=ScoreImpact(dimension=DimensionCode.D1, delta=2.0),
                             reinforce_template="配色呼应到位，保持。", suggestion_template=None))
    assert ok.effect == EffectType.bonus


# ---------------------------------------------------------------- Report 不变量


def _report(**over) -> Report:
    total_centi = 7879
    kw = dict(
        report_id="rpt_t", outfit_id="oft_t", profile_id="prf_t",
        verdict=Verdict.suitable, vetoed=False,
        total_score=round(total_centi / 100, 2), total_centi=total_centi,
        dimension_scores=_dims([1579, 1800, 1300, 1300, 900]),
        rule_hits=[
            RuleHit(rule_id="R-COLOR-001", title="t", category=Category.COLOR,
                    severity=Severity.soft, dimension=DimensionCode.D1,
                    delta_schema=-7.8, delta_applied=-7.8),
            RuleHit(rule_id="R-STYLE-002", title="t", category=Category.STYLE,
                    severity=Severity.boost, dimension=DimensionCode.D2,
                    delta_schema=5.0, delta_applied=5.0),
        ],
    )
    # 让维度分与 (base + Σdelta) 对齐：7000 - 780 + 500 = 6720 -> 重新构造
    kw.update(over)
    return Report(**kw)


def test_report_accepts_consistent_ledger():
    # base 7000 + (-780) + (+500) = 6720
    dims = _dims([1560, 1800 - 780 + 500, 1320, 1360, 960])
    r = Report(
        report_id="rpt_ok", outfit_id="o", profile_id="p",
        verdict=Verdict.suitable, total_score=67.2, total_centi=6720,
        dimension_scores=dims,
        rule_hits=[
            RuleHit(rule_id="R-COLOR-001", title="t", category=Category.COLOR,
                    severity=Severity.soft, dimension=DimensionCode.D1,
                    delta_schema=-7.8, delta_applied=-7.8),
            RuleHit(rule_id="R-STYLE-002", title="t", category=Category.STYLE,
                    severity=Severity.boost, dimension=DimensionCode.D2,
                    delta_schema=5.0, delta_applied=5.0),
        ],
    )
    assert r.total_centi == 6720
    assert r.base_centi == BASE_SCORE_CENTI


def test_report_rejects_inv2_violation():
    dims = _dims([1560, 1800, 1320, 1360, 960])
    with pytest.raises(ValueError, match="INV-2"):
        Report(
            report_id="rpt_bad", outfit_id="o", profile_id="p",
            verdict=Verdict.suitable, total_score=80.0, total_centi=8000,
            dimension_scores=dims,
            rule_hits=[],
        )


def test_report_rejects_dimension_sum_mismatch():
    # 维度分之和 7100，但 total_centi 7000 且无生效 delta → INV-2 通过但维度和不符
    dims = _dims([1660, 1800, 1320, 1360, 960])   # 和 = 7100
    assert sum(d.score_centi for d in dims) == 7100
    with pytest.raises(ValueError, match="维度分之和"):
        Report(
            report_id="rpt_bad2", outfit_id="o", profile_id="p",
            verdict=Verdict.suitable, total_score=70.0, total_centi=7000,
            dimension_scores=dims,
            rule_hits=[],
        )


def test_report_rejects_veto_without_unsuitable():
    dims = _dims([1560, 1800, 1320, 1360, 960])
    with pytest.raises(ValueError, match="vetoed"):
        Report(
            report_id="rpt_bad3", outfit_id="o", profile_id="p",
            verdict=Verdict.strongly_recommended, vetoed=True,
            total_score=70.0, total_centi=7000,
            dimension_scores=dims, rule_hits=[],
        )
