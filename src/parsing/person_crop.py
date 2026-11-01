"""人形检测与主体裁剪 —— 把「整张图」变成「主要人物的干净裁剪」。

为什么需要（docs/10 实测结论）：
    · 背景杂乱：VLM 表现其实不错（3/3 命中、色差 ≤8、还会自报忽略了哪些背景物）
    · **遮挡**：是真风险 —— 手提包挡在腰前，VLM 把包的颜色当成了下装（色差 159）
    · 裁切：单品缺失（鞋没进画面），但系统照常给强判断
    · 多人物：本次靠"第二人是灰度"侥幸躲过，真人合照风险更高

策略：先用检测/定位拿到「主要人物框」，只把这块 crop 交给 VLM 精析。
      这样能同时消除背景干扰、第二人干扰，并让"主体占比 / 是否全身 / 人数"
      变成可量化的输入质量信号（进入 assessability 门控）。

三个后端（可插拔，按可用性自动降级）：
    onnx  : models/yolov8n.onnx（YOLOv8 单类 person）—— 最稳，零网络
    vlm   : 用百炼 VLM 做 grounding（返回归一化 bbox）—— 有 key 就能用
    none  : 直接用原图（离线性兜底）
"""
from __future__ import annotations

import base64
import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ONNX = ROOT / "models" / "yolov8n.onnx"

# 输入质量门槛（低于任一即应提示用户重拍，而不是硬打分）
MIN_SUBJECT_AREA_RATIO = 0.06      # 主体占画面比例下限
MIN_SIDE_PX = 320                  # 裁剪后最短边下限
FULL_BODY_MARGIN = 0.06            # 判定"全身"时 bbox 距上下边缘的容差（归一化）


@dataclass
class SubjectInfo:
    bbox: tuple[float, float, float, float] | None = None   # 归一化 x0,y0,x1,y1
    person_count: int | None = None
    subject_area_ratio: float | None = None
    is_full_body: bool | None = None
    backend: str = "none"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "bbox": list(self.bbox) if self.bbox else None,
            "person_count": self.person_count,
            "subject_area_ratio": self.subject_area_ratio,
            "is_full_body": self.is_full_body,
            "backend": self.backend,
            "warnings": list(self.warnings),
        }


# --------------------------------------------------------------------------
# 图像载入
# --------------------------------------------------------------------------
def load_image(ref: str) -> Image.Image | None:
    """支持本地路径 / data URI / http(s)。失败返回 None（调用方降级为原图直送）。"""
    text = str(ref).strip()
    try:
        if text.startswith("data:image/"):
            payload = text.split(",", 1)[1]
            return Image.open(io.BytesIO(base64.b64decode(payload))).convert("RGB")
        if text.startswith(("http://", "https://")):
            import httpx
            r = httpx.get(text, timeout=30, follow_redirects=True)
            r.raise_for_status()
            return Image.open(io.BytesIO(r.content)).convert("RGB")
        p = Path(text)
        if p.is_file():
            return Image.open(p).convert("RGB")
    except Exception:  # noqa: BLE001
        return None
    return None


# --------------------------------------------------------------------------
# 后端 1：YOLOv8 ONNX（person 类）
# --------------------------------------------------------------------------
_YOLO_SESSION = None
_YOLO_INPUT = None


def _get_yolo(path: Path):
    global _YOLO_SESSION, _YOLO_INPUT
    if _YOLO_SESSION is None:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.log_severity_level = 3
        _YOLO_SESSION = ort.InferenceSession(str(path), sess_options=so,
                                            providers=["CPUExecutionProvider"])
        _YOLO_INPUT = _YOLO_SESSION.get_inputs()[0]
    return _YOLO_SESSION, _YOLO_INPUT


