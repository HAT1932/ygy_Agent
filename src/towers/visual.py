"""视觉编码器（FashionCLIP · ONNX Runtime）—— 替换 160 维「伪视觉」哈希。

背景（docs/10 F-10）：
    原实现的 ``img_emb`` 是「搭配文本签名的 sha256 伪随机单位向量」，确定性可复现
    但**没有视觉语义**。后果：cosine 被压缩到 0.007–0.29，双塔只能靠 96 维结构化
    特征排序，"视觉像不像"完全测不出来。

本模块给出真视觉编码：
    · 模型：patrickjohncyh/fashion-clip（CLIP ViT-B/32，时尚域微调），512 维输出
    · 推理：onnxruntime CPU，**不需要 torch**（省 ~250MB 依赖与 2GB 内存）
    · 输入：224×224，CLIP 标准归一化（mean/std 来自 preprocessor_config.json）
    · 输出：L2 归一化的 512 维向量

设计约束：
    · 懒加载 + 单例：首次调用才建 session（模型 606MB，建 session 约 1–2s）
    · 失败必须可降级：模型缺失/加载失败 → 返回 None，调用方回退哈希后端
    · 制品可追溯：暴露模型 sha256 与预处理参数，写进 embedding manifest
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "models" / "fashion-clip"
IMAGE_SIZE = 224
EMBED_DIM = 512

# CLIP 文本塔只用于让 ONNX 图跑通（我们只要图像侧），dummy 序列即可
_CLIP_BOS, _CLIP_EOS, _CLIP_CTX = 49406, 49407, 77

_SESSION = None
_SESSION_FAILED = False
_PREPROCESS: dict | None = None
_MODEL_SHA: str | None = None


def model_dir() -> Path:
    return Path(os.getenv("FASHIONCLIP_DIR") or DEFAULT_DIR)


def available() -> bool:
    p = model_dir()
    return (p / "onnx" / "model.onnx").exists() and (p / "preprocessor_config.json").exists()


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest() -> dict:
    """制品指纹：模型 sha256 + 预处理参数 + 版本标记（可随 ruleset 发布，B-06）。"""
    global _MODEL_SHA
    p = model_dir() / "onnx" / "model.onnx"
    if _MODEL_SHA is None and p.exists():
        _MODEL_SHA = _sha256_of(p)
    pre = _load_preprocess() or {}
    return {
        "backend": "fashion-clip-onnx",
        "model": "patrickjohncyh/fashion-clip",
        "embed_dim": EMBED_DIM,
        "image_size": IMAGE_SIZE,
        "model_sha256": _MODEL_SHA,
        "image_mean": pre.get("image_mean"),
        "image_std": pre.get("image_std"),
        "runtime": "onnxruntime-cpu",
    }


def _load_preprocess() -> dict | None:
    global _PREPROCESS
    if _PREPROCESS is None:
        p = model_dir() / "preprocessor_config.json"
        if not p.exists():
            return None
        _PREPROCESS = json.loads(p.read_text(encoding="utf-8"))
    return _PREPROCESS


def _l2(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _get_session():
    """懒建 ONNX session；失败一次后不再重试（避免每张图都阻塞）。"""
    global _SESSION, _SESSION_FAILED
    if _SESSION is not None or _SESSION_FAILED:
        return _SESSION
    try:
        import onnxruntime as ort
        p = model_dir() / "onnx" / "model.onnx"
        if not p.exists():
            _SESSION_FAILED = True
            return None
        so = ort.SessionOptions()
        so.log_severity_level = 3
        so.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 4) // 2))
        _SESSION = ort.InferenceSession(str(p), sess_options=so,
                                        providers=["CPUExecutionProvider"])
    except Exception:  # noqa: BLE001
        _SESSION_FAILED = True
        _SESSION = None
    return _SESSION


def preprocess(img: Image.Image) -> np.ndarray:
    """短边缩放到 224 → 中心裁 224×224 → CLIP 归一化 → NCHW float32。"""
    pre = _load_preprocess() or {}
    size = int(pre.get("crop_size") or pre.get("size") or IMAGE_SIZE) \
        if not isinstance(pre.get("crop_size"), dict) else IMAGE_SIZE
    w, h = img.size
    scale = size / min(w, h)
    nw, nh = max(size, int(round(w * scale))), max(size, int(round(h * scale)))
    resized = img.resize((nw, nh), Image.BICUBIC)
    left, top = (nw - size) // 2, (nh - size) // 2
    crop = resized.crop((left, top, left + size, top + size))
    arr = np.asarray(crop, dtype=np.float32) / 255.0
    mean = np.asarray(pre.get("image_mean") or [0.48145466, 0.4578275, 0.40821073],
                      dtype=np.float32)
    std = np.asarray(pre.get("image_std") or [0.26862954, 0.26130258, 0.27577711],
                     dtype=np.float32)
    arr = (arr - mean) / std
    return np.transpose(arr, (2, 0, 1))[None, ...].astype(np.float32)


def encode_image(img: Image.Image) -> list[float] | None:
    """图像 → 512 维 L2 归一化向量。模型不可用时返回 None（调用方回退哈希）。"""
    sess = _get_session()
    if sess is None:
        return None
    try:
        ids = np.zeros((1, _CLIP_CTX), dtype=np.int64)
        ids[0, 0] = _CLIP_BOS
        ids[0, 1] = _CLIP_EOS
        mask = np.zeros((1, _CLIP_CTX), dtype=np.int64)
        mask[0, :2] = 1
        feeds = {"pixel_values": preprocess(img), "input_ids": ids, "attention_mask": mask}
        names = {i.name for i in sess.get_inputs()}
        out = sess.run(["image_embeds"], {k: v for k, v in feeds.items() if k in names})[0]
        return _l2(np.asarray(out[0], dtype=np.float64)).astype(float).tolist()
    except Exception:  # noqa: BLE001
        return None


def cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    den = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(va @ vb / den) if den > 0 else 0.0
