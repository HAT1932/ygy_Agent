"""DSL 条件求值器（docs/03 §6.2 / §8 Step 2 的实现）。

契约里的封闭 op 集合（``contracts.OPS``）就是这里支持的全部能力，刻意**不**扩展：
    gt gte lt lte eq ne in not_in between
    contains_any contains_all contains_none
    abs_diff_gt abs_diff_lt exists is_true is_false

docs/03 §6.2.1 里的量词节点（count / for_all / any_garment）与
hue_dist_gt / intersects 等 op **不在冻结契约内**，因此那些语义由
``context.build_context`` 预计算成可独立测量的聚合字段（docs/03 §7.7 的合法机制），
规则只对聚合字段做阈值判断。详见 src/rules/context.py 顶部说明。

求值确定性与可计算性（§6.2.5）：
  - 集合运算前对两侧去重排序，结果不依赖解析顺序；
  - 数值比较先把浮点四舍五入到 1e-6，避免 0.85 > 0.85 类抖动；
  - 纯函数：同 (condition, ctx) 必得同一布尔值。
"""
from __future__ import annotations

from typing import Any

from ..contracts import Condition

__all__ = ["evaluate"]

_MISSING = object()


def _num(value: Any) -> float | None:
    """把数值统一成 round(·, 6) 的 float；bool / str / None 不算数值。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    return None


def _eq(left: Any, right: Any) -> bool:
    ln, rn = _num(left), _num(right)
    if ln is not None and rn is not None:
        return ln == rn
    return left == right


def _as_list(value: Any) -> list[Any] | None:
    if isinstance(value, (list, tuple, set, frozenset)):
        return sorted(value, key=repr)
    return None


def _compare(left: Any, right: Any, op: str) -> bool:
    lv, rv = _num(left), _num(right)
    if lv is None or rv is None:
        return False
    if op == "gt":
        return lv > rv
    if op == "gte":
        return lv >= rv
    if op == "lt":
        return lv < rv
    if op == "lte":
        return lv <= rv
    raise ValueError(f"未知比较 op: {op!r}")


def _eval_leaf(condition: Condition, ctx: dict[str, Any]) -> bool:
    field = condition.field
    op = condition.op
    assert field is not None and op is not None  # contracts 已校验

    actual = ctx.get(field, _MISSING)
    if actual is _MISSING:
        # 未登记字段一律视为不可判定；存在性判断同样为 False。
        return False

    if op == "exists":
        return actual is not None
    if op == "is_true":
        return actual is True
    if op == "is_false":
        return actual is False

    if actual is None:
        # 字段存在但值为 null：任何比较都不成立（门控之外的最后一道防线）。
        return False
    if isinstance(actual, float):  # 统一数值口径，避免边界抖动
        actual = round(actual, 6)

    if op in {"gt", "gte", "lt", "lte"}:
        return _compare(actual, condition.value, op)

    if op == "eq":
        return _eq(actual, condition.value)
    if op == "ne":
        return not _eq(actual, condition.value)

    if op in {"in", "not_in"}:
        if not isinstance(condition.value, (list, tuple, set, frozenset)):
            raise ValueError(f"op={op} 的 value 必须是列表")
        hit = any(_eq(actual, candidate) for candidate in condition.value)
        return hit if op == "in" else not hit

    if op == "between":
        bounds = condition.value
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            raise ValueError("op=between 的 value 必须是 [min, max]")
        low, high = _num(bounds[0]), _num(bounds[1])
        av = _num(actual)
        if av is None or low is None or high is None:
            return False
        return low <= av <= high

    if op in {"contains_any", "contains_all", "contains_none"}:
        left = _as_list(actual)
        right = _as_list(condition.value)
        if left is None or right is None:
            return False
        left_set, right_set = set(map(repr, left)), set(map(repr, right))
        if op == "contains_any":
            return bool(left_set & right_set)
        if op == "contains_all":
            return right_set <= left_set
        return not (left_set & right_set)

    if op in {"abs_diff_gt", "abs_diff_lt"}:
        other = ctx.get(condition.other_field or "", _MISSING)
        if other is _MISSING or other is None:
            return False
        lv, rv, threshold = _num(actual), _num(other), _num(condition.value)
        if lv is None or rv is None or threshold is None:
            return False
        diff = round(abs(lv - rv), 6)
        return diff > threshold if op == "abs_diff_gt" else diff < threshold

    raise ValueError(f"未实现的 op: {op!r}")


def evaluate(condition: Condition, ctx: dict[str, Any]) -> bool:
    """递归求值条件树；组合节点短路求值（左→右，顺序确定）。"""
    kind = condition.kind
    if kind == "all":
        return all(evaluate(child, ctx) for child in condition.all or [])
    if kind == "any":
        return any(evaluate(child, ctx) for child in condition.any or [])
    if kind == "not":
        assert condition.not_ is not None
        return not evaluate(condition.not_, ctx)
    return _eval_leaf(condition, ctx)
