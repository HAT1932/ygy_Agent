"""双塔向量层（召回 / 风格度量空间，不训练）。

对外接口（被 src.pipeline 按此调用，签名不可改）::

    from src.towers import TowerEncoder, VectorStore
    enc = TowerEncoder()
    v = enc.encode_outfit(outfit)            # 256 维，L2 归一化
    u = enc.encode_profile(profile)          # 256 维，L2 归一化
    enc.config -> {"mode","dim","projection_sha256","img_dim","struct_dim","alpha","beta"}
    store = VectorStore(); store.add(outfit_id, v); store.search(u, k=3)

降级（docs/05 §10.4）：Embedding API 不可用时用本模块的 ``mode="hash"``
（``β=1, α=0`` 的等价路径见 :meth:`TowerEncoder.encode_profile` 的缺失画像分支）。
"""
from __future__ import annotations

from .encoder import (
    ALPHA,
    BETA,
    COLOR_FAMILIES,
    DIM,
    EMBEDDING_MODE,
    EMBEDDING_VERSION,
    FITS,
    IMG_DIM,
    LENGTHS,
    MATERIAL_FAMILIES,
    MATERIAL_FAMILY_BY_CN,
    PATTERNS,
    PROJECTION_SEED,
    SLOT_ORDER,
    STRUCT_DIM,
    STRUCT_RAW_DIM,
    STYLE_TAGS,
    TowerEncoder,
    color_family_from_hsv,
    material_family_of,
    profile_struct_features,
    projection_sha256,
    struct_features,
)
from .store import VectorStore

__all__ = [
    "ALPHA",
    "BETA",
    "COLOR_FAMILIES",
    "DIM",
    "EMBEDDING_MODE",
    "EMBEDDING_VERSION",
    "FITS",
    "IMG_DIM",
    "LENGTHS",
    "MATERIAL_FAMILIES",
    "MATERIAL_FAMILY_BY_CN",
    "PATTERNS",
    "PROJECTION_SEED",
    "SLOT_ORDER",
    "STRUCT_DIM",
    "STRUCT_RAW_DIM",
    "STYLE_TAGS",
    "TowerEncoder",
    "VectorStore",
    "color_family_from_hsv",
    "material_family_of",
    "profile_struct_features",
    "projection_sha256",
    "struct_features",
]
