# 评测（eval）—— 20 套标注集跑分

> 事实源：`docs/08-evaluation-and-implementation.md` §2/§3、`docs/00-baseline.md` §10。
> 本目录只做一件事：**用可复现的脚本，诚实测量「准不准 / 可不可追溯 / 稳不稳定」**。

---

## 1. 文件

| 文件 | 作用 |
|---|---|
| `cases.yaml` | 20 套标注用例（5 场景 / 5 风格 / 5 画像 / 6 good + 6 bad + 8 boundary / 5 对配对样本） |
| `run_eval.py` | 跑分脚本：Spearman、verdict 准确率 + 混淆矩阵、可追溯率、确定性、覆盖度自检，输出 `report.json` |
| `report.json` | 最近一次跑分的**实测**结果（脚本生成，禁止手改） |

## 2. 怎么跑

```bash
# 在仓库根目录
python eval/run_eval.py                 # 默认强制离线（mock 解析 + 模板文案），可复现
python eval/run_eval.py --verbose       # 打印逐用例明细
python eval/run_eval.py --online        # 用 .env 里的真实 VLM/LLM 跑（不可复现，慎用）
python eval/run_eval.py --out /tmp/x.json --allow-fail
```

- 依赖：`scipy`（Spearman）、`PyYAML`；业务链路走 `src.pipeline.get_service()`。
- **评测默认钉死离线**（`OFFLINE_MODE=true`）：mock 解析 + 模板文案 + 本地 hash embedding。
  理由：用真实 VLM/LLM 跑评测时分数会受模型版本、网络、非确定性影响且不可复现；评测要测的是
  规则引擎与链路，不是模型今天的状态。即使 `.env` 里配了真实 key，默认也不会调用。
  JSON 里 `environment.offline_pinned_by_eval=true` 会记录这一点；`--online` 时才为 `false`。
- 退出码：全部门槛达标且覆盖度满足 → `0`；否则 `1`（`--allow-fail` 强制 `0`，仅供演示）。
- 用例里 `demo/...` 只是确定性标识，不做任何文件/网络 I/O。

## 3. 用例与 mock 剧本

mock parser（`src/parsing/mock.py`）按 image_ref 里的关键词选剧本（最长别名优先），
因此用例可读且稳定，**不依赖哈希**：

| ref 关键词 | 剧本 | 关键特征 |
|---|---|---|
| `commute` / `business` / `通勤` | commute_polished | 中性色通勤四件套 |
| `date` / `约会` / `法式` | date_french | 真丝衬衫 + 半裙 + 金属耳饰 |
| `sport` / `运动` / `gym` | sport_active | 卫衣 + 运动裤 + 球鞋（正式场合触发 veto 分支） |
| `formal` / `正式` / `suit` | formal_suit | 西装三件套 + 牛津鞋 |
| `casual` / `休闲` / `street` | casual_street | 印花T + 宽松牛仔 + 厚底鞋 |
| `clash` / `撞色` / `高饱和` | color_clash | 玫红 + 亮绿，hue_span≈146°（撞色 hard 分支） |
| `mono` / `同色` / `渐变` | mono_gradient | 驼/棕同族明度递进 |
| `season` / `季节` / `冲突` | season_mismatch | 亚麻 + 羊毛（季节材质冲突） |
| `accessory` / `配饰` / `overload` | accessory_overload | 5 件配饰（过载分支） |
| `missing` / `缺件` | formal_missing | 仅衬衫 + 西裤（正式缺件，优先级高于 `formal`） |

用例字段：

```yaml
- case_id: c01
  user_id: p01                 # 5 个虚拟用户，每人 4 套
  scenario: commute            # commute|date|sport|formal|home（覆盖要求见 docs/08 §2.1）
  style_ref: minimal           # minimal|french|american_vintage|japanese|sweet_cool
  difficulty: good             # good|bad|boundary
  profile_refs: [...]          # 参考穿搭 refs（建画像）
  outfit_refs: [...]           # 待评审 refs
  occasion: business           # 必须显式给，否则 D4 整组跳过
  season: autumn
  annotators: [82, 85, 86]     # 3 名（虚拟）标注者的总分
  human_verdict: strongly_recommended
  human_note: 一句话理由
  tags: [pair_check:commute_p01_p05]
```

