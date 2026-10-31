"""画像区分度测量 —— 证明「同一套衣服，对不同用户给不同分」。

为什么必须单独测（docs/08 §3 的第二个关键指标）：
    Spearman 只证明"排序大致对"；它无法证明"个性化真的生效"。
    如果同一套搭配对任何画像都给同一个分，系统就退化成一个通用审美打分器 ——
    这正是 docs/06 调研里 40 个同类 App 失败的原因，也是我们 P3 原则的生死线。

指标定义：
    Δ(i,j) = |score(outfit | profile_i) − score(outfit | profile_j)|   （厘点，1 分 = 100）
    画像区分度 = 所有 (outfit, profile 对) 上 Δ 的**中位数**
门槛：中位数 ≥ 8 分（800 厘点），见 docs/08 §3。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.contracts import Slot
from src.parsing.mock import SCRIPTS
from src.pipeline import DemoService

# 三个「人设」用不同的参考穿搭集合，模拟不同风格的真人用户
PERSONAS = {
    "通勤知性": ["demo/commute_polished", "demo/formal_suit", "demo/mono_gradient"],
    "法式约会": ["demo/date_french", "demo/casual_street", "demo/mono_gradient"],
    "运动休闲": ["demo/sport_active", "demo/casual_street", "demo/accessory_overload"],
}
OCCASIONS = ("commute", "date", "sport", "formal", "casual")

# 门槛（厘点）
MIN_MEDIAN_CENTI = 800


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="画像区分度测量")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="打印每个组合的明细")
    args = ap.parse_args(argv)

    svc = DemoService(persist=False)
    profiles = {}
    for name, refs in PERSONAS.items():
        p, _ = svc.create_profile(refs)
        profiles[name] = p

    if not args.json:
        print("=" * 78)
        print("画像区分度测量 · 同一套搭配对不同用户是否给出不同分")
        print("=" * 78)
        for name, p in profiles.items():
            tags = "、".join(f"{t.tag}({t.weight:.2f})" for t in p.tags[:3])
            print(f"  人设 {name:6s}  n_ref={p.n_ref_outfits}  top3: {tags}")
        print("-" * 78)

    rows = []
    verdict_pairs = 0
    verdict_diff = 0
    for sc in SCRIPTS:
        for occ in OCCASIONS:
            scores = {}
            verdicts = {}
            for name, p in profiles.items():
                r = svc.create_report(p.profile_id, image_refs=[f"demo/{sc}"], occasion=occ)
                scores[name] = r.total_centi
                verdicts[name] = r.verdict.value
            names = list(scores)
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    a, b = names[i], names[j]
                    delta = abs(scores[a] - scores[b])
                    rows.append({"script": sc, "occasion": occ, "pair": f"{a} vs {b}",
                                 "a": scores[a], "b": scores[b], "delta_centi": delta,
                                 "verdict_same": verdicts[a] == verdicts[b]})
                    verdict_pairs += 1
                    if verdicts[a] != verdicts[b]:
                        verdict_diff += 1

    deltas = [r["delta_centi"] for r in rows]
    median = statistics.median(deltas)
    mean = statistics.fmean(deltas)
    p90 = sorted(deltas)[int(len(deltas) * 0.9) - 1]
    zero = sum(1 for d in deltas if d == 0)

    result = {
        "pairs": len(rows),
        "median_centi": round(median, 1),
        "median_points": round(median / 100, 2),
        "mean_points": round(mean / 100, 2),
        "p90_points": round(p90 / 100, 2),
        "max_points": round(max(deltas) / 100, 2),
        "zero_delta_pairs": zero,
        "zero_delta_ratio": round(zero / len(deltas), 4),
        "verdict_disagreement_rate": round(verdict_diff / verdict_pairs, 4),
        "threshold_points": MIN_MEDIAN_CENTI / 100,
        "pass": median >= MIN_MEDIAN_CENTI,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["pass"] else 1

    print(f"  比较对数                      {result['pairs']}  (10 剧本 × 5 场合 × 3 画像对)")
    print(f"  **画像区分度（中位数）**      {result['median_points']} 分   "
          f"[门槛 ≥ {result['threshold_points']} 分]  "
          f"{'✅ PASS' if result['pass'] else '❌ FAIL'}")
    print(f"  平均差异                      {result['mean_points']} 分")
    print(f"  P90 差异                      {result['p90_points']} 分")
    print(f"  最大差异                      {result['max_points']} 分")
    print(f"  零差异占比                    {result['zero_delta_ratio']:.1%}  "
          f"（{zero} 对完全同分 → 这些搭配对谁都一样，个性化无效）")
    print(f"  Verdict 分歧率                {result['verdict_disagreement_rate']:.1%}  "
          f"（同一套衣服对 A 说适合、对 B 说不适合的比例）")
    print("-" * 78)
    if result["pass"]:
        print("结论：个性化**真实生效** —— 同一套搭配对不同用户给出显著不同的分。")
    else:
        print("结论：区分度不足，系统接近通用打分器，P3 原则未落地。")

    if args.verbose:
        worst = sorted(rows, key=lambda r: -r["delta_centi"])[:8]
        print("\n差异最大的组合：")
        for r in worst:
            print(f"  {r['script']:20s} {r['occasion']:8s} {r['pair']:22s} "
                  f"{r['a']/100:5.1f} vs {r['b']/100:5.1f}  Δ={r['delta_centi']/100:5.2f}")
        same = [r for r in rows if r["delta_centi"] == 0][:8]
        if same:
            print("\n零差异的组合（个性化失效点）：")
            for r in same:
                print(f"  {r['script']:20s} {r['occasion']:8s} {r['pair']:22s} 均为 {r['a']/100:.1f}")

    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
