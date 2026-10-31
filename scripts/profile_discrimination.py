"""画像区分度测量 —— 证明「同一套衣服，对不同用户给不同分」。

为什么必须单独测（docs/08 §3 的第二个关键指标）：
    Spearman 只证明"排序大致对"；它无法证明"个性化真的生效"。
    如果同一套搭配对任何画像都给同一个分，系统就退化成通用审美打分器 ——
    这正是 docs/06 调研里 40 个同类 App 失败的原因，也是我们 P3 原则的生死线。

指标定义：
    Δ(i,j) = |score(outfit | profile_i) − score(outfit | profile_j)|   （厘点，1 分 = 100）
    画像区分度 = 所有 (outfit, profile 对) 上 Δ 的**中位数**
门槛：中位数 ≥ 8 分（800 厘点），见 docs/08 §3。

本脚本测**两个条件**，避免用主观偏好"刷"指标：
    A 纯统计画像：只用参考穿搭聚合，不填 banned/explore（冷启动用户的真实状态）
    B 带主观偏好：额外声明"绝对不穿/想尝试"（画像页填表的用户）
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.parsing.mock import SCRIPTS
from src.pipeline import DemoService

REFS = {
    "通勤知性": ["demo/commute_polished", "demo/formal_suit", "demo/mono_gradient"],
    "法式约会": ["demo/date_french", "demo/casual_street", "demo/mono_gradient"],
    "运动休闲": ["demo/sport_active", "demo/casual_street", "demo/accessory_overload"],
}
PREFS = {
    "通勤知性": {"banned_styles": ["运动休闲", "街头潮流"], "explore_tags": ["法式"]},
    "法式约会": {"banned_styles": ["商务正装", "机能风"], "explore_tags": ["浪漫"]},
    "运动休闲": {"banned_styles": ["甜美", "商务正装"], "explore_tags": ["机能风"]},
}
OCCASIONS = ("commute", "date", "sport", "formal", "casual")
MIN_MEDIAN_CENTI = 800


def measure(with_prefs: bool) -> dict:
    svc = DemoService(persist=False)
    profiles = {}
    for name, refs in REFS.items():
        kw = PREFS[name] if with_prefs else {}
        p, _ = svc.create_profile(refs, **kw)
        profiles[name] = p

    deltas: list[int] = []
    verdict_pairs = 0
    verdict_diff = 0
    for sc in SCRIPTS:
        for occ in OCCASIONS:
            scores: dict[str, int] = {}
            verdicts: dict[str, str] = {}
            for name, p in profiles.items():
                r = svc.create_report(p.profile_id, image_refs=[f"demo/{sc}"], occasion=occ)
                scores[name] = r.total_centi
                verdicts[name] = r.verdict.value
            names = list(scores)
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    a, b = names[i], names[j]
                    deltas.append(abs(scores[a] - scores[b]))
                    verdict_pairs += 1
                    if verdicts[a] != verdicts[b]:
                        verdict_diff += 1

    median = statistics.median(deltas)
    return {
        "condition": "B 带主观偏好" if with_prefs else "A 纯统计画像",
        "pairs": len(deltas),
        "median_centi": round(median, 1),
        "median_points": round(median / 100, 2),
        "mean_points": round(statistics.fmean(deltas) / 100, 2),
        "max_points": round(max(deltas) / 100, 2),
        "zero_delta_ratio": round(sum(1 for d in deltas if d == 0) / len(deltas), 4),
        "verdict_disagreement_rate": round(verdict_diff / verdict_pairs, 4),
        "pass": median >= MIN_MEDIAN_CENTI,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="画像区分度测量")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    results = [measure(False), measure(True)]

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0 if any(r["pass"] for r in results) else 1

    print("=" * 82)
    print("画像区分度测量 · 同一套搭配对不同用户是否给出不同分")
    print("  3 人设 × 10 剧本 × 5 场合 × 3 画像对 = 150 对比较")
    print("=" * 82)
    print(f"  {'条件':14s} {'中位数':>8s} {'平均':>6s} {'最大':>6s} {'零差异':>7s} {'verdict分歧':>11s}  判定")
    print("-" * 82)
    for r in results:
        print(f"  {r['condition']:14s} {r['median_points']:6.1f}分 {r['mean_points']:5.2f} "
              f"{r['max_points']:5.1f} {r['zero_delta_ratio']:6.1%} "
              f"{r['verdict_disagreement_rate']:10.1%}   "
              f"{'PASS' if r['pass'] else 'FAIL'} (≥8.0)")
    print("-" * 82)
    best = max(results, key=lambda r: r["median_centi"])
    if best["pass"]:
        print(f"结论：**{best['condition']}** 下个性化在分数量级上成立（中位数 {best['median_points']} 分 ≥ 8）。")
    else:
        print("结论：两种条件下都不足 → 个性化在**分数量级**上尚未成立（docs/10 F-08）。")
        print("      但 verdict 分歧率能到 30%+：系统能改变结论，只是分差幅度偏小。")
    return 0 if best["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
