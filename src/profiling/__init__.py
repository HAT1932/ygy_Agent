"""画像层入口。

对外接口（其他模块按此调用，签名不可改）::

    from src.profiling import build_profile
    profile = build_profile(outfits)          # -> contracts.StyleProfile
    profile.tags[0].tag, profile.tags[0].weight
    profile.tag_evidence["通勤知性"]           # -> ["oft_...", ...]（US-2 可回溯）
    profile.preference_vector                 # 256 维用户塔向量 u
"""
from __future__ import annotations

from .builder import (
    MAX_TAGS,
    SLOT_WEIGHTS,
    build_profile,
)

__all__ = ["MAX_TAGS", "SLOT_WEIGHTS", "build_profile"]
