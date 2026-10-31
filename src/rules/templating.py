"""解释模板渲染（docs/03 §9.7 的 INV-5「模板自足性」的唯一实现处）。

语法（封闭集合，见 docs/03 §9.7.2）：

    {<field_path>}                直接取 ctx 值
    {<field_path>|<formatter>}    formatter ∈ {deg, pct, list, enum_cn}

约束（与 §9.7.2 / §9.7.4 一致）：
  - 变量名必须与字段字典 1:1 同名同义，禁止别名；
  - 未定义变量直接抛 ``TemplateError``（对应 lint 码 TPL_UNDEFINED_VAR）；
  - 模板内不做任何计算：格式化器只做展示层转换；
  - 不出现 rule_id / 维度代号等内部术语由 lint（loader.py）负责，不在此处。

模板自足性意味着：这里的输出必须能**脱离 LLM** 直接贴到 UI 上。
"""
from __future__ import annotations

import re
from typing import Any

from ..utils import round_half_up

__all__ = [
    "TemplateError",
    "ENUM_CN",
    "format_value",
    "render_template",
    "template_variables",
]


class TemplateError(ValueError):
    """模板变量未定义 / 格式化器未知 —— 属于 lint 级错误，不允许静默降级。"""


_VAR_RE = re.compile(r"\{([^{}]+)\}")

_TPL_OPEN = "{"
_TPL_CLOSE = "}"


# --------------------------------------------------------------------------
# 受控词表 → 中文（§9.7.2.2 的 enum_cn；与 docs/03 §7 的词表一致）
# --------------------------------------------------------------------------

ENUM_CN: dict[str, str] = {
    # 场合（docs/04 英文枚举）
    "business": "通勤",
    "formal": "正式",
    "date": "约会",
    "casual": "休闲",
    "sport": "运动",
    "party": "聚会",
    "travel": "出行",
    "home": "居家",
    "unspecified": "未指定",
    # 季节
    "spring": "春季",
    "summer": "夏季",
    "autumn": "秋季",
    "winter": "冬季",
    "unknown": "未确定",
    # 冷暖调性
    "balanced": "冷暖平衡",
    "warm_only": "整体偏暖",
    "cool_only": "整体偏冷",
    "conflicting": "冷暖冲突",
    # 版型
    "tight": "紧身",
    "slim": "修身",
    "regular": "常规",
    "loose": "宽松",
    "oversized": "轮廓放大",
    # 长度
    "crop": "短款",
    "short": "短款",
    "long": "长款",
    "maxi": "超长",
    # 腰位
    "high": "高腰",
    "mid": "中腰",
    "low": "低腰",
    "na": "无腰位",
    # 材质家族
    "cotton": "棉",
    "wool": "羊毛",
    "silk": "丝",
    "linen": "亚麻",
    "denim": "牛仔",
    "leather": "皮革",
    "knit": "针织",
    "synthetic": "化纤",
    "metal": "金属",
    "other": "其他",
    # 图案
    "stripe": "条纹",
    "check": "格纹",
    "floral": "花卉",
    "graphic": "图案",
    "geometric": "几何",
    "solid": "纯色",
    # 颜色家族（docs/03 §7.5）
    "neutral": "中性色",
    "red": "红",
    "orange": "橙",
    "yellow": "黄",
    "yellow_green": "黄绿",
    "green": "绿",
    "spring_green": "春绿",
    "cyan": "青",
    "azure": "天蓝",
    "blue": "蓝",
    "violet": "紫",
    "magenta": "洋红",
    "rose": "玫红",
    # 判定
    "strongly_recommended": "强烈推荐",
    "suitable": "适合",
    "unsuitable": "不适合",
}


# --------------------------------------------------------------------------
# 格式化器（封闭集合，5 个含「省略」）
# --------------------------------------------------------------------------


def _trim_number(value: float) -> str:
    """数值保留至多 2 位小数并去尾零：158.0 → "158"，0.90 → "0.9"。"""
    v = round(float(value), 2)
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _fmt_default(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return _trim_number(value)
    if isinstance(value, (list, tuple)):
        return "、".join(_fmt_default(item) for item in value)
    return str(value)


def _fmt_deg(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TemplateError(f"|deg 只能用于数值，收到 {value!r}")
    return f"{_trim_number(value)}°"


def _fmt_pct(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TemplateError(f"|pct 只能用于数值，收到 {value!r}")
    return f"{int(round_half_up(float(value) * 100.0, 0))}%"


def _fmt_list(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (list, tuple)):
        return "、".join(_fmt_default(item) for item in value)
    return _fmt_default(value)


def _fmt_enum_cn(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (list, tuple)):
        return "、".join(_fmt_enum_cn(item) for item in value)
    key = str(value)
    return ENUM_CN.get(key, key)


_FORMATTERS = {
    "deg": _fmt_deg,
    "pct": _fmt_pct,
    "list": _fmt_list,
    "enum_cn": _fmt_enum_cn,
}


# --------------------------------------------------------------------------
# 对外 API
# --------------------------------------------------------------------------


def template_variables(template: str) -> list[str]:
    """返回模板中出现的全部字段路径（按出现顺序，未去重）。lint 用。"""
    return [_split_token(m.group(1))[0] for m in _VAR_RE.finditer(template)]


def _split_token(token: str) -> tuple[str, str | None]:
    token = token.strip()
    if "|" in token:
        field, formatter = token.split("|", 1)
        return field.strip(), formatter.strip()
    return token, None


def format_value(value: Any, formatter: str | None = None) -> str:
    """按 formatter 渲染单个值。未知 formatter 抛 TemplateError。"""
    if formatter is None or formatter == "":
        return _fmt_default(value)
    fn = _FORMATTERS.get(formatter)
    if fn is None:
        raise TemplateError(f"未知格式化器 |{formatter}（允许：{sorted(_FORMATTERS)}）")
    return fn(value)


def render_template(template: str, ctx: dict[str, Any], *, rule_id: str = "") -> str:
    """渲染模板；任何未定义变量都抛 TemplateError（不静默留空）。"""

    def _replace(match: re.Match[str]) -> str:
        field, formatter = _split_token(match.group(1))
        if not field:
            raise TemplateError(f"{rule_id}: 模板出现空变量 {{{match.group(1)}}}")
        if field not in ctx:
            raise TemplateError(f"{rule_id}: 未定义模板变量 {{{field}}}")
        return format_value(ctx[field], formatter)

    return _VAR_RE.sub(_replace, template)
