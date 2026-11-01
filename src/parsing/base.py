"""解析器抽象层（VLM adapter 的统一收口，docs/05 §10.2）。

业务层只依赖本模块的 :class:`BaseParser` / :class:`ParseResult`，
**禁止** import 任何厂商 SDK（docs/05 §10.3 机制 1）。

``ParseResult`` 必须带 ``warnings``：R-01 要求「材质不确定时规则降级并记
``parse_warnings``」，所以警告是规则引擎的输入，不是可选装饰（docs/05 §10.2）。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field

from ..contracts import Outfit

# ---------------------------------------------------------------------------
# 用户输入归一化（中文别名 → docs/04 的英文枚举）
# ---------------------------------------------------------------------------
OCCASION_ALIASES: dict[str, str] = {
    "business": "business", "commute": "business", "work": "business",
    "通勤": "business", "上班": "business", "商务": "business", "职场": "business",
    "formal": "formal", "正式": "formal", "宴会": "formal", "典礼": "formal",
    "date": "date", "约会": "date", "date night": "date",
    "casual": "casual", "休闲": "casual", "日常": "casual", "周末": "casual",
    "sport": "sport", "sports": "sport", "运动": "sport", "健身": "sport", "gym": "sport",
    "party": "party", "派对": "party", "聚会": "party",
    "travel": "travel", "旅行": "travel", "出游": "travel",
    "home": "home", "居家": "home", "在家": "home",
    "unspecified": "unspecified", "未知": "unspecified",
}

SEASON_ALIASES: dict[str, str] = {
    "spring": "spring", "春": "spring", "春季": "spring",
    "summer": "summer", "夏": "summer", "夏季": "summer",
    "autumn": "autumn", "fall": "autumn", "秋": "autumn", "秋季": "autumn",
    "winter": "winter", "冬": "winter", "冬季": "winter",
    "unknown": "unknown", "未知": "unknown",
}


def normalize_occasion(value: str | None) -> str | None:
    """把用户声明归一化为 docs/04 的 Occasion 枚举；不认识返回 ``None``。"""
    if value is None:
        return None
    key = str(value).strip().lower()
    if not key:
        return None
    return OCCASION_ALIASES.get(key)


def normalize_season(value: str | None) -> str | None:
    if value is None:
        return None
    key = str(value).strip().lower()
    if not key:
        return None
    return SEASON_ALIASES.get(key)


# ---------------------------------------------------------------------------
# 结果与基类
# ---------------------------------------------------------------------------


@dataclass
class ParseResult:
    """解析结果。

    Attributes:
        outfit: 结构化搭配（contracts.Outfit）。
        warnings: 机器可读的降级/不确定标记，会原样进入 ``Report.meta.parse_warnings``。
    """

    outfit: Outfit
    warnings: list[str] = field(default_factory=list)


class BaseParser(abc.ABC):
    """所有解析实现的共同接口（签名不可改，上层按此调用）。"""

    name: str = "base"
    model: str = "base"
    mode: str = "base"

    @abc.abstractmethod
    def parse_outfit(
        self,
        image_refs: list[str],
        occasion: str | None = None,
        season: str | None = None,
    ) -> ParseResult:
        """把 1–5 张图解析为一套 ``Outfit``。"""

    # -- 共用工具 ---------------------------------------------------------
    @staticmethod
    def normalize_ref(raw: str) -> str:
        """归一化单个 image_ref（用户从各处粘贴过来的路径形态差异很大）。

        Windows 上「复制文件地址」会**自带双引号**：
            "E:\\photos\\1.jpg"
        如果不剥掉，``Path(...).is_file()`` 永远为 False，系统会静默降级成 mock
        （实测过：用户以为在测自己的照片，其实拿到的是假数据，见 docs/10 F-13/F-14）。

        一并处理：首尾空白、成对引号（" ' “ ”）、file:// 前缀、行内多余空白。
        """
        text = str(raw).strip()
        # 成对剥引号（可能套了两层，最多剥 3 次）
        for _ in range(3):
            before = text
            for pair in (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’")):
                if len(text) >= 2 and text.startswith(pair[0]) and text.endswith(pair[1]):
                    text = text[1:-1].strip()
            if text == before:
                break
        # file:///E:/a.jpg → E:/a.jpg
        for prefix in ("file:///", "file://"):
            if text.lower().startswith(prefix):
                text = text[len(prefix):]
                break
        return text.strip()

    @staticmethod
    def _clean_refs(image_refs: list[str] | None) -> list[str]:
        refs = [BaseParser.normalize_ref(r) for r in (image_refs or [])]
        refs = [r for r in refs if r]
        if not refs:
            raise ValueError("image_refs 不能为空")
        return refs

    @staticmethod
    def _resolve_occasion(
        raw: str | None, default: str | None, warnings: list[str]
    ) -> str | None:
        if raw is None or not str(raw).strip():
            return default
        normalized = normalize_occasion(raw)
        if normalized is None:
            warnings.append(f"UNKNOWN_OCCASION:{raw}")
            return default
        return normalized

    @staticmethod
    def _resolve_season(
        raw: str | None, default: str | None, warnings: list[str]
    ) -> str | None:
        if raw is None or not str(raw).strip():
            return default
        normalized = normalize_season(raw)
        if normalized is None:
            warnings.append(f"UNKNOWN_SEASON:{raw}")
            return default
        return normalized
