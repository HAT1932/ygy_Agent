"""F-09 补丁第二步：把新增条件引用到的字段声明进 evidence_fields（lint 要求可追溯）。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "rules"

ADDITIONS = {
    "color/R-COLOR-006.yaml": [
        "garment.top.color.is_neutral",
        "garment.bottom.color.is_neutral",
        "outfit.occasion",
    ],
    "detail/R-DET-003.yaml": [
        "outfit.structure.layer_depth",
    ],
    "style/R-STYLE-003.yaml": [
        "outfit.formality",
    ],
}


def main() -> int:
    for rel, fields in ADDITIONS.items():
        path = R / rel
        text = path.read_text(encoding="utf-8")
        missing = [f for f in fields if f"- {f}\n" not in text]
        if not missing:
            print(f"  SKIP {path.name}: 已声明")
            continue
        # 在 evidence_fields 段末尾追加（该段以 "evidence_fields:" 开始，到下一个顶层键结束）
        lines = text.splitlines(keepends=True)
        start = next(i for i, ln in enumerate(lines) if ln.startswith("evidence_fields:"))
        end = start + 1
        while end < len(lines) and (lines[end].startswith("- ") or lines[end].startswith("  - ")
                                    or lines[end].startswith("  #") or not lines[end].strip()):
            end += 1
        insert = [f"- {f}\n" for f in missing]
        lines[end:end] = insert
        path.write_text("".join(lines), encoding="utf-8")
        print(f"  PATCHED {path.name}: +{len(missing)} 个 evidence 字段")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
