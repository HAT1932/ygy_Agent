#!/usr/bin/env python
"""20 套标注评测集跑分脚本 —— 证明「准不准 / 可不可追溯 / 稳不稳定」。

指标与门槛（docs/08-evaluation-and-implementation.md §3，docs/00-baseline.md §10）：

| 指标                | 定义                                   | 门槛  |
|---------------------|----------------------------------------|-------|
| 总分 Spearman ρ     | 系统总分 vs 3 名人工标注者均值         | ≥ 0.60 |
| Verdict 准确率      | 4 分类（strongly/suitable/neutral/unsuitable） | ≥ 65% |
| 「不适合→强烈推荐」 | 混淆矩阵该格占比                       | ≤ 5%  |
| 扣分可追溯率        | 扣分命中项带 rule_id + evidence 的比例 | 100%  |
| 确定性              | 同输入两次运行 total_centi 完全一致    | 100%  |

用法：
    python eval/run_eval.py                 # 默认读 eval/cases.yaml，写 eval/report.json
    python eval/run_eval.py --verbose       # 打印逐用例明细
    python eval/run_eval.py --allow-fail    # 未达标也返回 0（仅演示用）

**本脚本不修正/不美化任何数字**：指标不达标就原样输出，并列偏差最大的用例。
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_CASES = ROOT / "eval" / "cases.yaml"
DEFAULT_OUT = ROOT / "eval" / "report.json"

VERDICTS = ["strongly_recommended", "suitable", "neutral", "unsuitable"]
VERDICT_CN = {
    "strongly_recommended": "强烈推荐",
    "suitable": "适合",
    "neutral": "一般",
    "unsuitable": "不适合",
}

# 门槛：docs/08 §3 + docs/00-baseline.md §10
THRESHOLDS = {
    "spearman_total": {"op": ">=", "value": 0.60, "label": "总分 Spearman ρ ≥ 0.60"},
    "verdict_accuracy": {"op": ">=", "value": 0.65, "label": "Verdict 4 分类准确率 ≥ 65%"},
    "severe_confusion_rate": {"op": "<=", "value": 0.05,
                              "label": "「不适合 → 强烈推荐」占比 ≤ 5%"},
    "traceability": {"op": ">=", "value": 1.0, "label": "扣分可追溯率 = 100%"},
    "determinism": {"op": ">=", "value": 1.0, "label": "确定性 = 100%"},
}

# 采样覆盖要求：docs/08 §2.1
SCENARIOS = ["commute", "date", "sport", "formal", "home"]
STYLES = ["minimal", "french", "american_vintage", "japanese", "sweet_cool"]
MIN_PER_SCENARIO = 3
MIN_PER_STYLE = 3
MIN_PER_DIFFICULTY = 5      # 20 套的 1/3 ≈ 6.67，取 ≥5 允许 ±1 浮动
MIN_USERS = 4
MIN_CASES_PER_USER = 4
MIN_PAIRS = 5


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------


def check(value: float | None, spec: dict[str, Any]) -> bool:
    if value is None:
        return False
    op = spec["op"]
    if op == ">=":
        return value >= spec["value"]
    if op == "<=":
        return value <= spec["value"]
    raise ValueError(f"未知阈值运算符: {op}")


def human_verdict_of(total: float) -> str:
    """人工 verdict 的判定口径（用于校验 cases.yaml 标注自洽，不用于打分）。"""
    if total >= 80:
        return "strongly_recommended"
    if total >= 65:
        return "suitable"
    if total >= 50:
        return "neutral"
    return "unsuitable"


def load_cases(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "cases" not in raw:
        raise SystemExit(f"用例文件格式错误：{path}（需要顶层 cases: 列表）")
    cases = raw["cases"]
    if not isinstance(cases, list) or not cases:
        raise SystemExit(f"用例文件没有用例：{path}")
    return raw.get("meta") or {}, cases


# --------------------------------------------------------------------------
# 覆盖度自检（不达标时如实报告，不阻塞跑分）
# --------------------------------------------------------------------------


def check_coverage(cases: list[dict[str, Any]]) -> dict[str, Any]:
    problems: list[str] = []

    if len(cases) != 20:
        problems.append(f"用例数应为 20，实际 {len(cases)}")

    scen: dict[str, int] = {}
    styles: dict[str, int] = {}
    diffs: dict[str, int] = {}
    users: dict[str, int] = {}
    pairs: set[str] = set()

    for c in cases:
        scen[c["scenario"]] = scen.get(c["scenario"], 0) + 1
        styles[c["style_ref"]] = styles.get(c["style_ref"], 0) + 1
        diffs[c["difficulty"]] = diffs.get(c["difficulty"], 0) + 1
        users[c["user_id"]] = users.get(c["user_id"], 0) + 1
        for tag in c.get("tags") or []:
            if str(tag).startswith("pair_check:"):
                pairs.add(str(tag))

    for s in SCENARIOS:
        if scen.get(s, 0) < MIN_PER_SCENARIO:
            problems.append(f"场景 {s} 仅 {scen.get(s, 0)} 套（要求 ≥{MIN_PER_SCENARIO}）")
    for s in STYLES:
        if styles.get(s, 0) < MIN_PER_STYLE:
            problems.append(f"风格 {s} 仅 {styles.get(s, 0)} 套（要求 ≥{MIN_PER_STYLE}）")
    extra_scen = sorted(set(scen) - set(SCENARIOS))
    extra_styles = sorted(set(styles) - set(STYLES))
    if extra_scen:
        problems.append(f"出现未登记的 scenario：{extra_scen}")
    if extra_styles:
        problems.append(f"出现未登记的 style_ref：{extra_styles}")
    for d in ("good", "bad", "boundary"):
        if diffs.get(d, 0) < MIN_PER_DIFFICULTY:
            problems.append(f"难度 {d} 仅 {diffs.get(d, 0)} 套（要求 ≥{MIN_PER_DIFFICULTY}）")
    if len(users) < MIN_USERS:
        problems.append(f"虚拟用户仅 {len(users)} 个（要求 ≥{MIN_USERS}）")
    for u, n in sorted(users.items()):
        if n < MIN_CASES_PER_USER:
            problems.append(f"用户 {u} 仅 {n} 套（要求 ≥{MIN_CASES_PER_USER}）")
    if len(pairs) < MIN_PAIRS:
        problems.append(f"配对样本仅 {len(pairs)} 对（要求 ≥{MIN_PAIRS}：同一套衣服 × 两个画像）")

    return {
        "ok": not problems,
        "problems": problems,
        "scenarios": scen,
        "styles": styles,
        "difficulties": diffs,
        "users": users,
        "pair_count": len(pairs),
    }


# --------------------------------------------------------------------------
# 单用例执行
# --------------------------------------------------------------------------


def run_case(svc: Any, case: dict[str, Any], profile_cache: dict[tuple, Any],
             second_run: bool) -> dict[str, Any]:
    profile_refs = tuple(case["profile_refs"])
    if profile_refs not in profile_cache:
        profile, _outfits = svc.create_profile(list(profile_refs),
                                               case.get("profile_note"))
        profile_cache[profile_refs] = profile
    profile = profile_cache[profile_refs]

    kwargs = dict(
        profile_id=profile.profile_id,
        image_refs=list(case["outfit_refs"]),
        occasion=case.get("occasion"),
        season=case.get("season"),
    )
    report = svc.create_report(**kwargs)

    annotations = [float(x) for x in case["annotators"]]
    human_mean = round(statistics.mean(annotations), 2)
    system_total = float(report.total_score)

    # ---- 可追溯性 / 契约自检 ----
    penalty_hits = [h for h in report.rule_hits if h.delta_applied < 0]
    untraceable: list[dict[str, Any]] = []
    for h in penalty_hits:
        if not str(h.rule_id or "").strip() or not str(h.evidence or "").strip():
            untraceable.append({"rule_id": h.rule_id, "reason": "缺 rule_id 或 evidence"})
        elif not str(h.suggestion or "").strip():
            # INV-3：非 boost 规则必须给建议（docs/04 §5.8.1）
            untraceable.append({"rule_id": h.rule_id, "reason": "扣分规则缺 suggestion"})

    # INV-2 加法性 + 维度分之和（契约定义的审计真值）
    applied_centi = sum(int(round(h.delta_applied * 100)) for h in report.rule_hits)
    inv2_ok = report.total_centi == report.base_centi + applied_centi
    dim_sum_ok = sum(d.score_centi for d in report.dimension_scores) == report.total_centi
    veto_ok = (not report.vetoed) or report.verdict.value == "unsuitable"

    # expected_gain 与 reason_rule_ids 自洽（docs/04 §5.8.2）
    hit_by_id = {h.rule_id: h for h in report.rule_hits}
    gain_ok = True
    gain_detail: list[dict[str, Any]] = []
    for s in report.suggestions:
        expected = sum(abs(hit_by_id[r].delta_applied)
                       for r in s.reason_rule_ids if r in hit_by_id)
        if abs(float(s.expected_gain) - expected) > 0.05:
            gain_ok = False
            gain_detail.append({
                "suggestion": s.action,
                "expected_gain": s.expected_gain,
                "sum_reason_delta": round(expected, 2),
            })

    # ---- 确定性：同输入再跑一次 ----
    deterministic: bool | None = None
    second_centi: int | None = None
    if second_run:
        report2 = svc.create_report(**kwargs)
        second_centi = report2.total_centi
        sig1 = [(h.rule_id, h.delta_applied, h.clipped) for h in report.rule_hits]
        sig2 = [(h.rule_id, h.delta_applied, h.clipped) for h in report2.rule_hits]
        deterministic = (report2.total_centi == report.total_centi) and (sig1 == sig2)

    top_suggestion = report.suggestions[0].model_dump() if report.suggestions else None

    return {
        "case_id": case["case_id"],
        "user_id": case["user_id"],
        "scenario": case["scenario"],
        "style_ref": case["style_ref"],
        "difficulty": case["difficulty"],
        "occasion": case.get("occasion"),
        "season": case.get("season"),
        "profile_refs": list(profile_refs),
        "outfit_refs": list(case["outfit_refs"]),
        "profile_id": profile.profile_id,
        "outfit_id": report.outfit_id,
        "report_id": report.report_id,
        "annotators": annotations,
        "human_mean": human_mean,
        "human_verdict": case["human_verdict"],
        "human_verdict_by_threshold": human_verdict_of(human_mean),
        "system_total": system_total,
        "system_centi": report.total_centi,
        "system_verdict": report.verdict.value,
        "vetoed": report.vetoed,
        "delta": round(system_total - human_mean, 2),
        "dimension_scores": {d.code.value: d.score for d in report.dimension_scores},
        "penalty_hit_count": len(penalty_hits),
        "all_hit_count": len(report.rule_hits),
        "untraceable": untraceable,
        "invariants": {"inv2_additive_ok": inv2_ok, "dim_sum_ok": dim_sum_ok,
                       "veto_verdict_ok": veto_ok},
        "expected_gain_consistent": gain_ok,
        "expected_gain_detail": gain_detail,
        "deterministic": deterministic,
        "second_total_centi": second_centi,
        "top_suggestion": top_suggestion,
        "summary": report.summary,
        "parse_warnings": list(report.meta.parse_warnings),
        "render_mode": report.meta.render_mode.value,
        "meta": {
            "ruleset_version": report.meta.ruleset_version,
            "contract_version": report.meta.contract_version,
            "vlm_model": report.meta.vlm_model,
            "llm_model": report.meta.llm_model,
            "embedding_model": report.meta.embedding_model,
        },
    }


# --------------------------------------------------------------------------
# 指标计算
# --------------------------------------------------------------------------


def compute_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    from scipy.stats import spearmanr

    sys_totals = [r["system_total"] for r in results]
    human_means = [r["human_mean"] for r in results]

    rho: float | None
    p_value: float | None
    if len(set(sys_totals)) < 2 or len(set(human_means)) < 2:
        rho, p_value = None, None
        rho_note = "系统分或人工均值无方差，Spearman 无定义"
    else:
        res = spearmanr(sys_totals, human_means)
        rho = float(res.statistic)
        p_value = float(res.pvalue)
        rho_note = ""

    # verdict 4 分类
    confusion = {h: {s: 0 for s in VERDICTS} for h in VERDICTS}
    correct = 0
    for r in results:
        h = r["human_verdict"]
        s = r["system_verdict"]
        if h not in confusion:
            continue
        if s in confusion[h]:
            confusion[h][s] += 1
        if h == s:
            correct += 1
    accuracy = correct / len(results) if results else None
    severe = confusion["unsuitable"]["strongly_recommended"]
    severe_rate = severe / len(results) if results else None

    # 可追溯性
    penalty_total = sum(r["penalty_hit_count"] for r in results)
    untraceable_total = sum(len(r["untraceable"]) for r in results)
    traceability = (1.0 if penalty_total == 0
                    else (penalty_total - untraceable_total) / penalty_total)

    # 确定性
    det_cases = [r for r in results if r["deterministic"] is not None]
    determinism = (sum(1 for r in det_cases if r["deterministic"]) / len(det_cases)
                   if det_cases else None)

    inv2_violations = [r["case_id"] for r in results
                       if not r["invariants"]["inv2_additive_ok"]
                       or not r["invariants"]["dim_sum_ok"]
                       or not r["invariants"]["veto_verdict_ok"]]
    gain_violations = [r["case_id"] for r in results if not r["expected_gain_consistent"]]

    worst = sorted(results, key=lambda r: abs(r["delta"]), reverse=True)[:5]

    metrics = {
        "spearman_total": None if rho is None else round(rho, 4),
        "spearman_p_value": None if p_value is None else round(p_value, 6),
        "spearman_note": rho_note,
        "verdict_accuracy": None if accuracy is None else round(accuracy, 4),
        "verdict_correct": correct,
        "case_count": len(results),
        "severe_confusion_rate": None if severe_rate is None else round(severe_rate, 4),
        "severe_confusion_count": severe,
        "traceability": round(traceability, 4),
        "penalty_hit_total": penalty_total,
        "untraceable_total": untraceable_total,
        "determinism": None if determinism is None else round(determinism, 4),
        "determinism_case_count": len(det_cases),
        "inv2_violation_cases": inv2_violations,
        "expected_gain_violation_cases": gain_violations,
        "confusion_matrix": confusion,
        "worst_deviations": [
            {
                "case_id": r["case_id"],
                "difficulty": r["difficulty"],
                "scenario": r["scenario"],
                "style_ref": r["style_ref"],
                "system_total": r["system_total"],
                "human_mean": r["human_mean"],
                "delta": r["delta"],
                "system_verdict": r["system_verdict"],
                "human_verdict": r["human_verdict"],
            }
            for r in worst
        ],
        "score_range": {
            "system_min": min(sys_totals) if sys_totals else None,
            "system_max": max(sys_totals) if sys_totals else None,
            "human_min": min(human_means) if human_means else None,
            "human_max": max(human_means) if human_means else None,
        },
    }

    thresholds = {}
    for key, spec in THRESHOLDS.items():
        value = metrics[key]
        thresholds[key] = {
            "label": spec["label"],
            "op": spec["op"],
            "threshold": spec["value"],
            "actual": value,
            "pass": check(value, spec),
        }
    metrics["thresholds"] = thresholds
    metrics["passed"] = all(v["pass"] for v in thresholds.values())
    return metrics


def service_env(svc: Any) -> dict[str, Any]:
    info: dict[str, Any] = {}
    try:
        info = svc.health() or {}
    except Exception:
        pass
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "offline_mode": info.get("offline_mode"),
        "rule_count": info.get("rule_count"),
        "ruleset_version": info.get("ruleset_version"),
        "providers": info.get("providers"),
    }


# --------------------------------------------------------------------------
# 打印
# --------------------------------------------------------------------------


def print_report(metrics: dict[str, Any], coverage: dict[str, Any],
                 results: list[dict[str, Any]], verbose: bool) -> None:
    print("=" * 82)
    print("  AI 穿搭顾问 · 20 套标注评测集跑分")
    print("=" * 82)
    if verbose:
        print(f"{'case':<6}{'场景':<10}{'难度':<10}{'系统':>7}{'人工均值':>9}"
              f"{'偏差':>8}   verdict（系统/人工）")
        for r in results:
            print(f"{r['case_id']:<6}{r['scenario']:<10}{r['difficulty']:<10}"
                  f"{r['system_total']:>7.1f}{r['human_mean']:>9.2f}{r['delta']:>+8.2f}"
                  f"   {r['system_verdict']} / {r['human_verdict']}")
        print("-" * 82)

    sr = metrics["score_range"]
    print(f"用例数            {metrics['case_count']}  "
          f"（系统分 {sr['system_min']}–{sr['system_max']}，"
          f"人工均值 {sr['human_min']}–{sr['human_max']}）")
    print(f"总分 Spearman ρ   {fmt(metrics['spearman_total'])}"
          f"（p={fmt(metrics['spearman_p_value'])}）"
          + (f"  [{metrics['spearman_note']}]" if metrics["spearman_note"] else ""))

    print(f"Verdict 准确率    {pct(metrics['verdict_accuracy'])} "
          f"({metrics['verdict_correct']}/{metrics['case_count']})")
    print("混淆矩阵（行=人工，列=系统）")
    header = " " * 16 + "".join(f"{cn(s):>10}" for s in VERDICTS)
    print(header)
    for h in VERDICTS:
        row = metrics["confusion_matrix"][h]
        print(f"{cn(h):>14}  " + "".join(f"{row[s]:>10}" for s in VERDICTS))

    print(f"「不适合→强烈推荐」 {metrics['severe_confusion_count']} 例"
          f"（{pct(metrics['severe_confusion_rate'])}）")
    print(f"扣分可追溯率      {pct(metrics['traceability'])}"
          f"（{metrics['penalty_hit_total']} 个扣分项，"
          f"{metrics['untraceable_total']} 个不可追溯）")
    print(f"确定性            {pct(metrics['determinism'])}"
          f"（{metrics['determinism_case_count']} 套各跑两次）")
    print(f"INV-2 违反用例    {metrics['inv2_violation_cases'] or '无'}")
    print(f"expected_gain 不自洽用例  {metrics['expected_gain_violation_cases'] or '无'}")

    if metrics["worst_deviations"]:
        print("偏差最大的用例（|系统 − 人工均值| 降序）")
        for w in metrics["worst_deviations"]:
            print(f"  {w['case_id']}  系统 {w['system_total']:.1f} / 人工 {w['human_mean']:.2f}"
                  f" → {w['delta']:+.2f}  [{w['scenario']}/{w['difficulty']}]")

    print("-" * 82)
    print("覆盖度自检")
    print(f"  场景 {coverage['scenarios']}")
    print(f"  风格 {coverage['styles']}")
    print(f"  难度 {coverage['difficulties']}   用户 {coverage['users']}   "
          f"配对 {coverage['pair_count']} 对")
    for p in coverage["problems"]:
        print(f"  ✗ {p}")
    if coverage["ok"]:
        print("  ✓ 覆盖要求全部满足（docs/08 §2.1）")

    print("-" * 82)
    print("门槛判定（docs/08 §3）")
    for key, th in metrics["thresholds"].items():
        mark = "PASS" if th["pass"] else "FAIL"
        actual = th["actual"]
        actual_str = "N/A" if actual is None else (
            pct(actual) if key in ("verdict_accuracy", "traceability", "determinism")
            else (f"{actual * 100:.1f}%" if key == "severe_confusion_rate" else f"{actual:.4f}"))
        print(f"  [{mark}] {th['label']:<34} 实际 {actual_str:>9}")
    overall = metrics["passed"] and coverage["ok"]
    print("=" * 82)
    print(f"总体结论：{'达标 ✓' if overall else '未达标 ✗'}"
          + ("" if metrics["passed"] else "（指标未达门槛）")
          + ("" if coverage["ok"] else "（采样覆盖不足）"))
    print("=" * 82)


def fmt(v: float | None, nd: int = 4) -> str:
    return "N/A" if v is None else f"{v:.{nd}f}"


def pct(v: float | None) -> str:
    return "N/A" if v is None else f"{v * 100:.1f}%"


def cn(v: str) -> str:
    return VERDICT_CN.get(v, v)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="20 套标注评测集跑分")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES,
                        help="用例 YAML 路径（默认 eval/cases.yaml）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="JSON 报告输出路径（默认 eval/report.json）")
    parser.add_argument("--verbose", action="store_true", help="打印逐用例明细")
    parser.add_argument("--allow-fail", action="store_true",
                        help="未达标也以 0 退出（默认未达标返回 1）")
    parser.add_argument("--no-second-run", action="store_true",
                        help="跳过确定性复跑（不推荐；会使确定性指标为 N/A）")
    args = parser.parse_args(argv)

    if not args.cases.exists():
        print(f"找不到用例文件：{args.cases}", file=sys.stderr)
        return 2

    meta, cases = load_cases(args.cases)
    coverage = check_coverage(cases)

    try:
        from src.pipeline import get_service
    except Exception as exc:  # 依赖层尚未就绪时给出清晰提示，而不是裸堆栈
        print(f"无法导入 src.pipeline.get_service：{exc}", file=sys.stderr)
        print("请确认 rules/ 与 src/{parsing,profiling,towers,synthesis,rules} 已就绪。",
              file=sys.stderr)
        return 2

    svc = get_service()
    print(f"服务就绪：规则 {svc.health().get('rule_count')} 条，"
          f"离线模式 {svc.health().get('offline_mode')}，"
          f"用例 {len(cases)} 套\n")

    results: list[dict[str, Any]] = []
    profile_cache: dict[tuple, Any] = {}
    failures: list[dict[str, str]] = []
    for case in cases:
        try:
            results.append(run_case(svc, case, profile_cache,
                                    second_run=not args.no_second_run))
        except Exception as exc:  # 单用例失败不中断全量跑分，但要如实记录
            failures.append({
                "case_id": str(case.get("case_id", "?")),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=3),
            })
            print(f"  ! 用例 {case.get('case_id')} 执行失败：{exc}", file=sys.stderr)

    if not results:
        print("所有用例均执行失败，无法计算指标。", file=sys.stderr)
        return 2

    metrics = compute_metrics(results)
    metrics["failed_cases"] = failures
    print_report(metrics, coverage, results, args.verbose)

    payload = {
        "schema": "eval-report/1.0",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "cases_file": str(args.cases),
        "cases_meta": meta,
        "coverage": coverage,
        "environment": service_env(svc),
        "metrics": metrics,
        "cases": results,
        "honesty_note": (
            "本报告为脚本实测输出，未对数字做任何事后修正。人工标注为演示用模拟标注"
            "（3 名虚拟标注者，口径见 eval/README.md），不等同于真实形象顾问标定。"
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON 报告已写入：{args.out}")

    overall = metrics["passed"] and coverage["ok"]
    if overall or args.allow_fail:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
