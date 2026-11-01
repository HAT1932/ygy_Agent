# AGENTS.md — 给任何新会话 / 新协作者的第一份文件

> **为什么有这个文件**：AI 对话的上下文**不会跨会话保留**。你（新会话的 AI）默认对之前的
> 排障过程一无所知。这个仓库里唯一可靠的记忆是：**代码 + 测试 + 文档 + git log**。
>
> 新会话请按这个顺序读，不要跳：
> 1. 本文件（约 5 分钟）
> 2. [`docs/10-implementation-findings.md`](./docs/10-implementation-findings.md) —— 17 个已踩过的坑，每条都有实测数据
> 3. [`src/contracts.py`](./src/contracts.py) —— 全系统唯一的数据形状（FROZEN）
> 4. [`docs/03-rule-engine-and-scoring.md`](./docs/03-rule-engine-and-scoring.md) §4–§5 —— 打分口径
>
> 判断"某件事到底怎么定的"，优先级：**代码 + 测试 > docs/10 > docs/0x 设计稿 > README**。

---

## 1. 项目是什么

面向「每天决定穿什么」的人群：用户传几套参考穿搭 → 系统建立**风格画像** → 再传一套待评审
穿搭 → 输出 **是否适合 / 打分 / 建议**。核心是**双塔召回 + 千条规则引擎**：

- **双塔**：用户塔（画像向量）× 搭配塔（候选搭配库 `data/outfits.yaml`）→ top-5 召回。
- **规则引擎**：50 条 YAML 规则（`rules/`），五维度加权（D1 色彩 20 / D2 风格 25 / D3 比例 20 /
  D4 场合 20 / D5 细节 15），确定性整数（厘点）打分。
- **可解释**：每条扣分必有 `rule_id` + 证据 + 建议（INV-3）。

## 2. 怎么跑（最短路径）

```powershell
cd E:\Chuang_Yi_skill
.\.venv\Scripts\python.exe -m pytest -q          # 238 项测试，必须全绿
.\.venv\Scripts\python.exe run.py --online       # 真实 VLM(qwen) + LLM(deepseek)，读 .env
.\.venv\Scripts\python.exe run.py --offline      # 无 key 也能跑通：mock 解析 + 模板文案
#   前端 http://127.0.0.1:8000/   API 文档 /docs   健康检查 /v1/health
```

环境已经建好（`.venv`，继承 Anaconda 的 site-packages，**故意不装 torch**）。重建见
`scripts/setup_env.ps1`。**不要用系统 python 跑测试**（依赖装不上/版本不同）。

---

## 3. 唯一的真相来源

| 你想知道 | 看这里 | 注意 |
|---|---|---|
| 数据结构 / 枚举 / 不变量 | `src/contracts.py` | **FROZEN**：改动必须 bump `CONTRACT_VERSION` 并同步 `docs/04` |
| 打分口径 / 维度容量 / 判定阈值 | `docs/03` §4–§5 + `src/scoring.py` | `BASE_SCORE_CENTI = 7000`，整数厘点 |
| 规则语义 | `rules/*/*.yaml` + `src/rules/loader.py` 的 lint | 改规则必须过 lint + fixtures + eval |
| 踩过的坑 / 为什么这么写 | **`docs/10`** | 17 条，含实测数据；改代码前先搜关键词 |
| 当前实测指标 | `eval/report.json`（跑 `eval/run_eval.py` 生成） | README 的指标表来自这里 |
| 运行配置 | `.env`（gitignored） / `.env.example` | key 绝不入库、绝不硬编码 |

## 4. 不可违反的约束

1. **INV-1 确定性**：同 `(outfit, profile, ruleset)` → 同分同序。禁止任何 `random`/时间/哈希序。
2. **INV-2 可加性**：`total_centi == base_centi + Σ delta_applied_centi`（整数，无浮点误差）。
3. **INV-3 可追溯**：每条命中必须有 `rule_id` + `evidence`；建议的 `expected_gain` 必须能对上 ledger。
4. **INV-4 预算守恒**：`base_d + boost_budget_d == max_d`（不得靠调 `base_score` 让分数好看）。
5. **INV-5 模板自足**：规则文案脱离 LLM 也要读得通；LLM 只做润色，且必须过 `check_facts`。
6. **禁止静默降级**：走了 mock / 少了图 / 丢了单品，都必须出现在 `meta.data_source` 或诊断里。
7. **改契约 = 升版本 + 同步 docs/04 §9 变更记录**；改规则集 = 升 `RULESET_VERSION`（`rs-YYYY.M.N`）。
8. **建议模板不得假设具体单品**（不能写死"把西装外套换成…"），也不得用"那件/它"这类指代
   （lint：`TPL_PRONOUN_AMBIGUOUS`）。理由见 F-17 附录。

## 5. 已踩过的 17 个坑（每条都付过代价）

