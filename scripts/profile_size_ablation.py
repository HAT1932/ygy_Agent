"""参考穿搭数量消融实验：多加参考真的更好吗？边际收益在第几张归零？

设计：
  · 用 10 个风格互不相同的搭配作为参考池，按风格多样性递增顺序加入
  · n = 1..10：只用前 n 张建画像
  · 固定探针（10 套搭配 × 2 场合 = 20 次评审），比较各 n 与全量画像的一致性
  · 另做稀释实验：3 张同类 vs 3 张混杂，看画像被平均掉的程度
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, r"E:\Chuang_Yi_skill")
os.environ["OFFLINE_MODE"] = "true"
os.environ["PERSON_CROP_BACKEND"] = "none"
os.environ["VISUAL_BACKEND"] = "hash"          # 固定哈希后端，排除视觉后端干扰

import statistics

import numpy as np

from src.pipeline import DemoService

REF_ORDER = [
    "commute_polished",   # 1 通勤知性
    "mono_gradient",      # 2 极简同色
    "date_french",        # 3 法式
    "casual_street",      # 4 街头休闲
    "formal_suit",        # 5 正装
    "sport_active",       # 6 运动
    "accessory_overload", # 7 配饰堆叠
    "color_clash",        # 8 撞色
    "season_mismatch",    # 9 季节错配
    "formal_missing",     # 10 正装缺件
]
OCCASIONS = ("commute", "formal")
NOTE = ("极差越大 = 对不同搭配区分越明显；"
        "极化度越低 = 画像越面面俱到 = 越不个人化")


def probe_scores(svc: DemoService, profile_id: str) -> list[tuple[float, str]]:
    out = []
    for sc in REF_ORDER:
        for occ in OCCASIONS:
            r = svc.create_report(profile_id, image_refs=[f"demo/{sc}"], occasion=occ)
            out.append((r.total_score, r.verdict.value))
    return out


def cos(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


def main() -> int:
    svc = DemoService(persist=False)
    rows = []
    for n in range(1, len(REF_ORDER) + 1):
        profile, _ = svc.create_profile([f"demo/{s}" for s in REF_ORDER[:n]])
        rows.append({
            "n": n,
            "confidence": profile.confidence,
            "n_tags": len(profile.tags),
            "top1_w": profile.tags[0].weight if profile.tags else 0.0,
            "top3": " / ".join(t.tag for t in profile.tags[:3]),
            "vec": np.asarray(svc.encoder.encode_profile(profile)),
            "scores": probe_scores(svc, profile.profile_id),
        })
    full = rows[-1]

    print("=" * 92)
    print("实验 1 · 参考数量消融（置信度 / 标签极化 / 与「10 张全量画像」的一致性）")
    print("=" * 92)
    print(f"{'n':>2} {'置信度':>6} {'标签数':>5} {'top1权重':>8} {'收敛度':>7} "
          f"{'verdict一致':>11} {'平均分差':>8}  top3")
    print("-" * 92)
    for r in rows:
        c = cos(r["vec"], full["vec"])
        agree = sum(1 for a, b in zip(r["scores"], full["scores"])
                    if a[1] == b[1]) / len(full["scores"])
        mdiff = statistics.fmean(abs(a[0] - b[0]) for a, b in zip(r["scores"], full["scores"]))
        print(f"{r['n']:>2} {r['confidence']:>6.2f} {r['n_tags']:>5} {r['top1_w']:>8.3f} "
              f"{c:>7.3f} {agree:>10.0%} {mdiff:>8.2f}  {r['top3']}")
    print("-" * 92)

    print("\n边际变化（加入第 n 张时，画像相对上一版的变化；越接近 1 = 新信息越少）:")
    for i in range(1, len(rows)):
        c = cos(rows[i - 1]["vec"], rows[i]["vec"])
        gain = rows[i]["confidence"] - rows[i - 1]["confidence"]
        print(f"  {i:>2} → {i+1:>2} 张：画像余弦 {c:.3f}   置信度 {gain:+.3f}")

    print("\n" + "=" * 92)
    print("实验 2 · 稀释效应（参考集是「同类」还是「混杂」）")
    print("=" * 92)
    groups = {
        "3 张同类（通勤/极简/正装）": ["commute_polished", "mono_gradient", "formal_suit"],
        "3 张混杂（通勤+运动+撞色）": ["commute_polished", "sport_active", "color_clash"],
        "6 张混杂": REF_ORDER[:6],
    }
    probe_uniform = ["commute_polished", "sport_active", "color_clash", "formal_suit"]
    for label, refs in groups.items():
        s2 = DemoService(persist=False)
        p, _ = s2.create_profile([f"demo/{s}" for s in refs])
        ws = [t.weight for t in p.tags[:4]]
        polar = round(ws[0] - ws[2], 3) if len(ws) >= 3 else None
        sc = [s2.create_report(p.profile_id, image_refs=[f"demo/{s}"], occasion="commute").total_score
              for s in probe_uniform]
        print(f"\n  {label}")
        print(f"    置信度 {p.confidence:.2f}  标签数 {len(p.tags)}  "
              f"top3={[t.tag for t in p.tags[:3]]}")
        print(f"    极化度(top1-top3)={polar}   探针分数 {sc}   极差={max(sc)-min(sc):.1f}")
    print(f"\n说明：{NOTE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
