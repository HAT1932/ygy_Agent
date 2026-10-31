"""内存向量存储（MVP）。

生产实现见 docs/02 §7：PostgreSQL + pgvector（HNSW, ``vector_cosine_ops``）。
本模块只提供同一语义的最小内存实现，供 demo、单测与离线评测使用：

    store.add("oft_1", vec)
    store.search(query_vec, k=3) -> [(outfit_id, cosine), ...]

语义要点（对齐 docs/02 §6.4.3 / §6.3）：
    * 入库前 L2 归一化，检索用 cosine（= 单位向量点积）。
    * 相同 ``outfit_id`` 重复 add = 覆盖（upsert，不做重复累加）。
    * 排序确定性：``(-cosine, outfit_id)``；同分按 id 字典序，禁止依赖 dict 插入序。
    * 维度不匹配直接报错——跨 embedding 版本的向量绝不允许混算。
"""
from __future__ import annotations

import math
from typing import Iterable, Iterator


def _normalize(vec: Iterable[float]) -> tuple[float, ...]:
    values = tuple(float(v) for v in vec)
    norm = math.sqrt(sum(v * v for v in values))
    if norm <= 1e-12:
        return tuple(0.0 for _ in values)
    return tuple(v / norm for v in values)


class VectorStore:
    """极简内存向量库（cosine）。"""

    def __init__(self) -> None:
        self._vectors: dict[str, tuple[float, ...]] = {}
        self._dim: int | None = None

    # -- 写 ---------------------------------------------------------------
    def add(self, outfit_id: str, vec: list[float]) -> None:
        if not outfit_id:
            raise ValueError("outfit_id 不能为空")
        values = tuple(float(v) for v in vec)
        if not values:
            raise ValueError("向量不能为空")
        if self._dim is None:
            self._dim = len(values)
        elif len(values) != self._dim:
            raise ValueError(
                f"向量维度不一致：store={self._dim}, 收到={len(values)}（禁止混用 embedding 版本）")
        self._vectors[outfit_id] = _normalize(values)

    def add_many(self, items: Iterable[tuple[str, list[float]]]) -> None:
        for outfit_id, vec in items:
            self.add(outfit_id, vec)

    def remove(self, outfit_id: str) -> bool:
        return self._vectors.pop(outfit_id, None) is not None

    def clear(self) -> None:
        self._vectors.clear()
        self._dim = None

    # -- 读 ---------------------------------------------------------------
    @property
    def dim(self) -> int | None:
        return self._dim

    def __len__(self) -> int:
        return len(self._vectors)

    def __contains__(self, outfit_id: object) -> bool:
        return outfit_id in self._vectors

    def get(self, outfit_id: str) -> list[float] | None:
        vec = self._vectors.get(outfit_id)
        return list(vec) if vec is not None else None

    def outfit_ids(self) -> list[str]:
        return sorted(self._vectors)

    def items(self) -> Iterator[tuple[str, list[float]]]:
        for outfit_id in sorted(self._vectors):
            yield outfit_id, list(self._vectors[outfit_id])

    def search(self, vec: list[float], k: int = 3) -> list[tuple[str, float]]:
        """返回 ``[(outfit_id, cosine)]``，按相似度降序（同分按 id 升序）。"""
        if k <= 0:
            return []
        values = tuple(float(v) for v in vec)
        if not values:
            raise ValueError("查询向量不能为空")
        if self._dim is not None and len(values) != self._dim:
            raise ValueError(
                f"查询向量维度不一致：store={self._dim}, 收到={len(values)}")
        query = _normalize(values)

        scored: list[tuple[str, float]] = []
        for outfit_id, stored in self._vectors.items():
            cosine = sum(a * b for a, b in zip(query, stored))
            # 浮点误差可能让 cosine 略微越界
            cosine = max(-1.0, min(1.0, cosine))
            scored.append((outfit_id, cosine))
        scored.sort(key=lambda pair: (-round(pair[1], 12), pair[0]))
        return [(outfit_id, float(cosine)) for outfit_id, cosine in scored[:k]]