## 4. 标注协议与诚实披露（重要）

- **这是演示用模拟标注**：3 名虚拟标注者按统一 rubric 独立给总分，**不是**真实形象顾问盲评。
  真实标定必须按 docs/08 §2.2 执行（3 名标注者、独立盲评、Krippendorff α ≥ 0.6）。
- 标注口径（rubric）写在 `cases.yaml` 头部：85–95 / 70–84 / 60–69 / 50–59 / 35–49 五档，
  verdict 由三人均值按 `>=80 强烈推荐 | 65–79.99 适合 | 50–64.99 一般 | <50 不适合` 换算。
- **标注先于跑分完成**，标注时未查看系统输出，因此 Spearman 是真实的人机一致性测量。
- 冒烟集只标总分，不做维度分标注；维度 Spearman 留给 100 套迭代集。
- 风格归类（`style_ref`）是人工判断。mock 剧本没有「美式复古 / 日系 / 甜酷」标签，
  这 3 类按搭配特征人工归类（见 `cases.yaml` 头部说明），与解析器输出的 `style_tags` 不必然一致。
- 低质量图降级路径（docs/08 §2.1 要求 3–5 张低质量图）未纳入本冒烟集：mock 解析不产生
  低置信度输入，硬造会污染指标；降级路径由 `tests/test_synthesis.py` / gate 测试覆盖。

## 5. 指标与门槛（docs/08 §3）

| 指标 | 定义 | 门槛 |
|---|---|---|
| 总分 Spearman ρ | 系统 `total_score` vs 3 人总分均值 | ≥ 0.60 |
| Verdict 准确率 | 4 分类完全一致率 | ≥ 65% |
| 「不适合 → 强烈推荐」 | 混淆矩阵该格 / 用例数 | ≤ 5% |
| 扣分可追溯率 | 扣分命中项带 `rule_id` + `evidence` + `suggestion` 的比例 | 100% |
| 确定性 | 同输入跑两次 `total_centi` 与命中序列完全一致 | 100% |

额外自检（不设门槛，但会打印/写入 JSON）：

- **INV-2 加法性**：`total_centi == base_centi + Σ round(delta_applied×100)`；
- **维度分之和**：`Σ dimension_scores[].score_centi == total_centi`；
- **veto 一致性**：`vetoed ⇒ verdict == unsuitable`；
- **建议自洽**：`|expected_gain − Σ|delta_applied(所引用命中)| | ≤ 0.05`（docs/04 §5.8.2）；
- **覆盖度**：场景/风格/难度/画像/配对样本是否满足 docs/08 §2.1（不满足会列出具体缺口）。

> 指标不达标时脚本**不会调参、不会修数据**：退出码为 1，`report.json` 里列出偏差最大的 5 个用例。

## 6. `report.json` 结构

```jsonc
{
  "schema": "eval-report/1.0",
  "generated_at": "...",
  "cases_meta": { /* cases.yaml 的 meta（含模拟标注披露） */ },
  "coverage": { "ok": true, "problems": [], "scenarios": {...}, "styles": {...}, ... },
  "environment": { "python": "...", "offline_mode": true, "rule_count": 50,
                   "ruleset_version": "rs-2026.10.1", "providers": {...} },
  "metrics": {
    "spearman_total": 0.0, "spearman_p_value": 0.0,
    "verdict_accuracy": 0.0, "confusion_matrix": { /* 行=人工，列=系统 */ },
    "severe_confusion_rate": 0.0,
    "traceability": 1.0, "determinism": 1.0,
    "thresholds": { /* 每项 {label, op, threshold, actual, pass} */ },
    "passed": true,
    "worst_deviations": [ /* 偏差最大的 5 个用例 */ ]
  },
  "cases": [ /* 逐用例：系统分/人工均值/verdict/命中数/确定性/不变式 */ ],
  "honesty_note": "..."
}
```

