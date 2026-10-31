"""规则资产与 DSL 引擎的公开入口。

``rules/`` 是内容（搭配师编辑），本包是代码（工程维护），两者刻意分离。

对外契约（其他模块按此调用，签名冻结）::

    load_rules(rules_dir) -> list[Rule]
    ruleset_meta(rules) -> {"version", "sha256", "count"}
    build_context(outfit, profile, features) -> dict[str, Any]
    evaluate(condition, ctx) -> bool
    render_template(template, ctx, rule_id="") -> str
    RuleEngine(rules).run(outfit, profile, features) -> list[RuleHit]
"""
from __future__ import annotations

from .context import KNOWN_FIELDS, build_context
from .engine import RuleEngine
from .evaluator import evaluate
from .loader import DEFAULT_RULES_DIR, lint_rules, load_rules, ruleset_meta
from .templating import TemplateError, render_template

__all__ = [
    "DEFAULT_RULES_DIR",
    "KNOWN_FIELDS",
    "RuleEngine",
    "TemplateError",
    "build_context",
    "evaluate",
    "lint_rules",
    "load_rules",
    "render_template",
    "ruleset_meta",
]
