"""解析层入口（VLM adapter 的统一工厂）。

对外接口（其他模块按此调用，签名不可改）::

    from src.parsing import get_parser
    result = get_parser().parse_outfit(["demo/commute-1"], "business", "autumn")
    result.outfit     # contracts.Outfit
    result.warnings   # list[str]，会进入 Report.meta.parse_warnings

选择逻辑（由 ``src.config.get_settings()`` 决定）：
    * ``DASHSCOPE_API_KEY`` 存在 **且** ``OFFLINE_MODE=false`` → :class:`QwenVLMParser`
    * 否则 → :class:`MockParser`（全链路离线可跑，**零网络请求**）

即使错误地直接实例化 :class:`QwenVLMParser`，只要没有 key / 处于 offline，
它也会在发请求前降级为 mock（见该模块 ``parse_outfit`` 的首个分支）。
"""
from __future__ import annotations

from ..config import Settings, get_settings
from .base import (
    BaseParser,
    ParseResult,
    normalize_occasion,
    normalize_season,
)
from .mock import MockParser, N_SCRIPTS, SCRIPTS
from .qwen_vlm import QwenVLMParser

__all__ = [
    "BaseParser",
    "MockParser",
    "N_SCRIPTS",
    "ParseResult",
    "QwenVLMParser",
    "SCRIPTS",
    "get_parser",
    "normalize_occasion",
    "normalize_season",
]


def get_parser(settings: Settings | None = None) -> BaseParser:
    """按运行时配置选择解析器：有 key 且非 offline → Qwen，否则 mock。"""
    resolved = settings or get_settings()
    if resolved.vlm_ready:
        return QwenVLMParser(settings=resolved)
    return MockParser()
