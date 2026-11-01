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
from PIL import Image, ImageDraw

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


def _hex_of(garment) -> str:
    try:
        if garment is not None and garment.primary_color is not None:
            return garment.primary_color.hex
    except Exception:  # noqa: BLE001
        pass
    return "#CCCCCC"


def render_outfit_image(outfit: Outfit, size: tuple[int, int] = (320, 480)) -> Image.Image:
    """把结构化搭配渲染成「人穿着这套衣服」的示意图，用于给库条目算真视觉向量。

    刻意与真人照片同模态（人物居中、浅色背景），让 FashionCLIP 的视觉空间可比。
    这是**过渡方案**：生产环境应使用真实搭配图，而不是渲染图。
    """
    W, H = size
    img = Image.new("RGB", (W, H), "#EFEFEF")
    d = ImageDraw.Draw(img)
    cx = W // 2
    top = outfit.first(Slot.outer) or outfit.first(Slot.upper) or outfit.first(Slot.dress)
    lower = outfit.first(Slot.lower) or outfit.first(Slot.dress)
    shoes = outfit.first(Slot.shoes)
    acc = outfit.first(Slot.accessory)

    # 头 + 颈
    d.ellipse([cx - 30, 26, cx + 30, 86], fill="#D9B99B")
    d.rectangle([cx - 11, 80, cx + 11, 98], fill="#D9B99B")
    # 躯干（上装/外套/连衣裙）
    d.rectangle([cx - 70, 96, cx + 70, 240], fill=_hex_of(top))
    d.rectangle([cx - 96, 100, cx - 70, 230], fill=_hex_of(top))
    d.rectangle([cx + 70, 100, cx + 96, 230], fill=_hex_of(top))
    # 下装（或连衣裙延伸）
    lower_color = _hex_of(lower)
    d.rectangle([cx - 58, 240, cx - 6, 400], fill=lower_color)
    d.rectangle([cx + 6, 240, cx + 58, 400], fill=lower_color)
    # 鞋
    d.rectangle([cx - 64, 400, cx - 2, 436], fill=_hex_of(shoes))
    d.rectangle([cx + 2, 400, cx + 64, 436], fill=_hex_of(shoes))
    # 配饰（小色块，提示存在）
    if acc is not None:
        d.rectangle([cx - 18, 150, cx + 18, 196], fill=_hex_of(acc))
    return img


class LibraryIndex:
    """把搭配库编码进与用户塔同一向量空间，并提供 top-K 召回。"""

    def __init__(self, items: list[LibraryItem], encoder,
                 allow_visual: bool = True) -> None:
        self.items = items
        self._by_id = {it.item_id: it for it in items}
        self._ids: list[str] = []
        self._mat: np.ndarray | None = None
        self.visual_used = False
        if items:
            # 关键：只能与查询侧同后端。若查询侧拿不到视觉向量（离线/mock），
            # 库里也必须是哈希，否则两侧不同源、cosine 无意义（docs/10 F-10）。
            if allow_visual:
                decorated = [self._with_visual(it) for it in items]
                self.visual_used = all(it.outfit.image_embedding is not None for it in decorated)
            else:
                decorated = items
            self.items = decorated
            self._by_id = {it.item_id: it for it in decorated}
            vecs = [np.asarray(encoder.encode_outfit(it.outfit), dtype=np.float64)
                    for it in decorated]
            self._mat = np.vstack(vecs)
            self._ids = [it.item_id for it in decorated]

    @staticmethod
    def _with_visual(item: LibraryItem) -> LibraryItem:
        """给库条目补真视觉 embedding（把属性渲染成「人穿着这套衣服」的示意图）。

        为什么必须补：双塔要求两侧同空间。用户侧是真实人像（FashionCLIP 向量），
        若库里只有哈希指纹，两边不同源，cosine 就没有可比性。
        """
        if item.outfit.image_embedding is not None:
            return item
        try:
            from . import visual as _visual
            if not _visual.available():
                return item
            vec = _visual.encode_image(render_outfit_image(item.outfit))
            if vec is None:
                return item
            return LibraryItem(item_id=item.item_id, title=item.title,
                               occasion=item.occasion, style_tags=item.style_tags,
                               outfit=item.outfit.model_copy(update={"image_embedding": vec}))
        except Exception:  # noqa: BLE001
            return item

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