def _letterbox(img: Image.Image, size: int = 640):
    w, h = img.size
    scale = min(size / w, size / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    pad = ((size - nw) // 2, (size - nh) // 2)
    canvas.paste(img.resize((nw, nh), Image.BILINEAR), pad)
    arr = np.asarray(canvas, dtype=np.float32) / 255.0
    arr = np.transpose(arr, (2, 0, 1))[None, ...]
    return arr, scale, pad


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_thr: float = 0.45) -> list[int]:
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        a1 = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        a2 = (boxes[order[1:], 2] - boxes[order[1:], 0]) * (boxes[order[1:], 3] - boxes[order[1:], 1])
        iou = inter / np.maximum(a1 + a2 - inter, 1e-9)
        order = order[1:][iou <= iou_thr]
    return keep


def detect_onnx(img: Image.Image, path: Path | None = None,
                conf_thr: float = 0.25) -> SubjectInfo:
    model_path = Path(path or os.getenv("YOLO_ONNX_PATH") or DEFAULT_ONNX)
    if not model_path.exists():
        return SubjectInfo(backend="none", warnings=["YOLO_ONNX_MISSING"])
    try:
        sess, inp = _get_yolo(model_path)
        blob, scale, pad = _letterbox(img)
        name = inp.name if hasattr(inp, "name") else "images"
        out = sess.run(None, {name: blob})[0]
        # YOLOv8 输出: (1, 4+nc, 8400) 或 (1, 8400, 4+nc)
        pred = out[0]
        if pred.shape[0] < pred.shape[1]:
            pred = pred.T
        boxes_xywh = pred[:, :4]
        cls_scores = pred[:, 4:]
        person_scores = cls_scores[:, 0]           # COCO class 0 = person
        mask = person_scores >= conf_thr
        if not mask.any():
            return SubjectInfo(backend="onnx", person_count=0,
                               warnings=["NO_PERSON_DETECTED"])
        b = boxes_xywh[mask]
        s = person_scores[mask]
        # cx,cy,w,h → x0,y0,x1,y1（letterbox 坐标）
        xy = np.empty_like(b)
        xy[:, 0] = b[:, 0] - b[:, 2] / 2
        xy[:, 1] = b[:, 1] - b[:, 3] / 2
        xy[:, 2] = b[:, 0] + b[:, 2] / 2
        xy[:, 3] = b[:, 1] + b[:, 3] / 2
        keep = _nms(xy, s)
        W, H = img.size
        # 反 letterbox → 原图归一化
        pad_x, pad_y = pad
        norm = []
        for i in keep:
            x0 = (xy[i, 0] - pad_x) / scale / W
            y0 = (xy[i, 1] - pad_y) / scale / H
            x1 = (xy[i, 2] - pad_x) / scale / W
            y1 = (xy[i, 3] - pad_y) / scale / H
            norm.append((max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1)))
        # 取面积最大的那个人（主体），并统计人数
        areas = [(b_[2] - b_[0]) * (b_[3] - b_[1]) for b_ in norm]
        main = norm[int(np.argmax(areas))]
        count = len(norm)
        warns: list[str] = []
        if count > 1:
            warns.append(f"MULTIPLE_PERSONS({count})")
        info = SubjectInfo(bbox=main, person_count=count,
                           subject_area_ratio=round(areas[int(np.argmax(areas))], 4),
                           backend="onnx", warnings=warns)
        info.is_full_body = (main[1] <= FULL_BODY_MARGIN) and (main[3] >= 1.0 - FULL_BODY_MARGIN)
        if not info.is_full_body:
            info.warnings.append("NOT_FULL_BODY")
        return info
    except Exception as exc:  # noqa: BLE001
        return SubjectInfo(backend="onnx", warnings=[f"YOLO_FAIL:{type(exc).__name__}"])


# --------------------------------------------------------------------------
# 后端 2：VLM grounding（需要 DASHSCOPE_API_KEY 且非离线）
# --------------------------------------------------------------------------
_GROUND_PROMPT = (
    "定位照片里的**主要人物**（占比最大、位于画面中心的人）。只输出 JSON："
    '{"bbox":[x0,y0,x1,y1],"person_count":整数,"full_body":true|false,'
    '"occluded":[遮挡该人物身体的物体]}'
    " bbox 用归一化坐标（0~1，左上到右下）。没有人像则 bbox 为 null。"
)


def detect_vlm(img: Image.Image, settings) -> SubjectInfo:
    if not settings.dashscope_api_key or not settings.vlm_ready:
        return SubjectInfo(backend="none", warnings=["VLM_GROUNDING_UNAVAILABLE"])
    import httpx
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    body = {
        "model": settings.vlm_model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": _GROUND_PROMPT},
        ]}],
        "temperature": 0, "max_tokens": 300,
        "enable_thinking": False,
        "response_format": {"type": "json_object"},
    }
    try:
        r = httpx.post(f"{settings.dashscope_base_url.rstrip('/')}/chat/completions",
                       headers={"Authorization": f"Bearer {settings.dashscope_api_key}"},
                       json=body, timeout=60)
        if r.status_code != 200:
            return SubjectInfo(backend="vlm", warnings=[f"GROUND_HTTP_{r.status_code}"])
        data = json.loads(r.json()["choices"][0]["message"]["content"])
        bbox = data.get("bbox")
        warns: list[str] = []
        count = data.get("person_count")
        if bbox is None:
            return SubjectInfo(backend="vlm", person_count=count,
                               warnings=["NO_PERSON_DETECTED"])
        x0, y0, x1, y1 = (float(v) for v in bbox)
        # 模型经常无视"归一化"要求直接给像素坐标 → 按数量级自动判别并换算
        if max(x0, y0, x1, y1) > 1.5:
            W, H = img.size
            x0, x1 = x0 / W, x1 / W
            y0, y1 = y0 / H, y1 / H
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(1.0, x1), min(1.0, y1)
        if x1 - x0 <= 0.02 or y1 - y0 <= 0.02:
            return SubjectInfo(backend="vlm", person_count=count,
                               warnings=["DEGENERATE_BBOX"])
        if count and count > 1:
            warns.append(f"MULTIPLE_PERSONS({count})")
        occ = data.get("occluded") or []
        if occ:
            warns.append("OCCLUDED:" + ",".join(str(o) for o in occ[:3]))
        info = SubjectInfo(bbox=(x0, y0, x1, y1), person_count=count,
                           subject_area_ratio=round((x1 - x0) * (y1 - y0), 4),
                           backend="vlm", warnings=warns)
        info.is_full_body = bool(data.get("full_body"))
        if not info.is_full_body:
            info.warnings.append("NOT_FULL_BODY")
        return info
    except Exception as exc:  # noqa: BLE001
        return SubjectInfo(backend="vlm", warnings=[f"GROUND_FAIL:{type(exc).__name__}"])


