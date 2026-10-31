"""通用工具：厘点转换、确定性舍入、ID 生成、确定性哈希。

确定性要求见 docs/03 §2.4：全链路整数厘点，仅在输出边界 round_half_up 一次。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def to_centi(points: float) -> int:
    """分 → 厘点（1 分 = 100 厘点）。用 Decimal 避免 7.8*100=780.0000000000001。"""
    return int((Decimal(str(points)) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def to_points(centi: int) -> float:
    return float(Decimal(centi) / 100)


def round_half_up(value: float, ndigits: int = 1) -> float:
    """确定性四舍五入（Python 内置 round 是 banker's rounding，不可用于分数）。"""
    q = Decimal(1).scaleb(-ndigits)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))


def centi_to_score(centi: int, ndigits: int = 1) -> float:
    return round_half_up(centi / 100.0, ndigits)


def stable_hash(*parts: Any, size: int = 16) -> str:
    """跨进程稳定的短哈希（不用 hash()，它带 PYTHONHASHSEED 随机化）。"""
    payload = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:size]


def make_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}_{stable_hash(*parts)}"


def stable_uuid(prefix: str, *parts: Any) -> str:
    payload = "|".join("" if p is None else str(p) for p in parts)
    return f"{prefix}_{uuid.uuid5(uuid.NAMESPACE_URL, payload).hex}"


def canonical_json(obj: Any) -> str:
    """用于审计/黄金夹具比对：键排序、无空格、UTF-8 保留中文。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
