"""照片预检：在花 API 额度之前，先看清系统会怎么处理你的照片。

检查项：
  1. 能否解码（HEIC 是常见坑 —— iPhone 默认格式，Pillow 读不了）
  2. 尺寸与 image token 成本（Qwen 按 32×32 像素/token 计费）
  3. 人形检测结果（主体框、人数、是否全身、遮挡、输入质量）
  4. 缩放后实际发送的 token 数与预估费用

用法：
    python scripts/check_photo.py "D:/photos/a.jpg" "D:/photos/b.jpg"
    python scripts/check_photo.py "D:/photos/" --parse          # 整个目录 + 完整解析
    python scripts/check_photo.py "D:/photos/a.jpg" --backend none   # 跳过人形检测
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.parsing.person_crop import (crop_main_subject, fit_for_vlm, image_tokens_estimate,
                                     load_image, quality_score)

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic", ".heif"}
# Qwen-VL 定价（元 / 千 token，参考值，仅用于量级感知）
PRICE_PER_1K_TOKENS = 0.008


def collect(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        path = Path(p).expanduser()
        if path.is_dir():
            out.extend(sorted(q for q in path.iterdir() if q.suffix.lower() in IMG_EXT))
        elif path.is_file():
            out.append(path)
        else:
            print(f"  [跳过] 不存在：{p}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="照片预检")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--backend", default=None, choices=["auto", "onnx", "vlm", "none"])
    ap.add_argument("--parse", action="store_true", help="再做一次完整解析（会调用 API）")
    args = ap.parse_args(argv)

    files = collect(args.paths)
    if not files:
        print("没有找到图片。支持的扩展名：", ", ".join(sorted(IMG_EXT)))
        return 1

    from src.config import get_settings
    settings = get_settings()
    print("=" * 78)
    print(f"服务配置：offline={settings.offline_mode}  vlm_ready={settings.vlm_ready}  "
          f"model={settings.vlm_model}")
    print("=" * 78)

    total_tokens = 0
    for f in files:
        print(f"\n【{f.name}】  {f.stat().st_size/1024:.0f} KB")
        if f.suffix.lower() in (".heic", ".heif"):
            print("  ⚠️ HEIC/HEIF：Pillow 默认不支持 → 系统会判为 IMAGE_UNDECODABLE 并降级 mock。")
            print("     解决：用 画图/预览/微信 另存为 JPG，或 pip install pillow-heif")
        img = load_image(str(f))
        if img is None:
            print("  ❌ 无法解码 → 这张图会被丢弃（并记 IMAGE_UNDECODABLE）")
            continue
        w, h = img.size
        raw_tokens = image_tokens_estimate(img)
        fitted = fit_for_vlm(img)
        fw, fh = fitted.size
        tok = image_tokens_estimate(fitted)
        total_tokens += tok
        print(f"  解码成功：{w}×{h}  格式 {img.mode}")
        print(f"  token：原图 ≈ {raw_tokens}  →  缩放后 {fw}×{fh} ≈ **{tok}**"
              f"  （省 {raw_tokens - tok}）")

        cropped, info = crop_main_subject(str(f), settings,
                                         backend=args.backend or None)
        print(f"  人形检测（{info.backend}）：bbox={info.bbox}  人数={info.person_count}  "
              f"全身={info.is_full_body}")
        print(f"  主体占比={info.subject_area_ratio}  输入质量={quality_score(info)}")
        print(f"  提示：{info.warnings or '无'}")
        if cropped is not None:
            cw, ch = cropped.size
            print(f"  裁剪后 {cw}×{ch}（长边仍会缩放到 ≤1024 再发送）")

        if args.parse and settings.vlm_ready:
            from src.parsing.qwen_vlm import QwenVLMParser
            parser = QwenVLMParser(settings=settings)
            res = parser.parse_outfit([str(f)], "commute", None)
            o = res.outfit
            print(f"  解析出 {len(o.items)} 件单品：")
            for g in o.items:
                print(f"    {g.slot.value:10s} {g.category:8s} "
                      f"{g.primary_color.hex if g.primary_color else '—'}  "
                      f"fit={g.fit}  formality={g.formality}")
            print(f"  视觉向量：{'有 512 维' if o.image_embedding else '无（回退哈希）'}")
            for w_ in res.warnings:
                if w_.startswith(("SUBJECT", "VISUAL", "IMAGE_")):
                    print(f"    · {w_}")

    print("\n" + "=" * 78)
    print(f"合计 {len(files)} 张，缩放后共 ≈ {total_tokens} image tokens，"
          f"单次解析合计约 {total_tokens/1000*PRICE_PER_1K_TOKENS:.3f} 元（参考价）")
    print("提示：建画像会把每张参考图各解析一次；评审再按提交张数计一次。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
