"""F-09/F-03 规则标定后，同步更新夹具与版本断言。

原则：**不放宽断言，只更新输入**。每条夹具改的都是"为了命中新阈值所需的输入"，
期望值（hit/miss）一个都没动。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"
TESTS = ROOT / "tests"


def block_bounds(lines: list[str], case_id: str) -> tuple[int, int]:
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith(f"- case_id: {case_id}"))
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip().startswith("- case_id:"):
            end = j
            break
    return start, end


def replace_in_block(path: Path, case_id: str, old: str, new: str, count: int = 1) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    a, b = block_bounds(lines, case_id)
    hits = 0
    for i in range(a, b):
        if hits >= count:
            break
        if old in lines[i]:
            lines[i] = lines[i].replace(old, new, 1)
            hits += 1
    if hits != count:
        raise SystemExit(f"{path.name}/{case_id}: 期望替换 {count} 处，实际 {hits} 处（原文：{old!r}）")
    path.write_text("".join(lines), encoding="utf-8")
    print(f"  PATCHED {path.name} [{case_id}] {old.strip()} → {new.strip()}")


def main() -> int:
    # 1) R-COLOR-006 pos-01：原用例是「米白(中性) + 黑(中性)」的高明度对比，
    #    现在被 F-09 的「双中性豁免」正确放过了。换成一彩一深的高对比组合才应命中。
    p = FIX / "R-COLOR-006.yaml"
    replace_in_block(p, "pos-01", "hex: '#F2F2F2'", "hex: '#FFE699'")
    replace_in_block(p, "pos-01", "name: 米白", "name: 浅黄油")
    replace_in_block(p, "pos-01", "hex: '#111111'", "hex: '#1A1A1A'")

    # 2) R-STYLE-005 pos-01：阈值 0.55 → 0.35，原 0.4 不再触发；降到 0.30 仍应命中
    replace_in_block(FIX / "R-STYLE-005.yaml", "pos-01", "style_cosine: 0.4", "style_cosine: 0.3")

    # 3) R-STYLE-009 pos-01：窗口 0.60–0.75 → 0.45–0.58，原 0.68 落到窗外；改 0.50
    replace_in_block(FIX / "R-STYLE-009.yaml", "pos-01", "style_cosine: 0.68", "style_cosine: 0.5")

    # 4) 版本断言
    tp = TESTS / "test_rules.py"
    t = tp.read_text(encoding="utf-8")
    if 'assert meta["version"] == "rs-2026.10.1"' in t:
        t = t.replace('assert meta["version"] == "rs-2026.10.1"',
                      'assert meta["version"] == "rs-2026.10.2"')
        tp.write_text(t, encoding="utf-8")
        print("  PATCHED test_rules.py: 版本断言 → rs-2026.10.2")
    else:
        print("  SKIP test_rules.py: 版本断言已是新值")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
