"""规则资产装载与 lint（docs/03 §6.4 / §10 / §11 的 CI 门禁）。

``rules/`` 是**内容**（搭配师编辑），本模块是**代码**：只负责读 YAML、校验、排序、冻结指纹。

lint 覆盖（docs/03 §6.4 + §9.7.4 + §11.1 中对本任务可达的部分）：
  DSL_UNKNOWN_FIELD      字段不在 §7 字段字典 / §7.7 预计算字段 / ctx.* 白名单
  DSL_CATEGORY_SHORTCODE rule_id 分类短码与 category 不一致
  DSL_DIMENSION_MISMATCH score_impact.dimension 与 category 不一致
  DSL_BUDGET_EXCEEDED    单条 |delta| > 0.5 × base_d，或 < 1.00，或不在 0.01 网格
  DSL_NO_EVIDENCE        evidence_fields 为空，或未覆盖 condition / 模板引用的字段
  DSL_NO_SUGGESTION      非 boost 规则缺 suggestion_template（契约 validator 已有一半）
  DSL_SUPPRESS_UNKNOWN   suppresses 指向不存在的规则
  DSL_SUPPRESS_CYCLE     压制图有环，或存在「既压制又被压制」的链（本 ruleset 不允许链）
  DSL_TESTCASES          测试用例不是「≥1 正例 + ≥1 负例」
  TPL_*                  模板 8 条 lint（§9.7.4）中不依赖运行时上下文的部分
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable

import yaml

from ..contracts import (
    RULESET_VERSION,
    Category,
    Condition,
    DIM_BY_CODE,
    DimensionCode,
    Rule,
)
from ..utils import canonical_json, to_centi
from .context import KNOWN_FIELDS
from .templating import template_variables

__all__ = [
    "CATEGORY_DIM",
    "CATEGORY_SHORT",
    "DEFAULT_RULES_DIR",
    "lint_rules",
    "load_rules",
    "ruleset_meta",
]

DEFAULT_RULES_DIR: Path = Path(__file__).resolve().parents[2] / "rules"

RULE_ID_RE = re.compile(r"^R-(COLOR|STYLE|PROP|OCC|DET)-\d{3}$")
_DIM_LEAK_RE = re.compile(r"(?<![A-Za-z0-9])D[1-5](?![0-9])")

CATEGORY_SHORT: dict[Category, str] = {
    Category.COLOR: "COLOR",
    Category.STYLE: "STYLE",
    Category.PROPORTION: "PROP",
    Category.OCCASION: "OCC",
    Category.DETAIL: "DET",
}

CATEGORY_DIM: dict[Category, DimensionCode] = {
    Category.COLOR: DimensionCode.D1,
    Category.STYLE: DimensionCode.D2,
    Category.PROPORTION: DimensionCode.D3,
    Category.OCCASION: DimensionCode.D4,
    Category.DETAIL: DimensionCode.D5,
}

# §9.7.4 TPL_CONTEXT_DEPENDENT
TPL_BANNED_CONTEXT: tuple[str, ...] = (
    "请参考", "根据以上", "如上所述", "综上", "详见", "正如图片",
    "你上传的", "上文", "下一节", "本次检测",
)
# §9.7.4 TPL_PRONOUN_AMBIGUOUS
TPL_PRONOUNS: tuple[str, ...] = ("它", "这件", "那件", "该单品", "上述", "此款")
# §9.7.4 TPL_INTERNAL_LEAK
TPL_INTERNAL_TERMS: tuple[str, ...] = ("规则", "score", "delta", "veto", "维度")
# §9.7.3 TPL_NO_ACTION 的动作动词
ACTION_VERBS: tuple[str, ...] = ("换", "加", "去掉", "塞入", "提高", "降低", "统一", "缩短", "改")
MAX_TEMPLATE_CHARS = 90
MIN_STATEMENT_CHARS = 8


# --------------------------------------------------------------------------
# 条件 / 模板的字段引用抽取
# --------------------------------------------------------------------------


def iter_condition_fields(condition: Condition) -> Iterable[str]:
    """递归产出条件树引用的全部字段（含 abs_diff_* 的 other_field）。"""
    kind = condition.kind
    if kind == "all":
        for child in condition.all or []:
            yield from iter_condition_fields(child)
        return
    if kind == "any":
        for child in condition.any or []:
            yield from iter_condition_fields(child)
        return
    if kind == "not":
        if condition.not_ is not None:
            yield from iter_condition_fields(condition.not_)
        return
    if condition.field:
        yield condition.field
    if condition.other_field:
        yield condition.other_field


def iter_rule_template_vars(rule: Rule) -> Iterable[str]:
    for template in (rule.explain_template, rule.suggestion_template, rule.reinforce_template):
        if template:
            yield from template_variables(template)


def _chinese_len(text: str) -> int:
    return len(text)


def _lint_template(kind: str, template: str, rule: Rule) -> list[str]:
    """§9.7.4 中可静态检查的模板 lint。"""
    errors: list[str] = []
    where = f"{rule.rule_id} {kind}"

    for word in TPL_BANNED_CONTEXT:
        if word in template:
            errors.append(f"TPL_CONTEXT_DEPENDENT {where}: 含上下文依赖措辞「{word}」")
    for word in TPL_PRONOUNS:
        if word in template:
            errors.append(f"TPL_PRONOUN_AMBIGUOUS {where}: 含指代词「{word}」")
    for word in TPL_INTERNAL_TERMS:
        if word in template:
            errors.append(f"TPL_INTERNAL_LEAK {where}: 含内部术语「{word}」")
    if _DIM_LEAK_RE.search(template):
        errors.append(f"TPL_INTERNAL_LEAK {where}: 含维度代号")
    if rule.rule_id in template:
        errors.append(f"TPL_INTERNAL_LEAK {where}: 含 rule_id 原文")
    if not template.rstrip().endswith(("。", "！")):
        errors.append(f"TPL_INCOMPLETE_SENTENCE {where}: 未以「。」或「！」结尾")
    if _chinese_len(template) > MAX_TEMPLATE_CHARS:
        errors.append(f"TPL_TOO_LONG {where}: {_chinese_len(template)} 字 > {MAX_TEMPLATE_CHARS}")
    if not template.strip():
        errors.append(f"TPL_UNDEFINED_VAR {where}: 模板为空")
    return errors


# --------------------------------------------------------------------------
# 规则集 lint
# --------------------------------------------------------------------------


def lint_rules(rules: list[Rule]) -> list[str]:
    """返回全部 lint 错误（空列表 = 通过）。load_rules 在非空时拒绝装载。"""
    errors: list[str] = []
    by_id = {rule.rule_id: rule for rule in rules}
    if len(by_id) != len(rules):
        errors.append("DSL_DUPLICATE_RULE_ID: rule_id 有重复")

    for rule in sorted(rules, key=lambda item: item.rule_id):
        # ---- 身份 ----
        if not RULE_ID_RE.match(rule.rule_id):
            errors.append(f"DSL_CATEGORY_SHORTCODE {rule.rule_id}: rule_id 不符合 ^R-(COLOR|STYLE|PROP|OCC|DET)-\\d{{3}}$")
        elif rule.rule_id.split("-")[1] != CATEGORY_SHORT[rule.category]:
            errors.append(
                f"DSL_CATEGORY_SHORTCODE {rule.rule_id}: 短码与 category={rule.category.value} 不符")
        expected_dim = CATEGORY_DIM[rule.category]
        if rule.score_impact.dimension != expected_dim:
            errors.append(
                f"DSL_DIMENSION_MISMATCH {rule.rule_id}: "
                f"category={rule.category.value} 必须落在 {expected_dim.value}，"
                f"实际 {rule.score_impact.dimension.value}")

        # ---- delta ----
        delta = rule.score_impact.delta
        centi = to_centi(delta)
        if abs(delta * 100 - centi) > 1e-6:
            errors.append(f"DSL_BUDGET_EXCEEDED {rule.rule_id}: delta={delta} 不在 0.01 网格")
        base_centi = DIM_BY_CODE[rule.score_impact.dimension].base_centi
        if abs(centi) > base_centi / 2:
            errors.append(
                f"DSL_BUDGET_EXCEEDED {rule.rule_id}: |delta|={abs(delta)} 超过 "
                f"0.5×base_d={base_centi / 200:.2f}")
        if abs(centi) < 100:
            errors.append(f"DSL_BUDGET_EXCEEDED {rule.rule_id}: |delta|={abs(delta)} < 1.00")

        # ---- 字段登记与证据覆盖 ----
        if not rule.evidence_fields:
            errors.append(f"DSL_NO_EVIDENCE {rule.rule_id}: evidence_fields 为空")
        evidence = set(rule.evidence_fields)
        referenced = set(iter_condition_fields(rule.condition)) | set(iter_rule_template_vars(rule))
        for field in sorted(referenced):
            if field not in KNOWN_FIELDS:
                errors.append(f"DSL_UNKNOWN_FIELD {rule.rule_id}: 字段 {field} 未登记")
        for field in sorted(referenced - evidence):
            errors.append(f"DSL_NO_EVIDENCE {rule.rule_id}: 引用字段 {field} 未在 evidence_fields 声明")
        for field in sorted(evidence - KNOWN_FIELDS):
            errors.append(f"DSL_UNKNOWN_FIELD {rule.rule_id}: evidence_fields 含未登记字段 {field}")

        # ---- 建议 / 模板 ----
        if rule.severity.value != "boost":
            if not rule.suggestion_template:
                errors.append(f"DSL_NO_SUGGESTION {rule.rule_id}: 非 boost 规则缺 suggestion_template")
            elif not any(verb in rule.suggestion_template for verb in ACTION_VERBS):
                errors.append(f"TPL_NO_ACTION {rule.rule_id} suggestion: 不含动作动词")
        errors.extend(_lint_template("explain", rule.explain_template, rule))
        if rule.suggestion_template:
            errors.extend(_lint_template("suggestion", rule.suggestion_template, rule))
        if rule.reinforce_template:
            errors.extend(_lint_template("reinforce", rule.reinforce_template, rule))
        elif rule.severity.value == "boost":
            errors.append(f"DSL_NO_SUGGESTION {rule.rule_id}: boost 规则缺 reinforce_template")

        # ---- 冲突消解声明 ----
        for target in rule.suppresses:
            if target not in by_id:
                errors.append(f"DSL_SUPPRESS_UNKNOWN {rule.rule_id}: suppresses 指向不存在的 {target}")

        # ---- 用例（§11.1）----
        if not rule.test_cases.positive:
            errors.append(f"DSL_TESTCASES {rule.rule_id}: 缺正例")
        if not rule.test_cases.negative:
            errors.append(f"DSL_TESTCASES {rule.rule_id}: 缺负例")
        for case in rule.test_cases.positive + rule.test_cases.negative:
            if not isinstance(case, dict) or "fixture" not in case:
                errors.append(f"DSL_TESTCASES {rule.rule_id}: 用例缺少 fixture 名")

    # ---- 压制图：目标存在 + 无环 + 无链 ----
    graph = {rule.rule_id: [target for target in rule.suppresses] for rule in rules}
    errors.extend(_lint_suppression_graph(graph, by_id))

    # ---- mutex_group 必须成组 ----
    groups: dict[str, list[str]] = {}
    for rule in rules:
        if rule.mutex_group:
            groups.setdefault(rule.mutex_group, []).append(rule.rule_id)
    for group, members in sorted(groups.items()):
        if len(members) < 2:
            errors.append(f"DSL_MUTEX_GROUP {group}: 成员少于 2（{members}）")

    return errors


def _lint_suppression_graph(graph: dict[str, list[str]], by_id: dict[str, Rule]) -> list[str]:
    errors: list[str] = []
    targets = {target for listed in graph.values() for target in listed}
    for rule_id, listed in sorted(graph.items()):
        for target in listed:
            target_rule = by_id.get(target)
            if target_rule is None:
                continue
            if target_rule.severity.value == "boost":
                errors.append(
                    f"DSL_SUPPRESS_TARGET {rule_id}: boost 规则 {target} 不能被压制（§10.3 第 5 条）")
        if listed and rule_id in targets:
            errors.append(
                f"DSL_SUPPRESS_CHAIN {rule_id}: 既压制又被压制，本 ruleset 不支持压制链")

    # 环检测（DFS，确定性顺序）
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {rule_id: WHITE for rule_id in graph}

    def visit(node: str, stack: list[str]) -> None:
        color[node] = GRAY
        stack.append(node)
        for nxt in graph.get(node, []):
            if nxt not in color:
                continue
            if color[nxt] == GRAY:
                cycle = " -> ".join(stack[stack.index(nxt):] + [nxt])
                errors.append(f"DSL_SUPPRESS_CYCLE: {cycle}")
            elif color[nxt] == WHITE:
                visit(nxt, stack)
        stack.pop()
        color[node] = BLACK

    for rule_id in sorted(graph):
        if color[rule_id] == WHITE:
            visit(rule_id, [])
    return errors


# --------------------------------------------------------------------------
# 装载
# --------------------------------------------------------------------------


def _iter_rule_files(rules_dir: Path) -> Iterable[Path]:
    for path in sorted(rules_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".yaml", ".yml"}:
            continue
        if path.name.startswith(("_", ".")):
            continue
        yield path


def load_rules(rules_dir: Path | str | None = None) -> list[Rule]:
    """读取目录下全部规则 YAML，校验 ``Rule`` 与 lint，按 rule_id 排序返回（不可变约定）。"""
    directory = Path(rules_dir) if rules_dir is not None else DEFAULT_RULES_DIR
    if not directory.exists():
        raise FileNotFoundError(f"规则目录不存在: {directory}")

    rules: list[Rule] = []
    for path in _iter_rule_files(directory):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if payload is None:
            raise ValueError(f"{path}: 空文件")
        if not isinstance(payload, dict):
            raise ValueError(f"{path}: 顶层必须是 mapping（一个文件一条规则）")
        try:
            rules.append(Rule.model_validate(payload))
        except Exception as exc:  # noqa: BLE001 —— 需要路径上下文
            raise ValueError(f"{path}: 规则校验失败: {exc}") from exc

    rules.sort(key=lambda rule: rule.rule_id)
    errors = lint_rules(rules)
    if errors:
        joined = "\n".join(f"  - {error}" for error in errors)
        raise ValueError(f"规则集 lint 失败（{len(errors)} 条）:\n{joined}")
    return rules


def ruleset_meta(rules: list[Rule]) -> dict[str, Any]:
    """规则集指纹：版本 + 内容 sha256 + 条数（内容变更即指纹变更，可审计）。"""
    ordered = sorted(rules, key=lambda rule: rule.rule_id)
    digest = hashlib.sha256(
        canonical_json([rule.model_dump(mode="json") for rule in ordered]).encode("utf-8")
    ).hexdigest()
    return {"version": RULESET_VERSION, "sha256": digest, "count": len(ordered)}
