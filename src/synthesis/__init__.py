"""文案合成层入口（template_only 全功能 + LLM 只润色 + 事实校验兜底）。

对外接口（其他模块按此调用，签名不可改）::

    from src.synthesis import synthesize
    summary, suggestions, render_mode = synthesize(report_ctx)
    # report_ctx 含: verdict, total_score, dimension_scores, rule_hits,
    #                style_match, outfit, profile（也接受 Report 对象）

行为：
    1. **总是**先用模板生成完整的 ``summary`` + ``suggestions``
       （LLM 未配置 / 超时 / 事实校验失败都直接返回这一份，RenderMode.template_only）；
    2. 只有 LLM 可用且润色稿通过 :func:`check_facts` 时，才采用润色后的 summary，
       并返回 ``RenderMode.hybrid``（规则解释仍是引擎渲染的模板文本）；
    3. suggestions 永远由确定性模板生成，LLM 不参与——保证"把 X 换成 Y"的建议
       可复现、可追溯（docs/00 §5.3）。
"""
from __future__ import annotations

from typing import Any

from ..config import get_settings
from ..contracts import RenderMode, Suggestion
from .llm import polish_summary
from .templates import (
    build_suggestions,
    build_summary,
    check_facts,
    normalize_report_ctx,
)

__all__ = [
    "RenderMode",
    "Suggestion",
    "build_suggestions",
    "build_summary",
    "check_facts",
    "synthesize",
]


def synthesize(report_ctx: dict[str, Any], *,
               hint: str | None = None
               ) -> tuple[str, list[Suggestion], RenderMode]:
    """把结构化评审结果合成为「总评 + 可执行建议 + 渲染模式」。

    Args:
        report_ctx: 至少包含 ``verdict`` / ``total_score`` / ``dimension_scores`` /
            ``rule_hits``；``outfit`` / ``profile`` / ``style_match`` 可选。
        hint: 可选的用户偏好提示，只影响 LLM 润色，不影响模板输出。
    """
    ctx = normalize_report_ctx(report_ctx)

    suggestions = build_suggestions(ctx)
    reference_summary = build_summary(ctx, suggestions)

    settings = get_settings()
    if not getattr(settings, "llm_ready", False):
        return reference_summary, suggestions, RenderMode.template_only

    llm_ctx = dict(ctx)
    llm_ctx["suggestions"] = [suggestion.model_dump(mode="python")
                              for suggestion in suggestions]
    try:
        polished = polish_summary(reference_summary, llm_ctx, hint=hint, settings=settings)
    except Exception:  # noqa: BLE001 - 润色是可选增强，任何异常都不能让报告失败
        polished = None

    if not polished:
        return reference_summary, suggestions, RenderMode.template_only

    violations = check_facts(polished, llm_ctx, reference_summary)
    if violations:
        # 事实校验失败 → 回退模板版本，并用 RenderMode 标明（R-03 / docs/03 §9.7.7）
        return reference_summary, suggestions, RenderMode.template_only

    return polished, suggestions, RenderMode.hybrid
