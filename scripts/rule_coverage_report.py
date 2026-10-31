"""规则覆盖率体检：找出**永远不可能命中**的休眠规则。

动机（rules-author 集成反馈）：
    `context.build_context` 依赖若干「视觉几何/结构量」字段（视觉腰线、面积占比、裤脚堆积…），
    这些量当前 parser（mock 与 VLM 都）不产出 → 依赖它们的规则**不会报错，只是永不命中**。
    这类"静默失效"最危险：文档说 50 条规则，实际可能只有一部分在工作。

用法：python scripts/rule_coverage_report.py [--json]
退出码：0 正常；1 若休眠规则占比 > 30%（CI 可据此告警）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.contracts import Condition
from src.parsing.mock import SCRIPTS
from src.pipeline import DemoService
from src.rules.context import build_context
from src.rules.loader import load_rules


def leaf_fields(cond: Condition, out: set[str]) -> None:
    if cond.kind == "leaf":
        if cond.field:
            out.add(cond.field)
        if cond.other_field:
            out.add(cond.other_field)
        return
    for child in (cond.all or []) + (cond.any or []):
        leaf_fields(child, out)
    if cond.not_ is not None:
        leaf_fields(cond.not_, out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="休眠规则体检")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    rules = load_rules()
    svc = DemoService(persist=False)
    profile, _ = svc.create_profile([f"demo/{s}" for s in SCRIPTS[:5]])

    # 收集所有剧本 × 所有场合的 context，统计每个字段的取值多样性
    # （必须遍历场合：否则 R-OCC-* 会因 occasion 恒定而被误判为"无区分度"）
    from src.pipeline import compute_features
    OCCASIONS = ("commute", "date", "sport", "formal", "casual")
    field_values: dict[str, set[str]] = {}
    for s in SCRIPTS:
        for occ in OCCASIONS:
            outfit, _ = svc.parse_outfit([f"demo/{s}"], occ, "autumn")
            f = compute_features(outfit, profile, svc.encoder)
            ctx = build_context(outfit, profile, f)
            for k, v in ctx.items():
                field_values.setdefault(k, set()).add(repr(v))

    total = len(rules)
    missing_rules: list[dict] = []
    degenerate_rules: list[dict] = []

    for rule in rules:
        need: set[str] = set()
        leaf_fields(rule.condition, need)
        absent = sorted(f for f in need if f not in field_values)
        if absent:
            missing_rules.append({"rule_id": rule.rule_id, "title": rule.title,
                                  "severity": rule.severity.value, "fields": absent})
            continue
        # 该规则用到的字段若在 10 个剧本里取值完全一致 → 无区分度
        flat = sorted(f for f in need if len(field_values[f]) == 1)
        if flat:
            degenerate_rules.append({
                "rule_id": rule.rule_id, "title": rule.title,
                "severity": rule.severity.value,
                "constant_fields": {f: next(iter(field_values[f])) for f in flat},
            })

    n_missing = len(missing_rules)
    n_degen = len(degenerate_rules)
    dormant_boost = [r for r in degenerate_rules + missing_rules if r["severity"] == "boost"]
    ratio = (n_missing + n_degen) / total if total else 0.0

    if args.json:
        print(json.dumps({"total": total, "absent_fields": n_missing,
                          "no_discrimination": n_degen,
                          "ratio": round(ratio, 4),
                          "affected_boost": len(dormant_boost),
                          "missing": missing_rules,
                          "degenerate": degenerate_rules},
                         ensure_ascii=False, indent=2))
    else:
        print("=" * 78)
        print("规则判别力体检 · ruleset rs-2026.10.1")
        print(f"  规则总数                  {total}")
        print(f"  依赖字段缺失（真休眠）    {n_missing}")
        print(f"  依赖字段恒定（无区分度）  {n_degen}   ← 在 10 个剧本上取值完全相同")
        print(f"  合计受影响                {n_missing + n_degen}  ({ratio:.0%})")
        print(f"  其中 boost 规则           {len(dormant_boost)}  ← 直接压低分数上限")
        print("=" * 78)
        for r in missing_rules:
            print(f"  [缺失] {r['rule_id']:14s} [{r['severity']:5s}] {r['title']}")
            for f in r["fields"]:
                print(f"           {f}")
        for r in degenerate_rules:
            print(f"  [恒定] {r['rule_id']:14s} [{r['severity']:5s}] {r['title']}")
            for f, v in r["constant_fields"].items():
                print(f"           {f} == {v}")
        print("-" * 78)
        print("说明：字段恒定 ≠ 规则是错的。它说明**当前解析层喂不出区分该规则所需的信息**，")
        print("      因此这些规则在所有演示样本上表现一致（要么恒命中、要么恒不命中）。")
        print("      修法见 docs/10-implementation-findings.md F-02。")
    return 1 if ratio > 0.30 else 0


if __name__ == "__main__":
    raise SystemExit(main())
