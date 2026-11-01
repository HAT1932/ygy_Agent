"""文本 LLM 润色适配器（OpenAI 兼容 ``/chat/completions``）。

定位（docs/00 §4 / docs/03 §9.7.1）：**LLM 只做润色，不负责判断，也不改分数**。
因此本模块只接收「已由模板拼好的事实性摘要」，产出一段改写；调用方必须再用
:func:`src.synthesis.templates.check_facts` 做事实一致性校验，失败即回退模板。

降级矩阵（docs/05 §10.4）：
    * 未配置 key / ``OFFLINE_MODE=true`` → 直接返回 ``None``，**零网络请求**；
    * 5xx / 超时 / 空内容 → 重试 1 次（0.5s 退避）后返回 ``None``；
    * 返回 ``None`` 时调用方渲染 ``RenderMode.template_only``。
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from ..config import Settings, get_settings

DEFAULT_TIMEOUT_S = 6.0  # docs/03 §9.7.8：summary 超时 6.0s
_RETRY_BACKOFF_S = 0.5

_SYSTEM_PROMPT = """你是中文穿搭报告文案润色器，只能改写给定的事实摘要，禁止新增任何事实。
硬约束：
1. 不得新增数字：文本中出现的每个数字都必须与输入 JSON 中的数字一致。
2. 不得新增评价：禁止出现「高级感 / 显瘦 / 气质 / 百搭 / 上档次 / 质感 / 利落 / 完美 / 惊艳 / 绝了 / 氛围感」等主观评价词。
3. 不得改变判定：判定词只能使用输入中的 verdict_cn，不得写成其他等级。
4. 不得出现规则编号、内部维度代号（如 D1）、"规则""delta""维度"等内部术语。
5. 只能改写总结句，不得新增建议；建议部分由系统另外给出。
6. 长度不超过原摘要的 1.5 倍；中文；一到三句；以「。」结尾。
只输出一个 JSON 对象：{"summary": "改写后的总评"}"""


def llm_ready(settings: Settings | None = None) -> bool:
    resolved = settings or get_settings()
    return bool(resolved.llm_ready)


def _build_payload(reference_summary: str, ctx: dict[str, Any],
                   hint: str | None, settings: Settings) -> dict[str, Any]:
    from .templates import VERDICT_CN

    raw_verdict = str(ctx.get("verdict") or "neutral")
    context = {
        # 必须映射成中文判定词：直接传枚举会让模型写出 "判定为 suitable"（实测踩过）
        "verdict_cn": VERDICT_CN.get(raw_verdict, "一般"),
        "total_score": ctx.get("total_score"),
        "dimension_scores": [
            {"name": d.get("name"), "score": d.get("score"), "max": d.get("max")}
            for d in (ctx.get("dimension_scores") or []) if isinstance(d, dict)
        ],
        "issues": [
            {"title": h.get("title"), "explanation": h.get("explanation")}
            for h in (ctx.get("rule_hits") or [])
            if isinstance(h, dict) and str(h.get("severity") or "") != "boost"
        ][:5],
        "suggestions": [
            {"target": s.get("target"), "action": s.get("action"),
             "expected_gain": s.get("expected_gain")}
            for s in (ctx.get("suggestions") or []) if isinstance(s, dict)
        ][:3],
        "rewrite_reference": reference_summary,
    }
    if hint:
        context["user_hint"] = str(hint)[:120]
    return {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }


def _extract_summary(raw_text: str) -> str | None:
    cleaned = str(raw_text or "").strip()
    if not cleaned:
        return None
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            return None
    if isinstance(data, dict):
        summary = data.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
    return None


def polish_summary(reference_summary: str, ctx: dict[str, Any], *,
                   hint: str | None = None,
                   settings: Settings | None = None) -> str | None:
    """尝试用 LLM 润色摘要；不可用/失败返回 ``None``（调用方回退模板）。"""
    resolved = settings or get_settings()
    if not resolved.llm_ready:
        return None  # 无 key / 离线：绝不建连

    payload = _build_payload(reference_summary, ctx, hint, resolved)
    url = f"{resolved.deepseek_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {resolved.deepseek_api_key}",
        "Content-Type": "application/json",
    }

    for attempt in (1, 2):
        try:
            response = httpx.post(url, headers=headers, json=payload,
                                  timeout=DEFAULT_TIMEOUT_S)
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            summary = _extract_summary(content)
            if summary:
                return summary
            raise RuntimeError("空摘要")
        except Exception:  # noqa: BLE001 - 任何失败都降级，不把异常抛给业务层
            if attempt == 1:
                time.sleep(_RETRY_BACKOFF_S)
                continue
            return None
    return None
