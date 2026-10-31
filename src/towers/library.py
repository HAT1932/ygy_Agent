"""搭配库与召回索引 —— 「双塔召回」的交易对手方（搭配塔的候选池）。

设计要点：
  1. **候选池必须独立于用户输入**。若只用用户自己的参考穿搭做召回，等于把输入原样吐回，
     不构成"召回"。
  2. 用户塔向量由画像聚合得到（`TowerEncoder.encode_profile`），搭配塔向量由本库各条目得到，
     两者在同一 256 维空间用 cosine 比较。
  3. 召回结果要能解释：每条带 title / style_tags / item_summary，让用户知道"为什么推荐它"。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from ..contracts import Color, Garment, Outfit, RecallItem, Slot
from ..utils import make_id

DEFAULT_LIBRARY_PATH = Path(__file__).resolve().parents[2] / "data" / "outfits.yaml"

SLOT_CN = {
    Slot.upper: "上装", Slot.lower: "下装", Slot.outer: "外套",
    Slot.shoes: "鞋履", Slot.accessory: "配饰", Slot.dress: "连衣裙",
}


@dataclass(frozen=True)
class LibraryItem:
    item_id: str
    title: str
    occasion: str | None
    style_tags: tuple[str, ...]
    outfit: Outfit

    @property
    def summary(self) -> str:
        parts = []
        for g in self.outfit.items:
            if g.primary_color is not None:
                parts.append(f"{g.primary_color.name or ''}{g.category}".strip())
            else:
                parts.append(g.category)
        return " + ".join(parts)


def _to_garment(raw: dict, item_id: str, idx: int) -> Garment:
    color = Color(
        hex=str(raw.get("color", "#CCCCCC")),
        name=str(raw.get("color_name", "")),
        family=str(raw.get("family", "")),
    )
    return Garment(
        garment_id=make_id("grm", item_id, idx, raw.get("slot"), raw.get("category")),
        slot=Slot(raw["slot"]),
        category=str(raw.get("category", "")),
        colors=[color],
        material=raw.get("material"),
        fit=raw.get("fit"),
        length=raw.get("length"),
        formality=int(raw["formality"]) if raw.get("formality") is not None else None,
        style_tags=list(raw.get("style_tags") or []),
        pattern=raw.get("pattern"),
        confidence=1.0,          # 库里是人工定义的条目，不存在识别不确定性
    )


def load_library(path: Path | str | None = None) -> list[LibraryItem]:
    p = Path(path) if path is not None else DEFAULT_LIBRARY_PATH
    if not p.exists():
        return []
    payload = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    items: list[LibraryItem] = []
    for raw in payload.get("items", []):
        item_id = str(raw["item_id"])
        garments = [_to_garment(g, item_id, i) for i, g in enumerate(raw.get("items", []))]
        outfit = Outfit(
            outfit_id=item_id,
            items=garments,
            occasion=raw.get("occasion"),
            season=None,
            confidence=1.0,
        )
        items.append(LibraryItem(
            item_id=item_id,
            title=str(raw.get("title", item_id)),
            occasion=raw.get("occasion"),
            style_tags=tuple(raw.get("style_tags") or []),
            outfit=outfit,
        ))
    return items


class LibraryIndex:
    """把搭配库编码进与用户塔同一向量空间，并提供 top-K 召回。"""

    def __init__(self, items: list[LibraryItem], encoder) -> None:
        self.items = items
        self._by_id = {it.item_id: it for it in items}
        self._ids: list[str] = []
        self._mat: np.ndarray | None = None
        if items:
            vecs = [np.asarray(encoder.encode_outfit(it.outfit), dtype=np.float64) for it in items]
            self._mat = np.vstack(vecs)
            self._ids = [it.item_id for it in items]

    def __len__(self) -> int:
        return len(self._ids)

    def recall(self, profile_vec: list[float], k: int = 5,
               min_similarity: float | None = None) -> list[tuple[LibraryItem, float]]:
        """余弦召回。min_similarity 传 None 时按 top-1 的相对比例过滤（避免绝对阈值绑后端）。"""
        if self._mat is None or not self._ids:
            return []
        q = np.asarray(profile_vec, dtype=np.float64)
        denom = np.linalg.norm(self._mat, axis=1) * np.linalg.norm(q)
        denom[denom == 0] = 1e-12
        sims = (self._mat @ q) / denom
        order = np.argsort(-sims)[: max(k, 1)]
        top = [(self._by_id[self._ids[i]], float(sims[i])) for i in order]
        if min_similarity is not None:
            top = [(it, s) for it, s in top if s >= min_similarity]
        return top

    def to_recall_items(self, hits: list[tuple[LibraryItem, float]]) -> list[RecallItem]:
        return [
            RecallItem(
                item_id=it.item_id,
                title=it.title,
                similarity=round(sim, 4),
                style_tags=list(it.style_tags),
                occasion=it.occasion,
                item_summary=it.summary,
            )
            for it, sim in hits
        ]
