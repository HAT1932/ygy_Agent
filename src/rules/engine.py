"""规则引擎 —— docs/03 §8 Step 1–3（门控 / 求值 / 抑制 / 互斥 / 确定性排序 / 渲染）。

刻意**不做**的事（都属于 src/scoring.py 的职责边界）：
  - 预算截断（delta_applied / clipped 由 scoring 计算，本模块留 0 / False）；
  - 总分、维度分、verdict、confidence。

本模块只保证「哪些规则命中、按什么确定序命中、每笔命中怎么渲染成可直出 UI 的文本」。

执行顺序（§8 与 task 规格）：
  1. 门控：``features.assess(dim) < MIN_ASSESSABILITY`` 的维度，其规则全部跳过（含 hard）；
     另按 §4.6.1：``outfit.occasion == unspecified`` 时 D4 整组降级；
  2. 求值：``evaluate(condition, ctx)`` 为真即命中；
  3. 抑制：命中集合里 A.suppresses 命中 B → 丢弃 B（boost 不可被压制，§10.3 第 5 条）；
  4. 互斥：同 mutex_group 只保留 |delta| 最大者（并列取 rule_id 小者）；
  5. 排序：``(CATEGORY_ORDER[category], rule_id)`` —— 复用 src.scoring 的唯一实现；
  6. 渲染：explain → explanation；非 boost → suggestion；boost → reinforce；evidence 拼可见句。
"""
from __future__ import annotations

from typing import Any, Mapping

from ..contracts import (
    DimensionCode,
    Features,
    Outfit,
    Rule,
    RuleHit,
    Severity,
    StyleProfile,
)
from ..scoring import CATEGORY_ORDER, MIN_ASSESSABILITY, sort_key
from .context import build_context
from .evaluator import evaluate
from .templating import format_value, render_template

__all__ = ["RuleEngine", "resolve_mutex", "resolve_suppression"]


class RuleEngine:
    """确定性规则引擎：同 (rules, outfit, profile, features) 必得同输出（INV-1）。"""

    def __init__(self, rules: list[Rule]) -> None:
        # 装载期冻结顺序：rule_id 排序，之后任何遍历都不依赖 dict/set 迭代顺序
        self._rules: tuple[Rule, ...] = tuple(sorted(rules, key=lambda rule: rule.rule_id))
        # 冲突消解关系随规则定义冻结（RuleHit 契约里没有 suppresses/mutex_group 字段）
        self._suppress: dict[str, tuple[str, ...]] = {
            rule.rule_id: tuple(sorted(rule.suppresses))
            for rule in self._rules if rule.suppresses
        }
        self._mutex: dict[str, str] = {
            rule.rule_id: rule.mutex_group
            for rule in self._rules if rule.mutex_group
        }

    @property
    def rules(self) -> tuple[Rule, ...]:
        return self._rules

    # ------------------------------------------------------------------
    def run(
        self,
        outfit: Outfit,
        profile: StyleProfile,
        features: Features,
    ) -> list[RuleHit]:
        ctx = build_context(outfit, profile, features)

        hits: list[RuleHit] = []
        for rule in self._rules:
            if rule.status != "active":
                continue
            dimension: DimensionCode = rule.score_impact.dimension
            # Step 1a：证据可用度门控（含 hard；缺证据 ≠ 算通过，维度保持 base_d）
            if features.assess(dimension) < MIN_ASSESSABILITY:
                continue
            # Step 1b：§4.6.1 场合未声明 → D4 整组不执行，不惩罚用户
            if dimension is DimensionCode.D4 and ctx.get("outfit.occasion") == "unspecified":
                continue
            # Step 2：确定性求值
            if not evaluate(rule.condition, ctx):
                continue
            hits.append(_make_hit(rule, ctx))

        hits = resolve_suppression(hits, self._suppress)
        hits = resolve_mutex(hits, self._mutex)
        hits.sort(key=sort_key)
        return hits


# --------------------------------------------------------------------------
# 冲突消解
# --------------------------------------------------------------------------


def resolve_suppression(
    hits: list[RuleHit],
    suppress_index: Mapping[str, tuple[str, ...]],
) -> list[RuleHit]:
    """§10.2/§10.3：被命中规则的 ``suppresses`` 指名者丢弃；boost 不可被压制。

    本 ruleset 的压制图无链（loader 的 ``DSL_SUPPRESS_CHAIN`` 强制），因此单遍即可：
    压制关系只作用于「原始命中集合」，被压制者的出边不再生效。
    """
    by_id = {hit.rule_id: hit for hit in hits}
    suppressed: set[str] = set()
    for hit in hits:
        for target in suppress_index.get(hit.rule_id, ()):
            victim = by_id.get(target)
            if victim is not None and victim.severity is not Severity.boost:
                suppressed.add(target)
    return [hit for hit in hits if hit.rule_id not in suppressed]


def resolve_mutex(
    hits: list[RuleHit],
    mutex_index: Mapping[str, str],
) -> list[RuleHit]:
    """同 mutex_group 只保留 |delta_schema| 最大者；并列取 rule_id 字典序小者。"""
    best: dict[str, RuleHit] = {}
    keep: list[RuleHit] = []
    for hit in hits:
        group = mutex_index.get(hit.rule_id)
        if not group:
            keep.append(hit)
            continue
        current = best.get(group)
        if current is None or (-abs(hit.delta_schema), hit.rule_id) < (
                -abs(current.delta_schema), current.rule_id):
            best[group] = hit
    keep.extend(best[group] for group in best)
    return keep


# --------------------------------------------------------------------------
# 渲染（INV-3 / INV-5）
# --------------------------------------------------------------------------


def _evidence_text(rule: Rule, ctx: dict[str, Any]) -> str:
    parts = [f"{field}={format_value(ctx.get(field))}" for field in rule.evidence_fields]
    return "；".join(parts)


def _triggered_value(rule: Rule, ctx: dict[str, Any]) -> Any:
    for field in rule.evidence_fields:
        if field.startswith("ctx."):
            continue
        return ctx.get(field)
    return None


def _make_hit(rule: Rule, ctx: dict[str, Any]) -> RuleHit:
    explanation = render_template(rule.explain_template, ctx, rule_id=rule.rule_id)
    suggestion = None
    reinforce = None
    if rule.severity is Severity.boost:
        assert rule.reinforce_template is not None  # contracts 已校验
        reinforce = render_template(rule.reinforce_template, ctx, rule_id=rule.rule_id)
    elif rule.suggestion_template:
        suggestion = render_template(rule.suggestion_template, ctx, rule_id=rule.rule_id)
    return RuleHit(
        rule_id=rule.rule_id,
        title=rule.title,
        category=rule.category,
        severity=rule.severity,
        dimension=rule.score_impact.dimension,
        delta_schema=rule.score_impact.delta,
        delta_applied=0.0,   # 预算截断由 src/scoring.py 负责
        clipped=False,
        triggered_value=_triggered_value(rule, ctx),
        evidence=_evidence_text(rule, ctx),
        explanation=explanation,
        suggestion=suggestion,
        reinforce=reinforce,
        render_priority=rule.render_priority,
    )