## 7. 最近一次实测（2026-10-31，ruleset rs-2026.10.1，离线模式）

> 权威数据以 `eval/report.json` 为准（脚本生成）；下面是当次快照，便于评审快速查看。

| 指标 | 门槛 | 实测 | 结论 |
|---|---|---|---|
| 总分 Spearman ρ | ≥ 0.60 | **0.7376**（p=0.0002，n=20） | PASS |
| Verdict 4 分类准确率 | ≥ 65% | **65.0%（13/20）** | PASS（刚好压线） |
| 「不适合 → 强烈推荐」 | ≤ 5% | **0 例（0.0%）** | PASS |
| 扣分可追溯率 | 100% | **100%（71 个扣分项）** | PASS |
| 确定性 | 100% | **100%（20 套各跑两次）** | PASS |
| 覆盖度自检 | docs/08 §2.1 | 场景 5/5、风格 5/5、难度 6/6/8、5 用户×4、5 对配对 | PASS |
| INV-2 / 维度分之和 / veto 一致性 | 无违反 | 20 套 0 违反 | PASS |
| expected_gain 自洽 | ≤0.05 分 | 20 套 0 违反 | PASS |

**偏差最大的 5 个用例（如实列出，未做任何修正）**

| case | 系统 | 人工均值 | 偏差 | 说明 |
|---|---|---|---|---|
| c13 formal_suit（正式） | 64.0 | 86.00 | −22.00 | 黑西装+白衬衫被 R-COLOR-006（明度断层 0.72）、R-STYLE-003（配件间 style_jaccard=0）、R-DET-003（真丝衬衫+羊毛西装判季节冲突）连扣 9 分；人工认为正装的黑白对比与丝/毛叠穿都是正常穿法 → **规则缺正式场合例外**，需 rules-author 复核 |
| c11 color_clash（运动） | 66.2 | 48.00 | +18.20 | 撞色 hard 规则确实触发（veto → verdict=不适合，**分类与人工一致**）；但契约规定 veto 不把总分置 0（docs/04 §5.8.1），总分仍 66.2，Spearman 用总分因此吃亏 |
| c07 mono_gradient（约会） | 64.0 | 76.67 | −12.67 | 「法式」只出现在大衣上，被 `outfit.style_tags` 的聚合阈值（count≥2 或面积≥0.30）过滤掉，于是 R-STYLE-002 判「与画像无交集」→ 是否应让外套计入整身风格，需 rules-author 复核口径 |
| c02 mono_gradient（通勤） | 72.0 | 82.33 | −10.33 | 同 c07 口径 + R-STYLE-003/005 扣分偏重；人工认为驼棕同色系很稳 |
| c04 casual_street（运动） | 64.0 | 72.67 | −8.67 | R-STYLE-002（画像无交集）+ R-PROP-002/007（松身上衣+阔腿裤+厚底鞋）叠加；人工认为运动休闲可接受 |

> 上述偏差是**规则调优线索**，不是评测脚本问题；本轮不修改规则、不调整人工标注（那样会变成自评自测）。

## 8. 已知局限

- 20 套是冒烟集，样本量小，Spearman 的 95% 置信区间很宽；结论只能用于方向判断。
- 模拟标注不是真实盲评（见 §4）；上线前必须用真实标注者重跑。
- 未计算「画像区分度」（docs/08 §3 的第二关键指标）：5 对配对里 4 对同场合，可测，
  但本轮脚本未纳入；建议 100 套迭代集时一并实现（中位数 ≥8 分）。
- 未覆盖低质量图降级路径与跨 ruleset 重放（`replay`），属二期评测范围。