# --------------------------------------------------------------------------
# 裁剪主入口
# --------------------------------------------------------------------------
def crop_main_subject(ref: str, settings, *, backend: str | None = None,
                      pad_ratio: float = 0.06
                      ) -> tuple[Image.Image | None, SubjectInfo]:
    """返回 (裁剪后的图像 or None, 主体信息)。None 表示调用方应直接用原图。"""
    img = load_image(ref)
    if img is None:
        return None, SubjectInfo(backend="none", warnings=["IMAGE_UNREADABLE"])

    mode = (backend or os.getenv("PERSON_CROP_BACKEND") or "auto").lower()
    info = SubjectInfo(backend="none")
    if mode in ("auto", "onnx"):
        info = detect_onnx(img)
        if info.bbox is None and mode == "auto":
            info = detect_vlm(img, settings)
    elif mode == "vlm":
        info = detect_vlm(img, settings)
    elif mode == "none":
        return None, SubjectInfo(backend="none")

    if info.bbox is None:
        info.warnings.append("CROP_SKIPPED_USE_FULL_IMAGE")
        return None, info

    W, H = img.size
    x0, y0, x1, y1 = info.bbox
    pad_x, pad_y = (x1 - x0) * pad_ratio, (y1 - y0) * pad_ratio
    box = (max(0, int((x0 - pad_x) * W)), max(0, int((y0 - pad_y) * H)),
           min(W, int((x1 + pad_x) * W)), min(H, int((y1 + pad_y) * H)))
    cropped = img.crop(box)
    if min(cropped.size) < MIN_SIDE_PX:
        cropped = cropped.resize((max(MIN_SIDE_PX, cropped.width),
                                  max(MIN_SIDE_PX, cropped.height)), Image.BICUBIC)
        info.warnings.append("CROP_UPSCALED")
    if (info.subject_area_ratio or 1.0) < MIN_SUBJECT_AREA_RATIO:
        info.warnings.append("SUBJECT_TOO_SMALL")
    return cropped, info


def fit_for_vlm(img: Image.Image, max_side: int | None = None) -> Image.Image:
    """把图缩到「长边 ≤ max_side」，控制 image token 成本。

    为什么必须做：Qwen 按 32×32 像素切 token，一张 3024×4032 的手机照片
    ≈ (3024/32)×(4032/32) ≈ **11,900 image tokens**，是 640² 的 30 倍 —— 又慢又贵。
    长边 1024 时约 (1024/32)×(768/32) ≈ 768 tokens，画质对服装识别足够。
    """
    limit = int(max_side or os.getenv("VLM_MAX_SIDE") or 1024)
    w, h = img.size
    if max(w, h) <= limit:
        return img
    scale = limit / max(w, h)
    return img.resize((max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                      Image.LANCZOS)


def image_tokens_estimate(img: Image.Image) -> int:
    """按 Qwen 的 32×32 像素/token 规则估算 image token（用于成本预警）。"""
    w, h = img.size
    return max(1, (w + 31) // 32) * max(1, (h + 31) // 32)


def quality_score(info: SubjectInfo) -> float:
    """把输入质量折成 0..1，供 pipeline 的 assessability 门控使用。

    这是"证据不足就不下判断"原则的落地（docs/00-baseline §5.3 / R-01）：
    单人、全身、主体够大 → 1.0；主体过小或非全身 → 显著下降。
    """
    if info.backend == "none" and info.bbox is None:
        return 1.0        # 未做检测：不额外惩罚（保持旧行为）
    score = 1.0
    if info.person_count and info.person_count > 1:
        score -= 0.25
    if info.is_full_body is False:
        score -= 0.25
    if (info.subject_area_ratio or 1.0) < MIN_SUBJECT_AREA_RATIO:
        score -= 0.30
    if any(w.startswith("OCCLUDED") for w in info.warnings):
        score -= 0.15
    if info.bbox is None:
        # 「画面里没检测到人」有两类情况，不能一概而论：
        #   · 平铺摆拍图（我们的 demo 素材就是）—— 合法输入，仅轻微降权
        #   · 实在读不出主体 —— 证据不足，重罚
        no_person = any(w.startswith("NO_PERSON_DETECTED") for w in info.warnings)
        score = 0.85 if no_person else 0.5
    return round(max(0.0, min(1.0, score)), 3)
