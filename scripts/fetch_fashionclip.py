"""从 hf-mirror 下载 FashionCLIP 的 ONNX 权重与预处理文件（HF 官方站被墙，mirror 可达）。

只下必要文件：
  onnx/model.onnx            图像+文本双塔（我们只用图像侧，文本侧喂 dummy）
  config.json                模型结构
  preprocessor_config.json   归一化参数（mean/std/size）
  tokenizer.json / vocab.json / merges.txt / tokenizer_config.json / special_tokens_map.json
"""
from __future__ import annotations

import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://hf-mirror.com/patrickjohncyr/fashion-clip".replace("cyr", "cyh")
OUT = Path(r"E:\Chuang_Yi_skill\models\fashion-clip")

FILES = [
    "config.json",
    "preprocessor_config.json",
    "tokenizer.json",
    "vocab.json",
    "merges.txt",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "onnx/model.onnx",
]


def fetch(rel: str) -> None:
    url = f"{BASE}/resolve/main/{rel}"
    dst = OUT / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size > 0:
        print(f"[skip] {rel} ({dst.stat().st_size/1e6:.1f} MB)", flush=True)
        return
    tmp = dst.with_suffix(dst.suffix + ".part")
    t0 = time.time()
    # hf-mirror 会拒绝 urllib 默认 UA（403），必须带浏览器 UA
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        last = 0.0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total and (time.time() - last > 5):
                last = time.time()
                print(f"[{rel}] {got/1e6:.1f}/{total/1e6:.1f} MB "
                      f"({got/max(time.time()-t0,1e-9)/1e6:.2f} MB/s)", flush=True)
    tmp.replace(dst)
    print(f"[ok] {rel} {dst.stat().st_size/1e6:.1f} MB in {time.time()-t0:.1f}s", flush=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for rel in FILES:
        try:
            fetch(rel)
        except Exception as exc:  # noqa: BLE001
            print(f"[fail] {rel}: {type(exc).__name__}: {exc}", flush=True)
            return 1
    print("ALL DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