| ID | 症状（用户/我们看到的） | 教训 | 防复发位置 |
|---|---|---|---|
| F-01 | 分数最高只到 79，永远升不到"强烈推荐" | 先查规则误伤与死规则，**不要动 `base_score`** | `eval/`、`scripts/rule_coverage_report.py` |
| F-02 | 34% 规则在真实解析能力下无区分度 | 规则必须有正负夹具，且能判别 | `fixtures/`、规则 lint |
| F-03 | 换 embedding 后端后余弦阈值全线失效 | 阈值必须按后端标定（`calibrate_style_cosine`） | `src/pipeline.py` |
| F-04 | formality 在不同来源口径不一（0..1 vs 1..5） | 单位必须在契约层钉死 | `src/contracts.py` |
| F-05 | 契约缺字段，实现和文档各写一套 | 先改契约再写代码 | `docs/04` |
| F-06 | 评测集是合成的，不能证明审美质量 | 指标达标 ≠ 产品可用；必须用真人样例抽查 | `eval/` |
| F-07 | 文档数字与实测不符 | 指标必须从脚本生成 | `eval/report.json` |
| F-08 | 纯统计画像区分度只有 5.0 分（FAIL） | 个性化**必须**引导用户声明 `banned_styles`/`explore_tags` | `scripts/profile_discrimination.py` |
| F-09 | 首轮标定后 5 条测试红 | 改阈值要同步夹具与断言，期望值不变 | `fixtures/` |
| F-10 | 人形检测把包当成裤子（遮挡） | 裁剪能治背景/旁人，**治不了身前遮挡**；模型自评不可信 | `src/parsing/person_crop.py` |
| F-11 | 伪视觉 160 维（哈希）"看起来能用" | 换真 FashionCLIP；ONNX 不需要 torch | `src/towers/visual.py` |
| F-12 | 参考越多越"不准" | 3–5 张同风格主线最优；>5 张摊薄 top1 | `MAX_RECOMMENDED_REFS` + 诊断 |
| F-13 | 告警正常，结果却是假数据 | **静默降级是最大陷阱**：先看 `meta.data_source` | `_data_source()` + UI 红条 |
| F-14 | 粘 Windows「复制文件地址」（带引号）→ 降级 mock | 引号/`file://`/空白都要归一化 | `BaseParser.normalize_ref` |
| F-15 | `verdict=suitable`，总评却写「一般」 | `str(Enum) != .value`；查表一律走 `verdict_key()` | `src/synthesis/templates.py` |
| F-16 | 8 张参考 → 30 条机器码告警拼成一行，用户以为全坏了 | **诊断要分级 + 归属第几张 + 说明影响**；成功信息不该叫"告警" | `src/diagnostics.py` |
| F-17 | 人判「Y2K 古早混搭」，系统判「一般 56 分」；建议里出现"换掉西装外套" | ① 词表/规则要认同义词（Y2K≈千禧≈古早）② formality 必须定义方向 ③ 色相统计要排除黑白灰 ④ 建议模板不得假设单品 | `src/styles.py`、`R-STYLE-002/003`、`R-OCC-004` |

> 这 17 条的共同点：**故障几乎都不是"模型看不出来"，而是语义在词表/规则/模板/UI 之间被曲解或丢弃。**
> 排障顺序永远是：**先分清是「感知错」还是「语义错」**。

## 6. 改完必须跑的验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # 必须 238/238
.\.venv\Scripts\python.exe eval\run_eval.py      # Spearman / verdict 准确率 / 追溯率 / 确定性 门槛
.\.venv\Scripts\python.exe scripts\rule_coverage_report.py   # 改规则时必跑：找无判别力规则
```

改 **规则** → 额外跑 lint（`load_rules()` 自带）+ 更新 `fixtures/`。
改 **契约** → 额外 bump `CONTRACT_VERSION` + 在 `docs/04` §12 加变更记录。
改 **prompt/解析** → 用真实照片抽查，并检查 `meta.data_source == "vlm"`、`diagnostics` 是否合理。

## 7. "看起来坏了，其实是环境/进程"排查表

| 现象 | 真因 | 怎么确认 |
|---|---|---|
| 改完代码行为没变 | **Python 进程只在启动时 import**，没重启就是旧代码 | `/v1/health` 的 `providers.contract` 应等于 `src/contracts.py` 的 `CONTRACT_VERSION` |
| 报告结论看着像假数据 | 走了 mock 降级 | 报告顶部红条 + `meta.data_source` |
| 用户贴的路径"读不到" | Windows「复制文件地址」带引号 | 已归一化（F-14）；仍失败就看 `diagnostics` 的 `IMAGE_UNDECODABLE` |
| 项目根出现 `pytest-of-*/` | 本机 `%TEMP%` 不可写，`tempfile` 回退到 cwd | 已在 `.gitignore` 忽略（属环境问题，非代码 bug） |
| `pip install` 卡死 | 本机访问 PyPI 慢 | 用 `scripts/setup_env.ps1 -Isolated` 或清华镜像 |

## 8. 还没解决的（别当成已完成）

- **单品漏检**：画面边缘/被遮挡的配饰（如豹纹小包）仍会漏；单次解析有 ±1 件方差。
  下一步：**三次采样投票**（成本 ×3）来降方差。
- **图案粒度**：受控 `pattern` 只有 5 值（波点被记成 `graphic`）；已用 `pattern_detail`
  存"波点/豹纹"，但**还没有规则消费它**。
- **商品搜索**：目前用「VLM 生成商家标题 + 本地词表」替代（`merchant_title` + `src/styles.py`）。
  真接口（阿里云图像搜索 / 淘宝客 / 抖音开放平台）都需要资质与计费，未接入。
- **评测集仍是合成的**（F-06 未解）：真实照片只有零星案例，尚不足以证明审美判断质量。

## 9. 与人协作的约定

- **语言**：中文；代码注释解释"为什么"，不解释"是什么"。
- **先诊断再动手**：把"用户看到的"和"系统实际的"分开列证据（一条命令能拉出 `state.json`/日志再说）。
- **不静默兜底**：任何降级、跳过的判断都必须留痕（诊断/告警/日志三选一以上）。
- **提交信息**写清"问题 → 根因 → 修法 → 实测"，不要写 "fix bug"。
- **不擅自扩大范围**：契约/规则集的语义变更属于产品决策，先问再改（F-01 就是这么被搁置的）。
