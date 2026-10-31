"""规则资产 + DSL 引擎的验收测试（docs/03 §11）。

覆盖：
1. 50 条规则全部可加载，分类/严重度/delta 与 docs/03 §9 一致；
2. 每条规则的 fixture 正负用例通过（含条件边界与「打掉单个原子」的负例）；
3. 模板：变量已声明、渲染无未定义变量、自足性 lint（无内部术语/无指代词/有动作动词/长度）；
4. 门控：维度 assessability < MIN_ASSESSABILITY 时该维度规则全部跳过（含 hard）；
5. 抑制 / 互斥；
6. 确定性：同一输入两次 run 逐位相等，且顺序为 (CATEGORY_ORDER, rule_id)。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from src.contracts import (
    Category,
    Color,
    Condition,
    DimensionCode,
    EffectType,
    Features,
    Garment,
    Outfit,
    Rule,
    RuleHit,
    RuleTestCases,
    ScoreImpact,
    Severity,
    Slot,
    StyleProfile,
    StyleTagScore,
)
from src.rules import (
    KNOWN_FIELDS,
    RuleEngine,
    build_context,
    evaluate,
    lint_rules,
    load_rules,
    render_template,
    ruleset_meta,
)
from src.rules.engine import resolve_mutex, resolve_suppression
from src.rules.loader import iter_condition_fields
from src.rules.templating import TemplateError, template_variables
from src.scoring import CATEGORY_ORDER, MIN_ASSESSABILITY, sort_key

ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = ROOT / "rules"
FIXTURES_DIR = ROOT / "fixtures"

CONTRACT_SLOT = {
    "upper": Slot.upper,
    "lower": Slot.lower,
    "outer": Slot.outer,
    "dress": Slot.dress,
    "shoes": Slot.shoes,
    "accessory": Slot.accessory,
}
SEMANTIC_SLOT = {"upper": "top", "lower": "bottom", "outer": "outer",
                 "dress": "dress", "shoes": "shoes"}

# docs/03 §9.1–§9.5 的权威清单：(category, severity, delta)
EXPECTED: dict[str, tuple[Category, Severity, float]] = {
    "R-COLOR-001": (Category.COLOR, Severity.hard, -7.80),
    "R-COLOR-002": (Category.COLOR, Severity.soft, -4.00),
    "R-COLOR-003": (Category.COLOR, Severity.soft, -3.00),
    "R-COLOR-004": (Category.COLOR, Severity.soft, -3.00),
    "R-COLOR-005": (Category.COLOR, Severity.soft, -2.00),
    "R-COLOR-006": (Category.COLOR, Severity.soft, -2.00),
    "R-COLOR-007": (Category.COLOR, Severity.soft, -4.00),
    "R-COLOR-008": (Category.COLOR, Severity.soft, -3.00),
    "R-COLOR-009": (Category.COLOR, Severity.soft, -2.00),
    "R-COLOR-010": (Category.COLOR, Severity.soft, -2.00),
    "R-COLOR-011": (Category.COLOR, Severity.boost, 3.00),
    "R-COLOR-012": (Category.COLOR, Severity.boost, 4.00),
    "R-STYLE-001": (Category.STYLE, Severity.hard, -9.00),
    "R-STYLE-002": (Category.STYLE, Severity.soft, -5.00),
    "R-STYLE-003": (Category.STYLE, Severity.soft, -4.00),
    "R-STYLE-004": (Category.STYLE, Severity.soft, -5.00),
    "R-STYLE-005": (Category.STYLE, Severity.soft, -3.00),
    "R-STYLE-006": (Category.STYLE, Severity.soft, -4.00),
    "R-STYLE-007": (Category.STYLE, Severity.soft, -2.00),
    "R-STYLE-008": (Category.STYLE, Severity.boost, 4.00),
    "R-STYLE-009": (Category.STYLE, Severity.boost, 3.00),
    "R-STYLE-010": (Category.STYLE, Severity.boost, 3.00),
    "R-PROP-001": (Category.PROPORTION, Severity.soft, -4.00),
    "R-PROP-002": (Category.PROPORTION, Severity.soft, -3.00),
    "R-PROP-003": (Category.PROPORTION, Severity.soft, -2.00),
    "R-PROP-004": (Category.PROPORTION, Severity.soft, -3.00),
    "R-PROP-005": (Category.PROPORTION, Severity.soft, -3.00),
    "R-PROP-006": (Category.PROPORTION, Severity.soft, -3.00),
    "R-PROP-007": (Category.PROPORTION, Severity.soft, -2.00),
    "R-PROP-008": (Category.PROPORTION, Severity.soft, -2.00),
    "R-PROP-009": (Category.PROPORTION, Severity.boost, 4.00),
    "R-PROP-010": (Category.PROPORTION, Severity.boost, 3.00),
    "R-OCC-001": (Category.OCCASION, Severity.hard, -6.80),
    "R-OCC-002": (Category.OCCASION, Severity.soft, -5.00),
    "R-OCC-003": (Category.OCCASION, Severity.soft, -4.00),
    "R-OCC-004": (Category.OCCASION, Severity.soft, -3.00),
    "R-OCC-005": (Category.OCCASION, Severity.soft, -4.00),
    "R-OCC-006": (Category.OCCASION, Severity.soft, -3.00),
    "R-OCC-007": (Category.OCCASION, Severity.soft, -2.00),
    "R-OCC-008": (Category.OCCASION, Severity.soft, -3.00),
    "R-OCC-009": (Category.OCCASION, Severity.boost, 4.00),
    "R-OCC-010": (Category.OCCASION, Severity.boost, 3.00),
    "R-DET-001": (Category.DETAIL, Severity.soft, -3.00),
    "R-DET-002": (Category.DETAIL, Severity.soft, -3.00),
    "R-DET-003": (Category.DETAIL, Severity.soft, -3.00),
    "R-DET-004": (Category.DETAIL, Severity.soft, -2.00),
    "R-DET-005": (Category.DETAIL, Severity.soft, -2.00),
    "R-DET-006": (Category.DETAIL, Severity.soft, -2.00),
    "R-DET-007": (Category.DETAIL, Severity.boost, 3.00),
    "R-DET-008": (Category.DETAIL, Severity.boost, 4.00),
}

CATEGORY_DIM = {
    Category.COLOR: DimensionCode.D1,
    Category.STYLE: DimensionCode.D2,
    Category.PROPORTION: DimensionCode.D3,
    Category.OCCASION: DimensionCode.D4,
    Category.DETAIL: DimensionCode.D5,
}


# ============================================================
# fixtures 装载（YAML → contracts 对象；不依赖任何模型）
# ============================================================


def _semantic_key(item: dict) -> str:
    if item.get("key"):
        return str(item["key"])
    if item["slot"] != "accessory":
        return SEMANTIC_SLOT[item["slot"]]
    return "bag" if "包" in item["category"] else "accessory"


def build_case(case: dict, name: str) -> tuple[Outfit, StyleProfile, Features]:
    items: list[Garment] = []
    garment_overrides: dict[str, dict] = {}
    for index, item in enumerate(case["outfit"]["items"]):
        attrs = dict(item.get("attrs") or {})
        formality_01 = attrs.get("formality")
        model_formality = None
        if formality_01 is not None:
            # contracts.Garment.formality 是 int 1..5；0..1 的精确值走 Features.extra 覆盖
            model_formality = min(5, max(1, int(round(float(formality_01) * 4 + 1))))
        garment = Garment(
            garment_id=f"{name}-g{index}",
            slot=CONTRACT_SLOT[item["slot"]],
            category=item["category"],
            material=item.get("material"),
            fit=item.get("fit"),
            length=item.get("length"),
            pattern=item.get("pattern"),
            colors=[Color(**color) for color in item.get("colors") or []],
            style_tags=list(item.get("style_tags") or []),
            formality=model_formality,
        )
        items.append(garment)
        if attrs:
            garment_overrides[_semantic_key(item)] = attrs

    outfit = Outfit(
        outfit_id=f"{name}-outfit",
        items=items,
        occasion=case["outfit"].get("occasion"),
        season=case["outfit"].get("season"),
    )

    profile_data = case.get("profile") or {}
    top_styles = list(profile_data.get("top_styles") or [])
    weights = [0.5, 0.3, 0.2, 0.2, 0.2]
    profile = StyleProfile(
        profile_id=f"{name}-profile",
        tags=[StyleTagScore(tag=tag, weight=weights[min(i, 4)])
              for i, tag in enumerate(top_styles)],
        banned_styles=list(profile_data.get("forbidden_styles") or []),
        n_ref_outfits=int(profile_data.get("n_ref", 4)),
        confidence=0.9,
    )

    features_data = dict(case.get("features") or {})
    features = Features(
        style_cosine=float(features_data.pop("style_cosine", 0.70)),
        style_coverage=1.0,
        **{
            "occasion_fit_prob": float(features_data.pop("occasion_fit_prob", 0.80)),
            **features_data,
            "structure": dict(case.get("structure") or {}),
            "garment": garment_overrides,
            "profile": {"explore_tags": list(profile_data.get("explore_tags") or []),
                        "style_entropy": 0.4},
        },
    )
    return outfit, profile, features


def load_fixture(rule_id: str) -> dict:
    path = FIXTURES_DIR / f"{rule_id}.yaml"
    assert path.exists(), f"缺 fixture: {path}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def resolve_case(cases: list[dict], case_id: str) -> dict:
    for case in cases:
        if case["case_id"] == case_id:
            return case
    prefixed = [case for case in cases if case["case_id"].startswith(f"{case_id}-")]
    assert len(prefixed) == 1, f"用例 {case_id} 无法唯一定位，候选={[c['case_id'] for c in prefixed]}"
    return prefixed[0]


@pytest.fixture(scope="module")
def rules() -> list[Rule]:
    loaded = load_rules(RULES_DIR)
    assert len(loaded) == 50
    return loaded


@pytest.fixture(scope="module")
def engine(rules: list[Rule]) -> RuleEngine:
    return RuleEngine(rules)


def ctx_for(rule_id: str, case_id: str, dim: DimensionCode | None = None,
            assessability: float = 1.0) -> dict:
    fixture = load_fixture(rule_id)
    case = resolve_case(fixture["cases"], case_id)
    outfit, profile, features = build_case(case, f"{rule_id}-{case_id}")
    if dim is not None:
        features.assessability[dim.value] = assessability
    return build_context(outfit, profile, features)


# ============================================================
# 1. 规则清单与元信息
# ============================================================


def test_rule_count_and_distribution(rules: list[Rule]) -> None:
    assert len(rules) == 50
    counts = {category: 0 for category in Category}
    for rule in rules:
        counts[rule.category] += 1
    assert counts == {Category.COLOR: 12, Category.STYLE: 10,
                      Category.PROPORTION: 10, Category.OCCASION: 10, Category.DETAIL: 8}
    severity_counts = {severity: 0 for severity in Severity}
    for rule in rules:
        severity_counts[rule.severity] += 1
    assert severity_counts == {Severity.hard: 3, Severity.soft: 36, Severity.boost: 11}


def test_rule_ids_unique_and_sorted(rules: list[Rule]) -> None:
    ids = [rule.rule_id for rule in rules]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert set(ids) == set(EXPECTED)


def test_rule_delta_and_severity_match_docs(rules: list[Rule]) -> None:
    for rule in rules:
        category, severity, delta = EXPECTED[rule.rule_id]
        assert rule.category == category, rule.rule_id
        assert rule.severity == severity, rule.rule_id
        assert rule.effect == {Severity.hard: EffectType.veto,
                               Severity.soft: EffectType.penalty,
                               Severity.boost: EffectType.bonus}[severity]
        assert rule.score_impact.delta == pytest.approx(delta), rule.rule_id
        assert rule.score_impact.dimension == CATEGORY_DIM[category], rule.rule_id


def test_lint_is_clean_and_meta_stable(rules: list[Rule]) -> None:
    assert lint_rules(rules) == []
    meta = ruleset_meta(rules)
    assert meta["count"] == 50
    assert meta["version"] == "rs-2026.10.1"
    assert re.fullmatch(r"[0-9a-f]{64}", meta["sha256"])
    assert ruleset_meta(list(reversed(rules)))["sha256"] == meta["sha256"]


def test_suppression_and_mutex_tables(rules: list[Rule]) -> None:
    by_id = {rule.rule_id: rule for rule in rules}
    expected_suppresses = {
        "R-COLOR-012": ["R-COLOR-004"],
        "R-COLOR-011": ["R-COLOR-010"],
        "R-STYLE-002": ["R-STYLE-005"],
        "R-STYLE-004": ["R-STYLE-003"],
        "R-PROP-002": ["R-PROP-006"],
        "R-DET-002": ["R-DET-006"],
        "R-COLOR-008": ["R-COLOR-009"],
    }
    for rule_id, targets in expected_suppresses.items():
        assert by_id[rule_id].suppresses == targets, rule_id
    assert by_id["R-OCC-003"].mutex_group == "OCC_FORMALITY"
    assert by_id["R-OCC-004"].mutex_group == "OCC_FORMALITY"


# ============================================================
# 2. fixture 正负用例
# ============================================================


def test_every_rule_has_hit_and_miss_fixture(rules: list[Rule]) -> None:
    for rule in rules:
        fixture = load_fixture(rule.rule_id)
        assert fixture["rule_id"] == rule.rule_id
        expect = {case["expect"] for case in fixture["cases"]}
        assert "hit" in expect, rule.rule_id
        assert "miss" in expect, rule.rule_id
        # test_cases 指向的 fixture/case 必须真实存在
        for case_ref in rule.test_cases.positive:
            assert case_ref["fixture"] == rule.rule_id
            assert resolve_case(fixture["cases"], case_ref["case_id"])
        for case_ref in rule.test_cases.negative:
            assert case_ref["fixture"] == rule.rule_id
            assert resolve_case(fixture["cases"], case_ref["case_id"])


@pytest.mark.parametrize("rule_id", sorted(EXPECTED))
def test_rule_conditions_against_fixtures(rule_id: str, rules: list[Rule]) -> None:
    rule = next(rule for rule in rules if rule.rule_id == rule_id)
    fixture = load_fixture(rule_id)
    for case in fixture["cases"]:
        ctx = ctx_for(rule_id, case["case_id"])
        expected = case["expect"] == "hit"
        actual = evaluate(rule.condition, ctx)
        assert actual is expected, f"{rule_id}/{case['case_id']} 期望 {case['expect']}，实际 {actual}"


# ============================================================
# 3. 模板（INV-5 自足性）
# ============================================================


def test_template_variables_declared_in_evidence(rules: list[Rule]) -> None:
    for rule in rules:
        evidence = set(rule.evidence_fields)
        for template in (rule.explain_template, rule.suggestion_template,
                         rule.reinforce_template):
            if not template:
                continue
            for variable in template_variables(template):
                assert variable in evidence, f"{rule.rule_id}: 变量 {variable} 未声明"
                assert variable in KNOWN_FIELDS, f"{rule.rule_id}: 变量 {variable} 未登记"


def test_template_renders_without_undefined_variables(rules: list[Rule]) -> None:
    for rule in rules:
        fixture = load_fixture(rule.rule_id)
        hit_cases = [case for case in fixture["cases"] if case["expect"] == "hit"]
        assert hit_cases, rule.rule_id
        for case in hit_cases:
            ctx = ctx_for(rule.rule_id, case["case_id"])
            explanation = render_template(rule.explain_template, ctx, rule_id=rule.rule_id)
            assert explanation.strip()
            if rule.suggestion_template:
                assert render_template(rule.suggestion_template, ctx,
                                       rule_id=rule.rule_id).strip()
            if rule.reinforce_template:
                assert render_template(rule.reinforce_template, ctx,
                                       rule_id=rule.rule_id).strip()


def test_rendered_text_is_self_sufficient(rules: list[Rule]) -> None:
    banned = ["请参考", "根据以上", "如上所述", "综上", "详见", "正如图片",
              "你上传的", "上文", "下一节", "本次检测"]
    pronouns = ["它", "这件", "那件", "该单品", "上述", "此款"]
    internal = ["规则", "score", "delta", "veto", "维度"]
    actions = ["换", "加", "去掉", "塞入", "提高", "降低", "统一", "缩短", "改"]
    for rule in rules:
        fixture = load_fixture(rule.rule_id)
        for case in fixture["cases"]:
            if case["expect"] != "hit":
                continue
            ctx = ctx_for(rule.rule_id, case["case_id"])
            for template, kind in ((rule.explain_template, "explain"),
                                   (rule.suggestion_template, "suggestion"),
                                   (rule.reinforce_template, "reinforce")):
                if not template:
                    continue
                text = render_template(template, ctx, rule_id=rule.rule_id)
                where = f"{rule.rule_id}/{case['case_id']}/{kind}"
                assert len(text) <= 90, f"{where}: 渲染后 {len(text)} 字"
                assert text.endswith(("。", "！")), where
                assert rule.rule_id not in text, where
                assert not re.search(r"(?<![A-Za-z0-9])D[1-5](?![0-9])", text), where
                for word in banned + pronouns + internal:
                    assert word not in text, f"{where}: 出现「{word}」"
                if kind == "suggestion" and rule.severity is not Severity.boost:
                    assert any(verb in text for verb in actions), f"{where}: 缺动作动词"


def test_undefined_template_variable_raises() -> None:
    with pytest.raises(TemplateError):
        render_template("主色 {outfit.colors.not_registered} 个。", {}, rule_id="R-COLOR-999")


# ============================================================
# 4. 引擎：门控 / 抑制 / 互斥
# ============================================================


GATE_CASES = {
    DimensionCode.D1: "R-COLOR-001",
    DimensionCode.D2: "R-STYLE-001",
    DimensionCode.D3: "R-PROP-004",
    DimensionCode.D4: "R-OCC-003",
    DimensionCode.D5: "R-DET-001",
}


@pytest.mark.parametrize("dimension", sorted(GATE_CASES, key=lambda item: item.value))
def test_dimension_gating_skips_all_rules(dimension: DimensionCode,
                                          rules: list[Rule]) -> None:
    rule_id = GATE_CASES[dimension]
    fixture = load_fixture(rule_id)
    case = next(case for case in fixture["cases"] if case["expect"] == "hit")
    outfit, profile, features = build_case(case, f"gate-{rule_id}")

    baseline = RuleEngine(rules).run(outfit, profile, features)
    assert any(hit.dimension is dimension for hit in baseline), rule_id

    features.assessability[dimension.value] = MIN_ASSESSABILITY - 0.1
    gated = RuleEngine(rules).run(outfit, profile, features)
    assert [hit for hit in gated if hit.dimension is dimension] == [], dimension


def test_suppression_drops_less_specific_rule(engine: RuleEngine) -> None:
    fixture = load_fixture("R-COLOR-008")
    case = next(case for case in fixture["cases"] if case["expect"] == "hit")
    outfit, profile, features = build_case(case, "suppress-color")
    ctx = build_context(outfit, profile, features)
    rule_009 = next(rule for rule in engine.rules if rule.rule_id == "R-COLOR-009")
    assert evaluate(rule_009.condition, ctx) is True  # 条件确实为真

    hits = engine.run(outfit, profile, features)
    ids = [hit.rule_id for hit in hits]
    assert "R-COLOR-008" in ids
    assert "R-COLOR-009" not in ids  # 被更具体的图案混用压制


def test_suppression_uses_more_severe_style_rule(engine: RuleEngine) -> None:
    fixture = load_fixture("R-STYLE-005")
    case = next(case for case in fixture["cases"] if case["expect"] == "hit")
    outfit, profile, features = build_case(case, "suppress-style")
    ctx = build_context(outfit, profile, features)
    rule_002 = next(rule for rule in engine.rules if rule.rule_id == "R-STYLE-002")
    rule_005 = next(rule for rule in engine.rules if rule.rule_id == "R-STYLE-005")
    assert evaluate(rule_002.condition, ctx) is True
    assert evaluate(rule_005.condition, ctx) is True

    ids = [hit.rule_id for hit in engine.run(outfit, profile, features)]
    assert "R-STYLE-002" in ids
    assert "R-STYLE-005" not in ids


def _hit(rule_id: str, delta: float, severity: Severity = Severity.soft) -> RuleHit:
    category = Category.COLOR
    dimension = DimensionCode.D1
    return RuleHit(rule_id=rule_id, title=rule_id, category=category,
                   severity=severity, dimension=dimension, delta_schema=delta,
                   explanation="测试", evidence="测试")


def test_resolve_suppression_never_drops_boost() -> None:
    hits = [_hit("R-COLOR-900", -4.0), _hit("R-COLOR-011", 3.0, Severity.boost)]
    kept = resolve_suppression(hits, {"R-COLOR-900": ("R-COLOR-011",)})
    assert [hit.rule_id for hit in kept] == ["R-COLOR-900", "R-COLOR-011"]


def test_resolve_mutex_keeps_largest_then_smallest_id() -> None:
    hits = [
        _hit("R-COLOR-901", -2.0),
        _hit("R-COLOR-903", -3.0),
        _hit("R-COLOR-902", -2.0),
    ]
    kept = sorted(resolve_mutex(hits, {"R-COLOR-901": "G", "R-COLOR-902": "G",
                                       "R-COLOR-903": "G"}),
                  key=lambda hit: hit.rule_id)
    assert [hit.rule_id for hit in kept] == ["R-COLOR-903"]
    tied = sorted(resolve_mutex([_hit("R-COLOR-902", -2.0), _hit("R-COLOR-901", -2.0)],
                                {"R-COLOR-901": "G2", "R-COLOR-902": "G2"}),
                  key=lambda hit: hit.rule_id)
    assert [hit.rule_id for hit in tied] == ["R-COLOR-901"]


def _dummy_rule(rule_id: str, delta: float, condition: Condition,
                suppresses: list[str] | None = None,
                mutex_group: str | None = None) -> Rule:
    return Rule(
        rule_id=rule_id,
        category=Category.COLOR,
        title="测试规则",
        severity=Severity.soft,
        scope="outfit",
        condition=condition,
        effect=EffectType.penalty,
        score_impact=ScoreImpact(dimension=DimensionCode.D1, delta=delta),
        explain_template="这套搭配有 {outfit.garment_count} 件单品。",
        suggestion_template="把下装换成直筒版型。",
        evidence_fields=["outfit.garment_count"],
        suppresses=suppresses or [],
        mutex_group=mutex_group,
        test_cases=RuleTestCases(positive=[{"fixture": "x"}], negative=[{"fixture": "y"}]),
    )


def test_engine_mutex_through_run() -> None:
    condition = Condition.model_validate({"field": "outfit.garment_count", "op": "gte", "value": 1})
    rules = [
        _dummy_rule("R-COLOR-901", -2.0, condition, mutex_group="M"),
        _dummy_rule("R-COLOR-902", -3.0, condition, mutex_group="M"),
        _dummy_rule("R-COLOR-903", -1.0, condition),
    ]
    engine = RuleEngine(rules)
    outfit = Outfit(outfit_id="mutex", items=[Garment(garment_id="g", slot=Slot.upper,
                                                      category="衬衫")])
    hits = engine.run(outfit, StyleProfile(profile_id="p"), Features())
    assert [hit.rule_id for hit in hits] == ["R-COLOR-902", "R-COLOR-903"]


def test_suppression_cycle_is_lint_error() -> None:
    condition = Condition.model_validate({"field": "outfit.garment_count", "op": "gte", "value": 1})
    rules = [
        _dummy_rule("R-COLOR-901", -2.0, condition, suppresses=["R-COLOR-902"]),
        _dummy_rule("R-COLOR-902", -2.0, condition, suppresses=["R-COLOR-901"]),
    ]
    errors = lint_rules(rules)
    assert any("DSL_SUPPRESS_CHAIN" in error for error in errors)


def test_engine_skips_d4_when_occasion_unspecified(engine: RuleEngine) -> None:
    fixture = load_fixture("R-OCC-009")
    case = next(case for case in fixture["cases"] if case["expect"] == "hit")
    outfit, profile, features = build_case(case, "occ-unspecified")
    outfit = outfit.model_copy(update={"occasion": "unspecified"})
    hits = engine.run(outfit, profile, features)
    assert [hit for hit in hits if hit.dimension is DimensionCode.D4] == []


# ============================================================
# 5. 确定性 / 排序 / 命中契约
# ============================================================


def test_engine_is_deterministic(engine: RuleEngine) -> None:
    fixture = load_fixture("R-COLOR-001")
    case = next(case for case in fixture["cases"] if case["expect"] == "hit")
    outfit, profile, features = build_case(case, "determinism")
    first = [hit.model_dump() for hit in engine.run(outfit, profile, features)]
    second = [hit.model_dump() for hit in engine.run(outfit, profile, features)]
    assert first == second
    assert first, "该 fixture 至少应命中一条规则"


def test_hits_are_sorted_and_scoring_ready(engine: RuleEngine) -> None:
    fixture = load_fixture("R-COLOR-001")
    case = next(case for case in fixture["cases"] if case["expect"] == "hit")
    outfit, profile, features = build_case(case, "sorted")
    hits = engine.run(outfit, profile, features)
    assert [sort_key(hit) for hit in hits] == sorted(sort_key(hit) for hit in hits)
    for hit in hits:
        assert hit.delta_applied == 0.0
        assert hit.clipped is False
        assert hit.evidence
        assert hit.explanation
        assert hit.triggered_value is not None
        if hit.severity is Severity.boost:
            assert hit.reinforce and hit.suggestion is None
        else:
            assert hit.suggestion and hit.reinforce is None


def test_known_fields_covers_all_referenced_fields(rules: list[Rule]) -> None:
    referenced: set[str] = set()
    for rule in rules:
        referenced |= set(iter_condition_fields(rule.condition))
        referenced |= set(rule.evidence_fields)
        for template in (rule.explain_template, rule.suggestion_template,
                         rule.reinforce_template):
            if template:
                referenced |= set(template_variables(template))
    assert referenced <= KNOWN_FIELDS
    ctx = ctx_for("R-COLOR-001", "pos-01")
    assert referenced <= set(ctx), sorted(referenced - set(ctx))


# ============================================================
# 6. DSL 求值器（封闭 op 集合）
# ============================================================


def _cond(payload: dict) -> Condition:
    return Condition.model_validate(payload)


def test_evaluator_supports_frozen_ops() -> None:
    ctx = {
        "num": 0.9,
        "low": 0.2,
        "text": "conflicting",
        "tags": ["极简", "法式"],
        "flag": True,
        "none": None,
    }
    assert evaluate(_cond({"field": "num", "op": "gt", "value": 0.85}), ctx)
    assert not evaluate(_cond({"field": "num", "op": "lt", "value": 0.85}), ctx)
    assert evaluate(_cond({"field": "num", "op": "between", "value": [0.9, 1.0]}), ctx)
    assert evaluate(_cond({"field": "text", "op": "eq", "value": "conflicting"}), ctx)
    assert evaluate(_cond({"field": "text", "op": "ne", "value": "balanced"}), ctx)
    assert evaluate(_cond({"field": "text", "op": "in", "value": ["conflicting", "x"]}), ctx)
    assert evaluate(_cond({"field": "text", "op": "not_in", "value": ["x"]}), ctx)
    assert evaluate(_cond({"field": "tags", "op": "contains_any", "value": ["法式"]}), ctx)
    assert evaluate(_cond({"field": "tags", "op": "contains_all", "value": ["极简", "法式"]}), ctx)
    assert evaluate(_cond({"field": "tags", "op": "contains_none", "value": ["甜美"]}), ctx)
    assert evaluate(_cond({"field": "flag", "op": "is_true"}), ctx)
    assert evaluate(_cond({"field": "none", "op": "is_false"}), {**ctx, "none": False})
    assert evaluate(_cond({"field": "num", "op": "abs_diff_gt", "other_field": "low",
                           "value": 0.5}), ctx)
    assert evaluate(_cond({"field": "num", "op": "abs_diff_lt", "other_field": "low",
                           "value": 1.0}), ctx)
    assert evaluate(_cond({"field": "num", "op": "exists"}), ctx)
    assert not evaluate(_cond({"field": "missing", "op": "exists"}), ctx)
    assert evaluate(_cond({"all": [{"field": "num", "op": "gte", "value": 0.9},
                                   {"field": "flag", "op": "is_true"}]}), ctx)
    assert evaluate(_cond({"any": [{"field": "flag", "op": "is_false"},
                                   {"field": "flag", "op": "is_true"}]}), ctx)
    assert evaluate(_cond({"not": {"field": "flag", "op": "is_false"}}), ctx)


def test_missing_field_never_matches() -> None:
    ctx = {"outfit.garment_count": 3}
    assert not evaluate(_cond({"field": "garment.top.fit", "op": "eq", "value": "loose"}), ctx)
    assert not evaluate(_cond({"field": "outfit.colors.hue_span", "op": "gt", "value": 0}), ctx)


# ============================================================
# 7. 装载器的失败模式（防止坏规则静默上线）
# ============================================================


def test_load_rules_rejects_bad_rule_id(tmp_path: Path) -> None:
    payload = {
        "rule_id": "R-COLOR-1",
        "category": "COLOR",
        "title": "坏 ID",
        "severity": "soft",
        "scope": "outfit",
        "condition": {"field": "outfit.garment_count", "op": "gte", "value": 1},
        "effect": "penalty",
        "score_impact": {"dimension": "D1", "delta": -2.0},
        "explain_template": "这套搭配有 {outfit.garment_count} 件单品。",
        "suggestion_template": "把下装换成直筒版型。",
        "evidence_fields": ["outfit.garment_count"],
        "test_cases": {"positive": [{"fixture": "x"}], "negative": [{"fixture": "y"}]},
    }
    (tmp_path / "bad.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="lint"):
        load_rules(tmp_path)


def test_load_rules_rejects_unknown_field(tmp_path: Path) -> None:
    payload = {
        "rule_id": "R-COLOR-777",
        "category": "COLOR",
        "title": "未知字段",
        "severity": "soft",
        "scope": "outfit",
        "condition": {"field": "outfit.colors.not_a_field", "op": "gte", "value": 1},
        "effect": "penalty",
        "score_impact": {"dimension": "D1", "delta": -2.0},
        "explain_template": "这套搭配有未知问题。",
        "suggestion_template": "把下装换成直筒版型。",
        "evidence_fields": ["outfit.colors.not_a_field"],
        "test_cases": {"positive": [{"fixture": "x"}], "negative": [{"fixture": "y"}]},
    }
    (tmp_path / "bad.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="DSL_UNKNOWN_FIELD"):
        load_rules(tmp_path)
