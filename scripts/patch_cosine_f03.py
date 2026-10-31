"""F-03 标定补丁：把「余弦类阈值」按当前 embedding 后端重新标定。

背景（docs/10 F-03 实测）：
    hash embedding 的余弦区间被压缩：
        搭配↔搭配   : min 0.007 / p50 0.168 / p75 0.203 / max 0.289
        画像↔搭配   : min 0.281 / p50 0.445 /          max 0.585

而 docs/03 的阈值是按语义 embedding（FashionCLIP 量级）写的：
    R-STYLE-005  阈值 0.55  → 在 hash 模式下约 70% 的搭配都判"偏移过大"，系统性过罚
    R-STYLE-009  窗口 0.60–0.75 → 上限高于观测最大值 0.585，**永远不可能触发**（死的 boost）

本补丁把它们调到 hash 模式的合理分位：
    R-STYLE-005  0.55 → 0.35   （约 p10，只罚真正偏离的）
    R-STYLE-009  0.60–0.75 → 0.45–0.58（约 p50–max，用于"略有偏离但踩到探索标签"）

⚠️ 换成语义 embedding（FashionCLIP / dashscope）后必须**重新标定**这两个数，
    或改用 docs/10 F-03 推荐的方案 A（在 derived.cosine_uv 之前做归一化，让规则与后端解耦）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "rules"


def patch(rel: str, old: str, new: str) -> bool:
    p = R / rel
    text = p.read_text(encoding="utf-8")
    if old not in text:
        print(f"  SKIP {p.name}: 未匹配（可能已标定）")
        return False
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"  PATCHED {p.name}")
    return True


def main() -> int:
    ok = patch(
        "style/R-STYLE-005.yaml",
        "  field: derived.cosine_uv\n  op: lt\n  value: 0.55\n",
        "  field: derived.cosine_uv\n  op: lt\n"
        "  # F-03 后端标定：hash 模式下画像↔搭配余弦 p10≈0.35（原 0.55 会误罚约 70%）\n"
        "  value: 0.35\n",
    )
    ok &= patch(
        "style/R-STYLE-009.yaml",
        "    op: between\n    value:\n    - 0.6\n    - 0.75\n",
        "    op: between\n"
        "    # F-03 后端标定：hash 模式观测上界 0.585，原窗口 0.60–0.75 永远不可达\n"
        "    value:\n    - 0.45\n    - 0.58\n",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
