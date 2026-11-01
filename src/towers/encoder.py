"""双塔编码器（MVP：**不训练**）。

Outfit Tower（docs/00 §7.2 / docs/02 §6.2）::

    v = L2norm( [ α · L2norm(e_img) ; β · L2norm(W_struct · x_struct) ] )
    dim = img_dim + struct_dim = 160 + 96 = 256

- ``e_img``：**确定性哈希 embedding**（``mode = "hash"``）。零依赖、跨进程稳定，
  不使用 ``hash()``（它受 PYTHONHASHSEED 随机化），只用 sha256。
- ``W_struct``：固定 seed 的高斯随机投影矩阵，作为**不可变制品**发布；
  其 sha256 由 :pyattr:`TowerEncoder.config` 的 ``projection_sha256`` 暴露（baseline B-06）。
- 用固定随机投影而非 PCA：无需训练、不依赖数据、Johnson–Lindenstrauss 保证近邻结构
  期望保持、制品可哈希（docs/02 §6.2.3）。

硬约束：
    * ``img_dim`` / ``struct_dim`` / ``dim`` 钉死，任何实现都必须一致（B-11）。
    * 投影矩阵只依赖 seed，不读 numpy 全局随机状态、不受调用顺序影响。
    * **同一条 outfit 无论作为参考图还是待评审图，向量必须相同**——
      :func:`_signature` 只由 outfit 内容（含 image_ref）决定，不含 role / profile。
    * 全流程零网络、零时间依赖。

User Tower（docs/02 §6.3）：其参考穿搭 outfit 向量的加权平均；
:func:`src.profiling.build_profile` 在画像构建时写入 ``StyleProfile.preference_vector``，
:meth:`TowerEncoder.encode_profile` 优先使用它。
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Iterable

import numpy as np

from ..contracts import Garment, Outfit, Slot, StyleProfile
from ..utils import canonical_json

# ---------------------------------------------------------------------------
# 钉死的维度（B-11：不同实现的向量必须可比）
# ---------------------------------------------------------------------------
IMG_DIM: int = 160
STRUCT_DIM: int = 96
DIM: int = IMG_DIM + STRUCT_DIM  # 256
# 真视觉后端的原始维度（FashionCLIP / CLIP ViT-B/32）
VISUAL_RAW_DIM: int = 512
ALPHA: float = 0.6
BETA: float = 0.4
PROJECTION_SEED: int = 20261031  # docs/02 §6.2.3
EMBEDDING_MODE: str = "hash"
EMBEDDING_VERSION: str = "hash256-v1"


# ---------------------------------------------------------------------------
# 受控词表（与 docs/03 §7.5 / §7.6、docs/04 的枚举保持一致）
# ---------------------------------------------------------------------------
STYLE_TAGS: tuple[str, ...] = (
    "极简", "通勤知性", "法式", "甜美", "运动休闲", "街头潮流", "商务正装",
    "机能风", "工装", "正式", "优雅", "经典", "简约", "商务", "浪漫", "甜酷",
    "华丽", "潮流", "休闲", "居家", "舒适", "洛丽塔", "硬核工装", "复古", "学院",
)

CHROMATIC_FAMILIES: tuple[str, ...] = (
    "red", "orange", "yellow", "yellow_green", "green", "spring_green",
    "cyan", "azure", "blue", "violet", "magenta", "rose",
)
COLOR_FAMILIES: tuple[str, ...] = (
    "neutral_black", "neutral_mid", "neutral_white",
) + CHROMATIC_FAMILIES  # 15

FITS: tuple[str, ...] = ("tight", "slim", "regular", "loose", "oversized")
MATERIAL_FAMILIES: tuple[str, ...] = (
    "cotton", "wool", "silk", "linen", "denim",
    "leather", "knit", "synthetic", "metal", "other",
)
PATTERNS: tuple[str, ...] = ("solid", "stripe", "check", "floral", "graphic")
LENGTHS: tuple[str, ...] = ("crop", "short", "regular", "long", "maxi")
SLOT_ORDER: tuple[str, ...] = (
    "upper", "lower", "outer", "dress", "shoes", "accessory",
)

# docs/03 §7.6 左列（中文细粒度）→ material_family
MATERIAL_FAMILY_BY_CN: dict[str, str] = {
    "棉": "cotton", "府绸": "cotton", "牛津纺": "cotton", "斜纹棉": "cotton",
    "羊毛": "wool", "羊绒": "wool", "粗花呢": "wool", "法兰绒": "wool",
    "真丝": "silk", "缎面": "silk", "醋酸": "silk",
    "亚麻": "linen", "苎麻": "linen",
    "牛仔": "denim",
    "牛皮": "leather", "羊皮": "leather", "PU革": "leather",
    "针织": "knit", "罗纹": "knit", "毛线": "knit",
    "涤纶": "synthetic", "锦纶": "synthetic", "尼龙": "synthetic",
    "氨纶": "synthetic", "羽绒": "synthetic", "棉服填充": "synthetic",
    "金属": "metal",
}

# docs/03 §7.5 的 neutral 判定阈值（改它就是改分类学，必须升 MAJOR）
NEUTRAL_SAT_MAX: float = 0.20
NEUTRAL_LIGHT_HIGH: float = 0.88
NEUTRAL_LIGHT_LOW: float = 0.12


# ---------------------------------------------------------------------------
# 颜色族 / 材质族（确定性，供 mock parser 与特征工程共用同一定义）
# ---------------------------------------------------------------------------


def color_family_from_hsv(hue: float, saturation: float, lightness: float) -> str:
    """docs/03 §7.5 的确定性 color_family 实现（唯一实现处）。"""
    if saturation <= NEUTRAL_SAT_MAX or lightness >= NEUTRAL_LIGHT_HIGH or lightness <= NEUTRAL_LIGHT_LOW:
        if lightness <= NEUTRAL_LIGHT_LOW:
            return "neutral_black"
        if lightness >= NEUTRAL_LIGHT_HIGH:
            return "neutral_white"
        return "neutral_mid"
    bucket = int((((hue % 360.0) + 15.0) % 360.0)) // 30
    bucket = min(bucket, len(CHROMATIC_FAMILIES) - 1)
    return CHROMATIC_FAMILIES[bucket]


def material_family_of(material: str | None) -> str:
    """细粒度材质 → material_family；未知一律 ``other``（不猜）。"""
    if not material:
        return "other"
    text = str(material).strip()
    if text in MATERIAL_FAMILY_BY_CN:
        return MATERIAL_FAMILY_BY_CN[text]
    if text in MATERIAL_FAMILIES:
        return text
    # 容忍 "羊毛混纺" 这类复合描述：按最长包含匹配，仍然确定性
    for token in sorted(MATERIAL_FAMILY_BY_CN, key=lambda t: (-len(t), t)):
        if token in text:
            return MATERIAL_FAMILY_BY_CN[token]
    return "other"


def _slot_value(slot: Any) -> str:
    return slot.value if isinstance(slot, Slot) else str(slot)


# ---------------------------------------------------------------------------
# 原始结构特征 x_struct（固定顺序，禁止依赖 dict 顺序）
# ---------------------------------------------------------------------------


def _garment_style_tags(outfit: Outfit) -> list[str]:
    tags: list[str] = []
    for g in outfit.items:
        for t in g.style_tags:
            if t not in tags:
                tags.append(t)
    return tags


def struct_features(outfit: Outfit) -> np.ndarray:
    """Outfit → x_struct（固定顺序拼接的 float64 向量）。

    分组：StyleTag 多热 | 颜色族多热 | 版型 one-hot(upper) | 版型 one-hot(lower)
          | 材质族多热 | 图案多热 | 长度 one-hot(下装/连衣裙) | slot 存在性 | 正式度
    """
    tags = set(_garment_style_tags(outfit))
    families = {color_family_from_hsv(c.hue, c.saturation, c.lightness)
                for g in outfit.items for c in g.colors}
    by_slot = {s: outfit.first(Slot(s)) for s in SLOT_ORDER}

    mats = {material_family_of(g.material) for g in outfit.items}
    patterns = {g.pattern for g in outfit.items if g.pattern}
    anchor = by_slot.get("dress") or by_slot.get("lower") or by_slot.get("upper")
    length = anchor.length if anchor and anchor.length else None

    formality_vals = [g.formality for g in outfit.items if g.formality is not None]
    mean_formality = (sum(formality_vals) / len(formality_vals)) if formality_vals else 0.0
    # contract.Garment.formality ∈ 1..5 → 归一到 [0,1]
    formality_norm = max(0.0, min(1.0, (mean_formality - 1.0) / 4.0))

    vec: list[float] = []
    vec.extend(1.0 if t in tags else 0.0 for t in STYLE_TAGS)
    vec.extend(1.0 if f in families else 0.0 for f in COLOR_FAMILIES)

    for slot in ("upper", "lower"):
        g = by_slot.get(slot)
        fit = g.fit if g is not None and g.fit in FITS else None
        vec.extend(1.0 if fit == f else 0.0 for f in FITS)

    vec.extend(1.0 if m in mats else 0.0 for m in MATERIAL_FAMILIES)
    vec.extend(1.0 if p in patterns else 0.0 for p in PATTERNS)
    vec.extend(1.0 if length == ln else 0.0 for ln in LENGTHS)
    vec.extend(1.0 if by_slot.get(s) is not None else 0.0 for s in SLOT_ORDER)
    vec.append(formality_norm)

    return np.asarray(vec, dtype=np.float64)


def profile_struct_features(profile: StyleProfile) -> np.ndarray:
    """仅有标签的画像 → x_struct（没有 outfit 时的降级路径）。"""
    tags = set(profile.top_tags or [])
    vec: list[float] = []
    vec.extend(1.0 if t in tags else 0.0 for t in STYLE_TAGS)
    vec.extend(0.0 for _ in COLOR_FAMILIES)
    vec.extend(0.0 for _ in FITS)
    vec.extend(0.0 for _ in FITS)
    vec.extend(0.0 for _ in MATERIAL_FAMILIES)
    vec.extend(0.0 for _ in PATTERNS)
    vec.extend(0.0 for _ in LENGTHS)
    vec.extend(0.0 for _ in SLOT_ORDER)
    vec.append(0.0)
    return np.asarray(vec, dtype=np.float64)


STRUCT_RAW_DIM: int = len(struct_features(Outfit(outfit_id="_probe_")))


# ---------------------------------------------------------------------------
# 确定性工具
# ---------------------------------------------------------------------------


def _l2(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if norm <= 1e-12:
        return np.zeros_like(vec)
    return vec / norm


def _hash_embedding(signature: str, dim: int) -> np.ndarray:
    """sha256 → dim 维伪随机单位向量（跨进程/跨平台稳定）。"""
    out = np.empty(dim, dtype=np.float64)
    for i in range(dim):
        digest = hashlib.sha256(f"{signature}#{i}".encode("utf-8")).digest()
        unit = int.from_bytes(digest[:8], "big") / float(1 << 64)
        out[i] = unit * 2.0 - 1.0
    return _l2(out)


def _outfit_content(outfit: Outfit) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for g in outfit.items:
        items.append({
            "slot": _slot_value(g.slot),
            "category": g.category,
            "colors": [c.hex for c in g.colors],
            "material": g.material,
            "fit": g.fit,
            "silhouette": g.silhouette,
            "length": g.length,
            "formality": g.formality,
            "style_tags": list(g.style_tags),
            "pattern": g.pattern,
        })
    items.sort(key=lambda d: (d["slot"], canonical_json(d)))
    return items


def _signature(outfit: Outfit) -> str:
    """outfit 的内容指纹：不含 role / profile / outfit_id 之外的易变信息。"""
    payload = {
        "image_ref": outfit.image_ref or "",
        "occasion": outfit.occasion,
        "season": outfit.season,
        "items": _outfit_content(outfit),
    }
    return canonical_json(payload)


def _build_projection(raw_dim: int, out_dim: int, seed: int) -> np.ndarray:
    """固定 seed 高斯投影；用 ``<f8`` 保证字节序跨平台一致（sha256 可复现）。"""
    rng = np.random.default_rng(seed)
    matrix = rng.standard_normal((out_dim, raw_dim)) / math.sqrt(raw_dim)
    return np.ascontiguousarray(matrix, dtype="<f8")


def projection_sha256(raw_dim: int = STRUCT_RAW_DIM, struct_dim: int = STRUCT_DIM,
                      seed: int = PROJECTION_SEED) -> str:
    """投影矩阵制品的 sha256（随 ruleset / embedding_manifest 发布，B-06）。"""
    matrix = _build_projection(raw_dim, struct_dim, seed)
    header = (f"seed={seed};img_dim={IMG_DIM};struct_dim={struct_dim};"
              f"raw_dim={raw_dim};mode={EMBEDDING_MODE}").encode("ascii")
    return hashlib.sha256(header + b"|" + matrix.tobytes()).hexdigest()


# ---------------------------------------------------------------------------
# TowerEncoder
# ---------------------------------------------------------------------------


class TowerEncoder:
    """搭配塔 / 用户塔编码器（不训练，确定性）。"""

    def __init__(self, *, img_dim: int = IMG_DIM, struct_dim: int = STRUCT_DIM,
                 seed: int = PROJECTION_SEED, alpha: float = ALPHA,
                 beta: float = BETA) -> None:
        if img_dim <= 0 or struct_dim <= 0:
            raise ValueError("img_dim / struct_dim 必须为正")
        self.img_dim = int(img_dim)
        self.struct_dim = int(struct_dim)
        self.alpha = float(alpha)
        self.beta = float(beta)
        self.seed = int(seed)
        self.raw_dim = STRUCT_RAW_DIM
        self._projection = _build_projection(self.raw_dim, self.struct_dim, self.seed)
        self._projection_sha = self._compute_projection_sha()
        # 真视觉块：512 → img_dim 的固定投影（同 seed 族，可复现、可发布指纹）
        self._visual_projection = _build_projection(VISUAL_RAW_DIM, self.img_dim,
                                                   self.seed + 7919)
        self._last_image_backend = "hash"
        self.visual_enabled = self._resolve_visual()

    def _resolve_visual(self) -> bool:
        """是否启用真视觉后端：模型存在 + 未被环境变量强制关闭。"""
        import os
        mode = (os.getenv("VISUAL_BACKEND") or "auto").lower()
        if mode in ("off", "hash", "0", "false"):
            return False
        if mode in ("on", "visual", "fashionclip"):
            return True
        from . import visual as _visual
        return _visual.available()

    @property
    def visual_projection_sha256(self) -> str:
        header = (f"seed={self.seed + 7919};raw_dim={VISUAL_RAW_DIM};"
                  f"out_dim={self.img_dim}").encode("ascii")
        return hashlib.sha256(header + b"|" + self._visual_projection.tobytes()).hexdigest()

    # -- 制品信息 ---------------------------------------------------------
    def _compute_projection_sha(self) -> str:
        header = (f"seed={self.seed};img_dim={self.img_dim};struct_dim={self.struct_dim};"
                  f"raw_dim={self.raw_dim};mode={EMBEDDING_MODE}").encode("ascii")
        return hashlib.sha256(header + b"|" + self._projection.tobytes()).hexdigest()

    @property
    def dim(self) -> int:
        return self.img_dim + self.struct_dim

    @property
    def projection_sha256(self) -> str:
        return self._projection_sha

    @property
    def embedding_version(self) -> str:
        return f"{EMBEDDING_VERSION}-{self.dim}-{self._projection_sha[:12]}"

    @property
    def config(self) -> dict[str, Any]:
        """随每份 Report 写入 ``meta.embedding_config``（docs/02 §6.2 可归因要求）。

        B-06：``projection_sha256`` 必须暴露，否则"固定投影"不可验证。
        """
        return {
            "mode": EMBEDDING_MODE,
            "dim": self.dim,
            "projection_sha256": self._projection_sha,
            "img_dim": self.img_dim,
            "struct_dim": self.struct_dim,
            "alpha": self.alpha,
            "beta": self.beta,
        }

    def manifest(self) -> dict[str, Any]:
        """比 ``config`` 更完整的审计清单（不在契约必填字段内，供 meta 扩展）。"""
        data = dict(self.config)
        data.update({
            "projection_seed": self.seed,
            "struct_raw_dim": self.raw_dim,
            "embedding_version": self.embedding_version,
            # 图像块后端：visual = 真视觉（FashionCLIP ONNX），hash = 内容指纹回退。
            # 放在 manifest 而非 config：config 的键集是契约固定项（B-06/B-11）。
            "image_backend": "visual" if self.visual_enabled else "hash",
            "visual_raw_dim": VISUAL_RAW_DIM,
            "visual_projection_sha256": (self.visual_projection_sha256
                                          if self.visual_enabled else None),
            "last_image_backend": self._last_image_backend,
        })
        if self.visual_enabled:
            try:
                from . import visual as _visual
                data["visual_model"] = _visual.manifest()
            except Exception:  # noqa: BLE001
                pass
        return data

    def projection_matrix(self) -> np.ndarray:
        """返回投影矩阵副本（测试/审计用；调用方修改不影响编码器）。"""
        return self._projection.copy()

    # -- 编码 -------------------------------------------------------------
    def _encode_struct(self, x_struct: np.ndarray) -> np.ndarray:
        return _l2(self._projection @ x_struct)

    def encode_outfit(self, outfit: Outfit) -> list[float]:
        """Outfit → 256 维 L2 归一化向量。确定性：只依赖 outfit 内容。

        图像块有两种后端（docs/10 F-10）：
          · ``visual``：outfit 带 ``image_embedding``（FashionCLIP 512 维）→ 固定投影到 img_dim
          · ``hash``  ：回退后端，用内容签名的 sha256 伪随机向量（无视觉语义，但零依赖可复现）
        两者输出维度一致，所以上层无感；后端选择记在 :meth:`config` 里以便审计。
        """
        vec = outfit.image_embedding
        if self.visual_enabled and vec is not None and len(vec) == VISUAL_RAW_DIM:
            raw = _l2(np.asarray(vec, dtype=np.float64))
            img = _l2(self._visual_projection @ raw)
            backend = "visual"
        else:
            img = _hash_embedding(_signature(outfit), self.img_dim)
            backend = "hash"
        self._last_image_backend = backend
        struct = self._encode_struct(struct_features(outfit))
        combined = np.concatenate([
            self.alpha * _l2(img),
            self.beta * _l2(struct),
        ])
        return [float(v) for v in _l2(combined)]

    def encode_profile(self, profile: StyleProfile) -> list[float]:
        """StyleProfile → 256 维 L2 归一化向量。

        优先使用 :func:`src.profiling.build_profile` 写入的
        ``preference_vector``（= 参考穿搭向量的加权平均，docs/02 §6.3）；
        缺失时退化为"仅结构块"（图像块置零后整体重归一化）。
        """
        pref = profile.preference_vector
        if pref is not None and len(pref) == self.dim:
            vec = np.asarray(pref, dtype=np.float64)
            return [float(v) for v in _l2(vec)]

        struct = self._encode_struct(profile_struct_features(profile))
        combined = np.concatenate([
            self.alpha * np.zeros(self.img_dim, dtype=np.float64),
            self.beta * _l2(struct),
        ])
        return [float(v) for v in _l2(combined)]

    def encode_many(self, outfits: Iterable[Outfit]) -> list[list[float]]:
        return [self.encode_outfit(o) for o in outfits]

    def __repr__(self) -> str:  # pragma: no cover - 调试友好
        return (f"TowerEncoder(mode={EMBEDDING_MODE}, dim={self.dim}, "
                f"projection_sha256={self._projection_sha[:12]}…)")
