"""修正装误伤的规则补丁（F-09）：给三条规则加「场合/层次/正式度」豁免条件。

修的是误报（把正确穿搭判为缺陷），不是调参凑分：
  R-COLOR-006 明度断层：黑白高对比是正装与中性色的**经典手法**，不该判"生硬切断"
  R-DET-003  季节材质：**有层次的穿搭**里冬+夏混搭是正常的（衬衫+羊毛西装），
              冲突只发生在无层次的平铺混搭
  R-STYLE-003 单品不同源：**正装是制服式组合**（西装+西裤+衬衫），风格同源由品类保证，
              用 style_tags 的 jaccard 衡量会系统性误判
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "rules"


def patch(path: Path, old: str, new: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        print(f"  SKIP {path.name}: 未匹配到原文（可能已修补）")
        return False
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"  PATCHED {path.name}")
    return True


def main() -> int:
    ok = True

    # ---------------- R-COLOR-006 ----------------
    old = """condition:
  field: garment.top.color.lightness
  op: abs_diff_gt
  other_field: garment.bottom.color.lightness
  value: 0.65
"""
    new = """condition:
  all:
  - {field: garment.top.color.lightness, op: abs_diff_gt, other_field: garment.bottom.color.lightness, value: 0.65}
  # F-09 豁免：上半身与下半身同为中性色（黑白灰）时，高明度对比是经典剪裁手法，不是缺陷
  - not:
      any:
      - all:
        - {field: garment.top.color.is_neutral, op: is_true}
        - {field: garment.bottom.color.is_neutral, op: is_true}
      # F-09 豁免：正式场合的黑白高对比是正装标准配置
      - {field: outfit.occasion, op: in, value: [formal]}
"""
    ok &= patch(R / "color" / "R-COLOR-006.yaml", old, new)

    # ---------------- R-DET-003 ----------------
    old = """  - field: outfit.materials.has_winter_item
    op: is_true
"""
    new = """  - field: outfit.materials.has_winter_item
    op: is_true
  # F-09 豁免：有层次的穿搭（外套叠穿）里冬夏材质混搭是正常的，冲突只见于无层次平铺
  - {field: outfit.structure.layer_depth, op: lte, value: 1}
"""
    ok &= patch(R / "detail" / "R-DET-003.yaml", old, new)

    # ---------------- R-STYLE-003 ----------------
    old = """  - field: outfit.style_tag_jaccard_min
    op: lt
    value: 0.2
"""
    new = """  - field: outfit.style_tag_jaccard_min
    op: lt
    value: 0.2
  # F-09 豁免：高正式度（正装/礼服）是制服式组合，同源由品类保证，jaccard 会系统性误判
  - {field: outfit.formality, op: lt, value: 0.85}
"""
    ok &= patch(R / "style" / "R-STYLE-003.yaml", old, new)

    # ---------------- 规则集版本 ----------------
    print("\n下一步：bump RULESET_VERSION → rs-2026.10.2（改规则即改版本，可审计）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
