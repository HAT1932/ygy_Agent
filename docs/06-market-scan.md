# 06 · 市场调研：AI 穿搭赛道产品全景与逆向候选筛选

> 版本：v0.1 ｜ 日期：2026-10-31 ｜ 作者：market-analyst ｜ 状态：待评审
> 上游：[00-baseline](./00-baseline.md)（产品定位）｜[01-prd](./01-prd.md)（P1 判断优先 / P2 每分必可溯 / P3 个人化优先）
> 本文只做事实收集与判断，不修改任何设计决策。所有判断均可回溯到本文标注的来源 URL。

---

## 0. 先读这三个结论（其余章节都是证据）

| # | 结论 | 对我们的意义 |
|---|---|---|
| **C1** | **「AI 穿搭打分」是一个已经极度拥挤、但没有任何赢家的品类。** 在 App Store 美国区搜 `outfit rating` / `rate my outfit` / `outfit score`，返回的 40 个 App 中**绝大多数评分人数是 0–16**（见表 2.3）。中国区同理。这个品类里唯一算有点规模的 `GRWM` 也就 1,869 个评分。 | 「能打分」不构成任何差异化。PRD §1.2 的判断成立，且比我们原本想的更极端：**不是我方要抢先做，而是这条路已经被几十个团队试过、全部失败。** |
| **C2** | **未发现**任何产品在做「先从用户已有的穿搭里学习个人风格画像 → 再对一套新搭配输出可解释的、分维度的评分 + 改法」。最接近的三个都只做到一半（见 §2.5）：Style DNA 有画像但来自**自拍生物特征**且只做**单品级**判断；Cladwell 会学你的颜色与「Style Direction」但只做**生成**不做**评审**；小衣橱会**点评并给理由**但没有学习型画像。 | 我们 P0-3 + P0-6/P0-7 的组合是**空的**。这是好消息，但也意味着没有现成的用户教育，需要自己做价值说明。 |
| **C3** | **最值得逆向的是 `app.cladwell.com`（Cladwell 的 Web 版，Next.js SPA，HTTP 200 可直接访问、无需 APK/IPA）。** 第 2 层那几款打分类 App 反而**不值得逆向**——它们的评分维度已经从商店页完整拿到（见 §2.1），剩下的只有 prompt，而 prompt 逆向的产出并不能替我们回答「这个维度该给几分」。 | 逆向的投入应该花在「画像 → 搭配」的产品化结构（Cladwell）和「风格知识 taxonomy」（Style DNA），而不是花在「怎么让 LLM 打 78 分」（已饱和且无价值）。 |

---

## 1. 方法与证据等级

**网络路径**：本环境 `web_fetch` 被 fake-IP DNS 阻断，全部改用 `pwsh + Invoke-WebRequest`；移动端产品元数据改用 **iTunes Search API**（`itunes.apple.com/search`，结构化 JSON，比抓 App Store 页面更可靠）；学术元数据改用 **OpenAlex API**（Semantic Scholar 被 429 限流）。

**证据等级标注**（全文严格遵守）：

| 标记 | 含义 |
|---|---|
| 无标记 | 已抓到正文，可引用原句 |
| **仅标题级证据** | 只拿到搜索结果标题 / 页面标题，正文未取得 |
| **未查证** | 本次未能取得任何可用证据，不做推测 |
| **推断** | 由已取得的事实推出的判断，不是原文 |

**抓取失败清单（诚实记录，供复核）**：

| 目标 | 失败原因 |
|---|---|
| `apkpure.com` / `apkcombo.com` / `apk.support` | HTTP 403 / TLS 连接中断 → **本次未取得任何公开 APK 直链** |
| `visenze.com`（含 /solutions） | HTTP 403（反爬） |
| `indyx.app` / `whering.com` / `stymate.app` | TLS「基础连接已关闭」，疑似 CDN 拦截 |
| `smartli.com` | HTTP 403 |
| `saveyourwardrobe.com` | HTTP 308 重定向未跟随 |
| `labs.google/doppl/` | HTTP 404（页面已下线，与 Doppl 关停一致） |
| `m.thepaper.cn` | HTTP 403 |
| `daxue.taobao.com/...` | 正文 JS 渲染，仅拿到标题「规则动态」 |
| `genlook.app/...` | HTTP 404 |

---

## 2. 第 2 层：穿搭打分 / 评审类（重点）

> 这是与我们 demo 最贴近的一层，也是唯一需要逐条抄录维度的一层。

### 2.1 五款必须核实的产品

---

#### ① 穿搭日记 - AI 穿搭打分（OOTD Diary）

| 字段 | 内容 |
|---|---|
| App Store | id6761793902（中国区） |
| 开发者 | SymphonyMeld Technology Co.,Ltd（统一社会信用代码 91450100MAC0LCDM9J，广西） |
| 实际工作室 | 二作工作室 erzuo（独立开发者，官网自称「一个独立开发者的工作室」） |
| 平台形态 | **仅 iOS**（iPhone；未针对 macOS 验证）。**无 Android、无 Web 应用** |
| 官网 | `www.erzuo.com` 存在，但**是工作室营销页，不是产品 Web 版**；列出 OOTD Diary 与未上线的 Mirror 两款 |
| 大小 / 语言 | 43.4 MB ／ 简体中文 + 英语 |
| 评分 | **2 个评分，5.0** |
| 首次上架 | 1.0 版本 4 月 30 日；最新 1.1.0 于 8 月 12 日 |

**5 大维度评分（原文逐条抄录，App Store 描述）：**

> - 整体协调性（3分）
> - 时尚度（2分）
> - 场景适配度（2分）
> - 细节质感（2分）
> - 个人辨识度（1分）
>
> → 满分 10 分

**等级（原文）**：满分穿搭 / 优质穿搭 / 中等穿搭 / 基础穿搭 / 需优化

**其他能力（原文）**：AI 10 秒内出结果；自动识别穿搭单品并「逐步积累成你的衣橱」；每日穿搭自动留存；累计 6 次打分解锁「风格报告」（各维度均分、风格走势、专属风格标签）；「买前咨询」（拍单品判断值不值得买）；「今天穿什么」（说场合 → 3 套方案）。

**定价（App 内购买，原文）**：会员月卡 ¥12.00、会员年卡 ¥68.00、终身会员 ¥128.00。
免费版：每日 1 次打分 + 简短评语 + 1 条核心建议 + 本地最多 30 套。

**低质量输入的降级（更新日志原文）**：
> 照片里没有可识别的穿搭时会明确提示，不再返回 0 分结果

→ 这条对我们**很有参考价值**：它证明「无有效输入时不硬给分」在真实产品里是一个被用户投诉出来的坑（PRD US-8 / baseline §10 已经写了这条，属于被市场验证过的需求）。

**来源**：
- App Store 页面（已抓正文）：https://apps.apple.com/cn/app/id6761793902
- 工作室官网（已抓正文）：https://www.erzuo.com/

**⚠ 关键判断**：`个人辨识度（1分）` **不是**「像不像你」。理由：整个 App 的流程是「上传照片 → AI 识别 → 给分 → 存档」，**全程没有任何「上传你的参考穿搭 / 建立你的画像」的入口**（App Store「使用方式」四条 + erzuo.com 一句话描述均无）。在没有用户画像输入的前提下，这个维度只能被理解为「这套搭配本身是否有辨识度」。**这正是我们与它最本质的分野，也是最容易被误读的一栏。**

---

#### ② Endam - AI穿搭评分·时尚搭配助手

| 字段 | 内容 |
|---|---|
| App Store | id6755446008（中国区） |
| 开发者 | Voventech / **Voventech OÜ**（爱沙尼亚注册，属 App 工厂型团队：同期在架 Purchio、Ensar 祷告时间、Linewinks、Fotempo、Golistry 等 5+ 款无关 App） |
| 平台形态 | **iOS + Android（Google Play 均有）**。**无 Web 应用**，`endamai.com` 为营销站 |
| 官网 | `endamai.com`（有 `terms-of-use.html` / `privacy-policy.html`，正式站点） |
| 包名（Play） | `com.voventech.endam` |
| 大小 / 语言 | 63.9 MB ／ 简体中文 + 另外 12 种（共 13 种） |
| 评分 | App Store：**「此 App 尚未收到足够的评分或评论，无法显示整体评分」**（原文）；Google Play：**100+ Downloads，页面未显示星级/评分数**（原文仅有「100+ Downloads」「Contains ads」「Rated for 3+」）→ 两端量级均为「极小」 |
| 年龄分级 | **13+，含广告** |

**评分维度（两处交叉核对，原文逐条抄录）：**

App Store 描述：
> 上传穿搭照片，选择场合，即可获得风格评分与可执行建议（**配色、版型/合身度、比例与整体氛围**）

Google Play 描述（更明确，已抓到正文）：
> • Get an instant outfit score
> • See separate feedback for **color, fit, occasion, and accessories**
> • Learn which details strengthen your look and which ones hold it back
> • Choose a supportive, balanced, or brutally honest AI voice

官网首页的实机截图区（数值示例，已抓正文）：
> Overall score **73** / **Color 68** / **Fit 74** / **Occasion 80**
> Clear **0–100** outfit score｜Color, fit, occasion and accessory breakdown

→ **合并后的实际维度**：Overall（0–100）+ Color（配色）+ Fit（版型/合身度）+ Occasion（场合）+ Accessories（配饰）。**这是 4 个子维度 + 1 个总分**，与中国区描述里的「配色、版型/合身度、比例与整体氛围」措辞不完全一致（中英文描述不同步，属产品文案缺陷）。

**其他能力（原文）**：两套穿搭并排对比；个人色彩季型（color season，官网示例「Soft Autumn」）；AI 造型师语气（鼓励型/平衡型/直言不讳型；官网另写为 Savage AI / Elite Judge / Girl POV / Trend Sniper 四人格）；连续记录 + 徽章 + 分数走势；场合清单（Casual / Work / Date night / Party / Travel / Formal）；「No account needed」「Free to start」；分享与邀请好友得次数。

**定价（App 内购买，原文）**：周 ¥6.50、月 ¥16.80、年 ¥99.90、终身 ¥250.00。

**来源**：
- App Store（已抓正文）：https://apps.apple.com/cn/app/id6755446008
- 官网（已抓正文）：https://endamai.com/
- Google Play（已抓正文）：https://play.google.com/store/apps/details?id=com.voventech.endam

**⚠ 关键判断**：Endam 的「个人色彩季型」是**从一张自拍推出的生物学色彩季型**（12 季型），**不是**「从你已有的穿搭推出的风格画像」。它连「上传参考穿搭」的入口都没有。所以它虽然有「个人」二字，但**个人化只发生在色彩层，且是通用的**——两个人拍同一张自拍会得到同一个季型，与「你平时怎么穿」无关。

---

#### ③ Outfitly – AI Outfit Rating

| 字段 | 内容 |
|---|---|
| 商店 | Google Play：`com.outfitai.outfitly` |
| 开发者 | **LensWork** |
| 平台形态 | **Android**（未在 App Store 检索到同名同开发者条目 → iOS 版 **未查证**） |
| 官网 | **未查证 / 未见**（Play 页面未列出官网，仅条款链接指向模板页） |
| 评分 / 量级 | **4.3★ / 6 条评论 / 100+ 下载**；「Rated for 3+」；含内购 |
| 更新时间 | **Updated on Apr 9, 2026** |

**评分维度（原文逐条抄录）**：
> • AI Outfit Analysis — Get instant, intelligent feedback on your outfit using advanced AI.
> • Smart Scoring System — Understand your style with a detailed score based on **fit, colors, and occasion**.

→ **维度：fit + colors + occasion（3 项）+ 一个总分**

**其他能力（原文）**：Gamification（徽章、连续、升级）；Outfit Comparison（多套对比）；Share score cards（社交分享）；「PRIVACY FIRST — photos … used only for outfit analysis. We never sell your data」。

**定价**：In-App Purchases（**具体档位价格未查证**）。

**来源**：https://play.google.com/store/apps/details?id=com.outfitai.outfitly

**⚠ 关键判断**：`100+ 下载` + `6 条评论` → 这是**一个几乎没人用的早期产品**，不是竞争对手，是**同赛道失败的样本**。值得注意的是它把「社交分享 score card」当增长引擎——这是本赛道唯一一个被反复采用的增长假设（穿搭日记的「风格报告」、Endam 的「Share results」、小衣橱的「灵感复刻」都在往这个方向靠）。

---

#### ④ Style DNA: AI Stylist & Closet App

| 字段 | 内容 |
|---|---|
| App Store | id1358319821（美区） |
| 开发者 | **AI Style by DNA S.L.**（西班牙） |
| 平台形态 | **iOS + Android 双端**。**无 Web 应用**（`styledna.ai` 为营销站，CTA 是「Join now」→ 跳商店） |
| 大小 / 语言 | 134.5 MB ／ EN + 2 |
| 评分 / 量级 | App Store：**7.5K 个评分，4.3**；Google Play：**18.1K reviews，4.2，1M+ Downloads**。官网与商店自述「**Trusted by over 3 million users**」，「featured in Vogue, NYT, Forbes, and TechCrunch」 |
| 年龄分级 | 16+ |

**分析维度（原文逐条抄录，这是本层最有价值的一份 taxonomy）：**

> Using a simple selfie, your full style profile is created in 35 seconds:
> - **Color analysis** — discover your seasonal color type from one of **12** (Bright Spring, True Summer, Soft Autumn, Deep Winter, and more), and get a personal palette that complements your skin undertone.
> - **Body type guide** — for **hourglass, triangle, inverted triangle, rectangle, pear, apple, oval, and plus-size** figures, get tailored fashion recommendations.
> - **Style type** — **Romantic, Natural, Classic, Dramatic, Gamine**, and **Kibbe-inspired** profiles help you pick what flatters your unique features.

**其他能力（原文）**：AI 造型师聊天机器人（可问「What goes with green cargo pants?」「What should I wear to a wedding?」）；每日从你的衣橱出 5 套；虚拟衣橱（拍任何单品入库）；**Item match check**（在店里拍任何单品，判断是否适合你的色彩季型与体型）；个人购物（26,000 品牌 / 231 零售商，按色彩季型+体型过滤）；occasion 覆盖（business casual / weddings / date nights / gym / travel）。

**定价**：订阅制（App Store 描述提到「subscription plan giving unlimited access」，**具体档位价格未抓取到**）。

**来源**：
- App Store（已抓正文）：https://apps.apple.com/us/app/id1358319821
- 官网（已抓正文）：https://styledna.ai/
- Google Play（标题+评分已抓）：`https://play.google.com/store/search?q=style%20dna%20ai%20stylist&c=apps`

**⚠ 关键判断**：**Style DNA 是全赛道离我们最近的一个，但方向正交。**
- 它有真画像（`style profile`），但画像来源是**一张自拍** → 得到的是「你的色彩季型 / 体型 / Kibbe 风格类型」，这是**生物特征 + 形象顾问知识库**，与「你平时爱穿什么」无关。
- 它有判断（`item match check`），但判断对象是**单品**，不是**整套搭配**，而且输出是「适合/不适合你」，**没有分维度评分、没有扣分溯源、没有「把 X 换成 Y」**。
- 它真正的护城河是那套 **12 色季 × 8 体型 × 6 风格类型** 的分类学（taxonomy）+ 26,000 品牌的商品库对接。**这套 taxonomy 是我们规则引擎最好的外部知识源之一**（见 §11 建议）。

---

#### ⑤ Meta「Style Sketch」

| 字段 | 内容 |
|---|---|
| 归属 | Meta（无独立发布会，被媒体「悄然上线」发现） |
| 上线时间 | 2026-10-29 / 30（TechCrunch 首报，2026-10-29） |
| 平台形态 | **iOS + Android**；**区域性限量**：仅**印尼、新加坡、泰国**三国 |
| 官网 / Web 版 | **未查证 / 未见** |
| 定价 | **未查证**（现有报道未提及） |
| 评分 | 上线仅 1–2 天，**无有效评分数据** |

**核心机制（原文，digitaltoday 英文版已抓正文）：**
> It creates a virtual closet using photos posted on **Instagram** and suggests clothing suited to the weather and schedule. … The app links to a user's Instagram account and registers clothing from existing posts in a virtual closet. … The AI analyses the wardrobe built in this way and **recommends outfit combinations that match the user's preferences**. It also supports a feature that proposes new looks centred on a specific item selected by the user. It also offers a **virtual fitting** function … try recommended outfits on a **virtual model tailored to their body shape and skin tone** … If they enter the destination, duration and planned activities, the app recommends **day-by-day clothing** based on local weather and schedules.

**来源**：
- https://www.digitaltoday.co.kr/en/view/121896/meta-launches-style-sketch-ai-fashion-app （已抓正文）
- https://www.antaranews.com/berita/5821119/meta-luncurkan-aplikasi-fesyen-berbasis-ai-style-sketch （已抓正文，印尼语，内容一致）

**⚠ 关键判断**：**这是全赛道唯一一个真正「从用户已有穿搭学习偏好」的产品**——它直接读 Instagram 历史发帖建衣橱，再按「user's preferences」推荐。但它做的是**生成/推荐 + 虚拟试穿**，**完全没有「评审一套搭配并解释扣分」的能力**。所以：
- 它在「学习用户已有风格」这件事上**与我们重叠**（且它的数据源比我们强：Instagram 历史）；
- 它在「可解释判断」这件事上**与我们零重叠**。
- 战略含义：Meta 一旦把「Style Sketch 的衣橱」接到「评分/点评」，我们会非常被动。**这是本报告中风险最高的一条外部信号**（见 §9）。

---

### 2.2 五款横向对比（核心产出表）

| 产品 | 公司 / 地区 | 平台形态 | 核心能力一句话 | 评分维度（原文） | 商业模式 | **是否需要用户画像 / 参考穿搭** | 与我们重合度 | 客户端可获取性 |
|---|---|---|---|---|---|---|---|---|
| **穿搭日记** | SymphonyMeld / 二作（中国·广西，独立开发者） | iOS only | 拍照 → 5 维 10 分制打分 + 3 条建议，每日留存 | 整体协调性3 / 时尚度2 / 场景适配度2 / 细节质感2 / **个人辨识度1** | 月¥12 / 年¥68 / 终身¥128 | **不需要**（全程无参考穿搭入口） | 中 | 低（iOS only，无 APK，无 Web） |
| **Endam** | Voventech OÜ（爱沙尼亚） | iOS + Android | 拍照 + 选场合 → 0–100 分 + 4 子维度 + 语气可选 | Overall / **Color / Fit / Occasion / Accessories** | 周¥6.5 / 月¥16.8 / 年¥99.9 / 终身¥250，含广告 | **不需要**（仅自拍取色彩季型） | 中 | 中（有 Play 包名 `com.voventech.endam`，量小易取；无 Web） |
| **Outfitly** | LensWork | Android（iOS 未查证） | 拍照 → 打分 + 徽章 + 对比 + 分享卡 | **fit / colors / occasion** | 内购（档位未查证） | **不需要** | 中低 | 中（Play `com.outfitai.outfitly`；官网未查证） |
| **Style DNA** | AI Style by DNA S.L.（西班牙） | iOS + Android | 一张自拍 → 形象画像（色季/体型/Kibbe）→ 衣橱搭配 + 单品适不适合 | **12 色季 / 8 体型 / 6 风格类型（Romantic·Natural·Classic·Dramatic·Gamine + Kibbe）**；**无 0–100 整套评分** | 订阅（档位未查证）+ 商品导购分成 | **需要，但画像是自拍生物特征**，非「你已有的穿搭」 | **中高**（唯一有「画像 → 判断」闭环的） | **高**（Play 1M+ 下载、双端、包体可取得；但无 Web） |
| **Meta Style Sketch** | Meta（美国） | iOS + Android，仅印尼/新加坡/泰国 | 读 Instagram 建衣橱 → 按偏好推荐搭配 + 虚拟试穿 + 旅行穿搭 | **未公开评分维度（无评分功能）** | 未查证 | **需要，且从 Instagram 历史帖自动建立** | 低中（学习画像重叠，判断能力零重叠） | 低（区域限量，双端；APK 能否取到未查证） |

---

### 2.3 品类饱和度的量化证据（本次调研最硬的数字）

用 iTunes Search API 对美国区、中国区做抽样检索（`entity=software`，`limit=15`），统计**评分人数**这一唯一公开的规模指标：

**美国区 `outfit rating`（前 15 条）**

| App | 开发者 | 评分人数 |
|---|---|---|
| SLYDE - Rate Outfits | Slyde ApS | 92 |
| Sidewalk - Rate Your Outfit | Intonation Studios Inc. | 16 |
| Rate My Outfit | Burak Akin | 13 |
| Fit Score! AI Outfit Rating | Mustafa Anıl Kaya | 13 |
| DRESSFIT: Outfit Rating & Tips | Avalanche Factory | 10 |
| Twirl - AI Outfit Rating | Rishi Gurjar | 8 |
| Rate My Outfit: Outfit Rater | Arda Caglayan Ercan | 1 |
| Mirror: Rate My Outfit | Avery Intelligence Inc | 1 |
| OuchFit: Outfit Rating, Roasts | Appside Limited | 1 |
| **其余 6 款** | — | **0** |

**美国区 `rate my outfit`（前 15 条）中唯一有量的：**

| App | 开发者 | 评分人数 |
|---|---|---|
| Dressly: AI Outfit Maker | TIKVEX LIMITED | 32,914 |
| **GRWM - AI Stylist** | Aliaksei Karatkou | **1,869** ← 本层最高 |
| 其余 13 款 | — | **0–1** |

**中国区 `穿搭打分` / `穿搭评分` / `穿搭点评`（各前 10–15 条）中，所有「打分类」App 的评分人数：**

| App | 评分人数 |
|---|---|
| 穿搭日记 - AI 穿搭打分 | 2 |
| 虫虫幸运穿搭 | 2 |
| 穿搭评分 - Outfitby | 4 |
| 小衣橱 - 每日穿搭点评 | 3 |
| 丸子穿搭 | 1 |
| 穿搭分析AI: StyleBot | 0 |
| 穿搭镜 AI | 0 |
| 毒舌穿搭 | 0 |
| 穿搭评分：AI穿搭检测 | 0 |
| Clothly AI穿搭评分 | 0 |
| StyleSnap: AI穿搭评分 | 0 |
| Style Muse - AI穿搭评分 | 0 |
| Fit Score! AI穿搭评分 | 0 |
| Estila: AI穿搭评分与色彩分析 | 0 |
| Endam - AI穿搭评分 | 0 |

> **判断（推断）**：这个品类呈现典型的「**高供给、零需求验证**」形态——供给方众多（双语、跨区、模板化复制，明显有「AI 套壳批量上架」的痕迹），但没有任何一款跑出留存或口碑。**没有任何证据表明用户愿意为「给我的穿搭打分」这个功能本身付费。** 对比 PRD §2.1 的 H2「报告有用率」是生死线——市场已经在用脚投票：**「打分」不是需求，「判断得对且说得出理由」才可能是需求**。

**来源**：iTunes Search API 实时抽样（`https://itunes.apple.com/search?...&country=us|cn`），查询词与结果如上表；抽样时间 2026-10-31。样本为搜索引擎排序前 10–15 条，**不是全量普查**，但「绝大多数为 0」的结论在三个不同查询词下重复出现，稳健。

---

### 2.4 一个反直觉的对照：**衣橱管理有量，穿搭打分没量**

同一批检索里，中国区「电子衣橱 / 穿搭助手」类 App 的评分人数是**两个数量级**的差距：

| App（中国区） | 评分人数 |
|---|---|
| 尽简衣橱 - 超实用的衣橱管理工具 | **23,729** |
| 穿衣助手 | 6,463 |
| 今日装OOTD | 4,756 |
| Lookie - 淘宝智能电子衣橱 & AI 搭配助手 | 1,670 |
| 托特衣箱 | 1,579 |
| 心动衣橱 | 1,501 |
| 搭搭 - AI智能电子衣橱 | 1,077 |
| 无忧衣橱 | 1,017 |
| Yikool 依酷 | 673 |
| 我的衣橱 | 115 |
| —— 分界线 —— | |
| 小衣橱 - 每日穿搭点评 | **3** |
| 穿搭日记 - AI 穿搭打分 | **2** |

**判断（推断）**：中国用户的真实付费点是「**管好我已有的衣服**」（存量资产焦虑 + 搭配效率），不是「**请给我打分**」（自我形象评价，带来社交焦虑且不解决任何任务）。这对我们的 MVP 形态选择是**强信号**：如果我们只做「打分」，会直接撞进一个已被证明没有需求的口袋；如果我们把「画像」做成用户可以「看见自己的衣橱/风格资产」的东西（J4），就有机会搭上那 23,729 量级的真实需求。

**来源**：同上 iTunes Search API 抽样；查询词 `穿搭助手 衣橱`、`穿搭点评`。

---

### 2.5 【必答】到底有没有人做「先学习用户已有穿搭风格、再评审新搭配」？

**结论：未发现。**

本次逐一核对了 9 个最有可能的候选，全部为「未发现」。判定标准很严格：必须**同时**满足
(a) 有从用户既有穿搭/衣橱**学习**出的持久化个人风格表征，**且**
(b) 该表征被用于**评审一套新搭配**（输出判断或分数），**且**
(c) 评审结果是**可解释**的（说明理由或扣分依据）。

| 候选 | (a) 学习既有穿搭 | (b) 用于评审新搭配 | (c) 可解释 | 差距在哪 |
|---|---|---|---|---|
| 穿搭日记 | ❌ 无画像输入 | ✅ 打分 | ✅ 有评语+3 条建议 | **完全没有 (a)**。`个人辨识度` 指搭配自身而非像不像你 |
| Endam | ⚠️ 仅自拍→色彩季型 | ✅ 打分 | ✅ 有 breakdown | (a) 是生物特征非穿搭习惯 |
| Outfitly | ❌ | ✅ 打分 | ⚠️ 泛化 feedback | 无 (a) |
| StyleBot（穿搭分析AI） | ❌ 官网明写「无需账号」 | ✅ 分析 | ✅ 版型/配色/平衡/比例 | 无 (a)，且刻意不做账号=不做画像 |
| Style Muse | ⚠️「越用越懂你的喜好，推荐越来越准」 | ✅ 100 分制 4 维 | ✅ 亮点+最需改+3 条建议 | 学习只作用于**推荐**，不作用于**评审** |
| **小衣橱 - 每日穿搭点评** | ⚠️ 建数字衣橱（来自你的衣服） | ✅ 点评 | ✅「肩线垮了/比例没到/换乐福」 | (a) 只是**衣橱**不是**风格画像**；点评立场来自**12 个固定造型师人格**，不是「你」 |
| **Style DNA** | ⚠️ 画像来自自拍（非穿搭） | ⚠️ 只评审**单品** | ⚠️ 适合/不适合，无分维度 | 缺「整套搭配 × 分维度 × 扣分溯源」 |
| **Cladwell** | ✅ 照片测色 + Style Direction 问卷 + 你的衣橱 | ❌ **只生成不评审** | — | **缺 (b)**：它给方案，不做判断 |
| Meta Style Sketch | ✅ 读 Instagram 历史帖 | ❌ 只推荐/试穿 | — | **缺 (b)(c)** |

**最近的三个，各差一步：**

1. **Style DNA** —— 有画像、有判断，但画像是「你的脸/身体」，判断粒度是「单品」，且**从不给整套搭配打一个可解释的分**。
2. **Cladwell** —— 它官网的原话是「Cladwell is a personal stylist that **learns who you are** — not who fashion thinks you should become」，也有「Your colors」「Your style — A description of the style you already have」。它确实在做「学习你的风格」，但输出是**今天穿哪套**，不是**你今天这套行不行**。
3. **小衣橱** —— 这是本次调研里**产品设计最接近我们**的一个（数字衣橱 + 每日点评 + 具体到「换乐福鞋」的建议 + 算力制付费 + 灵感复刻）。但它给的是「12 位有立场 AI 造型师」的意见，**不是「根据你的画像」的意见**——这是「多人格」而非「个人化」。

> **对定位的含义（重要，请 Lead 重点看）**：
> 「个人风格画像驱动的可解释评审」这个组合，**在已核查的商业产品里是空白的**。但它空白的**原因可能不是没人想到，而是没人做成**——因为 (a) 要求用户先付出上传成本（PRD H1 的最大流失点），(b) 要求规则/评分体系足够可信（H3），(c) 要求解释足够具体（H2）。
> 也就是说：**这是一个「难而正确」的空白，不是「容易但没人做」的空白。** 竞品不约而同地绕开 (a)，全都去做「零输入的通用打分」——因为那是最容易上线的形态，也是最好套壳的形态。
> 我们赌的是：**愿意付上传成本的那部分用户，存在且愿意为「真的懂我」付费。** 这个赌注成立与否，就是 H1 和 H2，没有第三条路。

**来源**：各产品 App Store / Google Play / 官网正文（URL 见 §2.1、§3.1）；`穿搭分析AI: StyleBot` 与 `小衣橱` 的完整描述经 iTunes API 取得（`https://apps.apple.com/cn/app/id6757441857`、`https://apps.apple.com/cn/app/id6773195549`）；Cladwell 原文见 https://cladwell.com/ （已抓正文）。

---

## 3. 第 1 层：数字衣橱类

> 这一层与我们的重合度整体偏低——它们是「管理已有资产 + 生成搭配」，我们是「评审」。但**它们是用户教育最成熟、用户量最大的一层**，也是我们冷启动时要「借」的场景。

| 产品 | 公司 / 地区 | 平台形态 | 核心能力一句话 | 商业模式 | 是否需要用户画像 | 与我们重合度 | 客户端可获取性 |
|---|---|---|---|---|---|---|---|
| **Whering** | Whering Ltd（英国） | iOS + Android + **Chrome 扩展** | 「世界最大的免费社交衣橱」9M+ 用户，衣橱管理 + 穿搭记录 + 社交 | 免费（自述 free） | 弱（衣橱即画像） | 低 | 中（Web 域名 TLS 拦截未取得；Chrome 扩展可直接下载） |
| **Acloset** | Looko Inc.（韩国） | iOS + Android + **Chrome 扩展** | 8M 用户、Google Play Editor's Pick；拍一张照片 AI 建衣橱 + AI 造型师对话 | 免费 + 订阅 | 弱 | 低 | 中 |
| **Stylebook** | left brain / right brain, LLC（美国） | iOS + iPad（**2009 年至今**） | 90+ 功能的衣橱管理 + 搭配日历 + 打包清单，**无 AI** | 一次性买断（App Store） | ❌ | 极低 | 低（iOS only，老技术栈） |
| **Indyx** | Indyx Inc.（美国） | iOS + Android | 免费数字衣橱 + AI 自动抠图打标 + cost-per-wear + 真人造型师服务 | 免费 + 造型师服务 | 弱 | 低 | 中 |
| **Cladwell** | Cladwell, Inc（美国·辛辛那提，since 2016） | **iOS + Android + 真 Web 应用 `app.cladwell.com`** | 「learns who you are」：照片测色 + Style Direction + 用你自己的衣服生成每日搭配 | 免费档 + **$9.99/月 或 $79.99/年** | **强** | **中**（画像重叠、评审零重叠） | **高**（Next.js SPA，HTTP 200，无需 APK） |
| **Save Your Wardrobe** | Save your wardrobe Ltd（英国） | iOS + Android | 衣橱管理 + 接入本地**洗护/修补/改造服务**网络（可持续时尚） | 免费 + 服务分成 | ❌ | 极低 | 中 |
| **Smartli** | Lakshmi Kaanthan Shanmugam（个人开发者） | iOS | AI 扫描建衣橱 + 自动打标 + 二手转卖助手 | 免费 | ❌ | 极低 | 低（v1.0.15，**0 个评分**） |
| **StyleMate** | 多个同名开发者（STAIL TEK OOO / AYSE DEMIREL DENIZ / Elif Baser / appik） | iOS | 衣橱 + 搭配生成 | 免费 | ❌ | 极低 | 无（**4 个同名 App 全部 0 评分 → 该名字无实际产品**） |
| **Alta Daily** | Flagship AI, Inc. | iOS | 个人 AI 造型师，按衣橱+生活方式+预算+天气生成每日搭配；「advised by celebrity stylists and global editors from Vogue」 | 免费 + 订阅 | 强 | 中低（生成不做评审） | 中 |
| **Fits** | L. & J. Henne UG（德国） | iOS | 衣橱 + 搭配规划 + AI 造型建议 + 虚拟试穿 | 免费 + 订阅 | 弱 | 低 | 中 |

**关键数字**：Whering **12,021 个评分 / 4.67 / 9M+ 用户**；Alta Daily **17,966 / 4.89**；Acloset **5,500（美区）/ 8M 用户**；Indyx **1,647 / 4.77**；Cladwell **1,037 / 4.24 / 1M+ 下载**；Fits **5,733 / 4.59**；Save Your Wardrobe **281 / 4.51**；Smartli **0 / v1.0.15**。

**来源**：iTunes Search API 的 `description` / `userRatingCount` / `averageUserRating` / `trackViewUrl` 字段（原样引用）；Cladwell 官网 https://cladwell.com/ 与 Web 应用 https://app.cladwell.com/ （均已抓正文）；Whering / Acloset 官网首页已抓正文（https://whering.co.uk/ 、https://www.acloset.app/ ）。

---

## 4. 第 3 层：AI 试穿 / 虚拟形象

> **与我们的重合度：极低，且属于 PRD §2.2 明确不做的范围。** 列出是为了确证「这不是我们的战场」。

| 产品 | 状态 | 核心能力 | 与我们重合度 |
|---|---|---|---|
| **Google Doppl** | **已关停**。Google 官方帮助中心页面标题即为「Download items in Doppl before app shuts down」（**仅标题级证据**；`https://labs.google/doppl/` 已返回 404） | 上传全身照 + 商品图 → 生成上身效果 | 零（试穿非判断） |
| **ASOS「See My Fit」** | 已上线（**仅标题级证据**：Retail Week / Birmingham Mail 报道标题「Asos launches virtual try-on tool」） | 商品图映射到多体型模特上，看不同身材的穿着效果 | 零 |
| **Zalando** | 在推进「fit / 尺码」主题（已抓正文标题与栏目：`Size and fit`；文章「Rewriting the rules of fit in Europe: 3 key takeaways from CPHFW AW26」正文 cookie/导航占满，实质内容**未取得**） | 尺码与合身度（body scan / size recommendation 方向）**未查证细节** | 零 |
| **Amazon** | 有 Virtual Try-On（AR）能力，覆盖鞋/眼镜/部分服饰（**仅标题级证据**：Amazon Seller Central 帮助页「3D shopping experience and supported product types」） | AR 叠加 + 3D 商品预览 | 零 |
| **Pinterest** | 有视觉搜索与购物推荐；**本次未取得任何可引用证据，标为「未查证」** | — | 未查证 |

**判断（推断）**：试穿层解决的是「**看起来怎么样**」，我们解决的是「**该不该穿、为什么**」。Doppl 的关停（Google 自己都放弃）是一个重要信号：**生成式视觉效果的爽感不能转化为持续使用**——看一次就够，没有第 30 天的使用理由。这反过来支持我们的判断：**「每天出门前要做一次决定」才是高频入口**（PRD J2）。**建议在 01-prd §2.2 的排除理由里补上这条市场证据。**

---

## 5. 第 4 层：B2B 基础设施

> 回答任务问的：「谁提供『风格理解 / 属性标注 / 穿搭规则』API」。

| 厂商 | 定位（已抓正文明细） | 是否提供「穿搭规则/风格判断」API | 对我们的价值 |
|---|---|---|---|
| **Syte** | 「The #1 Product Discovery Platform for Apparel Ecommerce」；**Visual Discovery / AI Tagging / Hyper-Personalization**，按行业（Fashion / Home Decor / Jewelry）分方案 | **属性标注（AI Tagging）+ 视觉检索**：可买。**穿搭规则/风格评分：未见产品** | **中**：单品属性标注可以替我们省 VLM 调优成本（若成本低于自建） |
| **Lily AI** | 「Agentic Product Intelligence for AI Commerce」；**Lily Max** 引擎做商品目录富化（catalog enrichment）+ 受控实验证明提效（Google Ads / Meta Ads / AI Discovery / Onsite） | **属性/营销语标注**：可买。**风格判断/穿搭规则：无** | **中**：属性标注与「为什么这件适合你」的文案素材相关 |
| **Heuritech** | 「Fashion trend forecasting & prediction with AI」；已被 **Luxurynsight 收购**；**Platform & API**「Quantify the adoption of prints, colors, fabrics and shapes **24 months ahead**」；数据源含 Instagram、TikTok、小红书 | **趋势量化 API**（印花/色彩/面料/廓形的采纳度），**预测未来流行**，不是判断个体搭配 | **低**：它是「什么会流行」，不是「你适合什么」。但可做二期「趋势校正」 |
| **Stylumia** | **已更名 / 转型为 Orbix Solutions**：「a suite of AI Agents for retail」，数字孪生市场（350K+ 品牌），四个决策：**what to launch / what to stock / what to back / what to price** | **需求/选品/定价智能**，不是穿搭判断 | 低 |
| **Vue.ai** | 官网已**全面转向「Enterprise AI Orchestration Platform」**，产品线是 Banking（Turing 核心银行、Debit Cards、Neobanking、Lending、Loan Origination、KYC/AML 等）+ Data Hub + Customer Hub。**已不是时尚公司** | **无**（时尚线事实上已退出） | **零**（注意：很多中文资料仍把它列为「时尚 AI 公司」，本调研发现其官网已转型，**这是一条需要注意的信息陈旧陷阱**） |
| **ViSenze** | 官网 `visenze.com` 与 `/solutions` 均 **HTTP 403**，正文未取得。**未查证** | **未查证** | **未查证** |

**判断**：
1. **本赛道不存在「穿搭规则引擎」的 B2B 供应商。** 属性标注（Syte / Lily AI）是商品级、面向电商目录的，不是搭配级、不是面向个体用户的。**这既是风险（要自建），也是护城河的机会（可外售）。**
2. **没有任何一家提供「可解释的风格评分」**——这正是 baseline §6 规则引擎与本报告 §9 的差异化共同指向的东西。
3. **买 API 的正确位置是上游**：单品属性标注 / 跨模态嵌入（FashionCLIP 系，见 §7）值得评估外采；**判断层必须自建**，因为它是产品本身。

---

## 6. 第 5 层：中国市场

| 平台 | AI 穿搭功能现状 | 证据等级 |
|---|---|---|
| **淘宝** | 有 **AI 试穿** 能力（鸿蒙版淘宝支持 AI 试穿、深色模式等功能）；有 AI 购物助手。**具体形态与入口未查证**（官方学习中心页面正文为 JS 渲染，仅取得标题「规则动态-淘宝官方学习中心」） | **仅标题级证据**：`https://daxue.taobao.com/information/detail.jhtml?id=2133` |
| **得物** | 中国区 App Store **5,410,068** 个评分（生活类头部）。是否内建 AI 穿搭评分/评审：**未查证**（本次未取得功能页证据） | 仅评分数据（iTunes API） |
| **小红书** | 中国区 App Store **21,162,453** 个评分。是**穿搭内容的实际入口**（用户获取灵感与求点评的主场景，`「求打分」「帮我看看」` 类笔记大量存在——这一点是行业共识，但本次**未取得可引用的量化证据，标为未查证**）。另有 AI Skill / 开发者生态的报道（**仅标题级证据**） | 仅评分数据；功能细节**未查证** |
| **唯品会** | 中国区 App Store **4,762,493** 个评分。AI 穿搭功能：**未查证** | 仅评分数据 |
| **蘑菇街** | 历史上主推「AI 设计穿搭风格」（界面新闻文章《用 AI 来设计穿搭风格 蘑菇街的这个选择会成功吗？》，已抓正文开头，但文章主体为 2018 年前后背景，**时效性存疑，不作为现状结论**） | 已抓正文（历史文章） |
| **独立 App 生态** | 中国区**「电子衣橱管理」有真实量级**（尽简衣橱 23,729 / 穿衣助手 6,463 / 今日装 4,756）；**「穿搭打分/点评」全部为 0–4 个评分**（见 §2.3、§2.4） | iTunes API，硬数据 |

**判断（推断）**：
- **中国市场的「穿搭决策」需求真实存在但被内容平台（小红书/得物）承接了**，而不是被工具 App 承接。用户的行为路径是「发帖求点评」，不是「打开打分 App」。
- **这既是威胁也是机会**：威胁是我们很难把「求点评」的用户从社区拉出来；机会是**社区里的点评质量低、同质化、且不针对个人**（PRD §1.1「同事朋友只会客气」+「小红书给的是流行什么，不是你该穿什么」两条判断**与市场观察一致**）。
- **产品含义**：我们的报告形态应该「**像一条可以被转发/求证的点评**」，而不是「一个 App 内的分数」。这是 §11 建议里的一条具体动作。

---

## 7. 开源 / 学术：穿搭兼容性打分（对自研比逆向更重要）

> **说明**：以下元数据（标题 / 年份 / venue / 被引数 / DOI）来自 OpenAlex API，准确。**「一句话贡献」基于标题 + venue + 领域常识，本次未逐篇读摘要**，如需精确引用请复核原文。

### 7.1 奠基与基准

| 工作 | 元数据 | 一句话贡献 |
|---|---|---|
| **Learning Type-Aware Embeddings for Fashion Compatibility**（Vasileva, Plummer, Dusad 等，ECCV 2018） | 236 次被引；DOI `10.1007/978-3-030-01270-0_24`；arXiv `1803.09196` | 提出 **type-aware embedding**（把「品类」作为条件编码进兼容性度量），并发布 **Polyvore Outfits 数据集**——此后几乎所有穿搭兼容性工作都以它为基准。**这是我们做「搭配塔」最该读的一篇。** |
| **OutfitTransformer: Outfit Representations for Fashion Recommendation**（Sarkar, Bodla, Vasileva 等，Amazon；CVPRW 2022 / WACV 2023） | 35 / 37 次被引；DOI `10.1109/cvprw56347.2022.00249` | 用 Transformer 把**整套 outfit 编码成单一表征**，统一支持兼容性预测 / 填空 / 单品检索三类任务。**这是 baseline §7「搭配塔」最直接的架构参考。** |
| **Fashion Outfit Complementary Item Retrieval**（Lin, Tran, Davis，CVPR 2020） | 95 次被引；DOI `10.1109/cvpr42600.2020.00337` | 给定部分单品检索能补全整套的互补单品，定义了「互补性」而非「相似性」的检索任务。 |
| **Learning Fashion Compatibility from In-the-wild Images**（Popli, Kumar, Jos，ICPR 2022） | 2 次被引 | 用野外（非商品棚拍）图片学兼容性，**更贴近用户手机拍照的真实分布**——对我们的输入质量问题是直接相关的。 |

### 7.2 跨模态嵌入（「风格语义」的现成 backbone）

| 工作 | 元数据 | 一句话贡献 |
|---|---|---|
| **Contrastive language and vision learning of general fashion concepts**（= **FashionCLIP**，Chia, Attanasio, Bianchi 等，Scientific Reports 2022） | 71 次被引；DOI `10.1038/s41598-022-23052-9` | 在时尚图文对上继续预训练 CLIP，产出 fashion-specific 跨模态嵌入。**baseline §7.2 说「用预训练跨模态嵌入」时，这是首选之一。** |
| **OpenFashionCLIP**（Cartella, Baldrati, Morelli 等，2023） | 7 次被引；DOI `10.1007/978-3-031-43148-7_21` | 用**开源**时尚数据复现 FashionCLIP，解决原版权重可得性问题（工程落地时更友好）。 |
| **FAME-ViL: Multi-Tasking Vision-Language Model for Heterogeneous Fashion Tasks**（Xiao, Zhu, Yu 等，CVPR 2023） | 55 次被引；DOI `10.1109/cvpr52729.2023.00262` | 一个模型统一处理检索 / 识别 / 改写等多种时尚任务，减少多模型拼接的工程复杂度。 |
| **SyncMask**（Song 等，CVPR 2024） | 13 次被引；DOI `10.1109/cvpr52733.2024.01323` | 面向时尚的同步注意力掩码视觉-语言预训练，提升细粒度属性对齐。 |

### 7.3 个性化（最接近「学习用户风格」的一支）

| 工作 | 元数据 | 一句话贡献 |
|---|---|---|
| **POG: Personalized Outfit Generation for Fashion Recommendation at Alibaba iFashion**（Chen, Huang, Xu 等，KDD 2019） | **224** 次被引；DOI `10.1145/3292500.3330652` | 阿里工业级：**个性化整套搭配生成**，从用户行为学偏好。规模最大的一手工业实践。 |
| **Hierarchical Fashion Graph Network for Personalized Outfit Recommendation**（Li, Wang, He 等，SIGIR 2020） | **137** 次被引；DOI `10.1145/3397271.3401080` | 用「用户-搭配-单品」层级图网络做个性化推荐。 |
| **Personalised Outfit Recommendation via History-aware Transformers**（Jung, Monteil, Schulz，**WSDM 2025**） | 6 次被引；DOI `10.1145/3701551.3703545` | **最接近「用用户历史穿搭学偏好」的近期工作**：history-aware Transformer 把用户过往搭配作为条件。但目标是**推荐**，不是**评审**。 |
| **Learning Personal Style from Few Examples**（Lin & Martelaro，**DIS 2021**） | 7 次被引；DOI `10.1145/3461778.3462115` | **方法论上最贴近我们 P0-1/P0-3 的一篇**：从**少量样例**学习个人风格（few-shot personal style learning）。这正是「上传 3–5 组参考穿搭」的理论依据。 |
| **Personalized Fashion Recommendation from Personal Social Media Data: An Item-to-Set Metric Learning Approach**（Zheng 等，IEEE BigData 2021） | 28 次被引；DOI `10.1109/bigdata52589.2021.9671563` | 从个人社交媒体数据（而非购买记录）学偏好——**与 Meta Style Sketch 的思路同源**。 |
| **Diffusion Models for Generative Outfit Recommendation**（Xu, Wang, Feng，SIGIR 2024） | 48 次被引；DOI `10.1145/3626772.3657719` | 用扩散模型做生成式搭配推荐，是「生成」路线的最新代表。 |
| **A Personalized Wardrobe Management Application with Attention-Based Outfit Recommendation**（Le, Huu, Tran，Procedia Computer Science **2026**） | 0 次被引 | 与我们形态最像的一篇**应用**论文（衣橱管理 + 注意力推荐），可作为竞品级参考。 |

### 7.4 学术层的结论

> **整个学术文献里，「穿搭」= 「兼容性预测 / 推荐 / 生成」三件事。「评审并给出可解释分维度评分」这个任务，本次检索没有找到对应的工作。**
>
> 具体表现：
> - 所有兼容性工作的输出是 **一个标量分数**（compatible / incompatible）或 **一个排序**，**没有分维度**，也**没有解释**。
> - 所有个性化工作的输出是 **top-K 推荐列表或生成的搭配**，**没有一个把「用户画像 vs 这套搭配」的差异显式输出成报告**。
> - 「解释性」在时尚推荐里的研究（如 `ConstitutionMaker` 之类交互式 LLM 批评）**不是穿搭领域的**——本次检索到的「explainable fashion / outfit critique」查询结果全部是跨领域误召（RAG survey、LLM 宪法批评等）。
>
> **对我们的直接价值**：
> 1. **baseline §7 的双塔设计有扎实的文献支撑**（type-aware embedding + outfit Transformer），不需要担心架构走偏。**但要注意**：文献里的双塔是为「推荐」服务的（query=用户，target=商品），我们是为「度量个人风格空间」服务的（baseline §7.3 已经指出了这一点，**文献不能直接照搬**）。
> 2. **「可解释的分维度评分」不是文献能给的，是规则引擎能给的**——这反向验证了 baseline §6 把规则引擎当护城河的判断。
> 3. **`Learning Personal Style from Few Examples`（DIS 2021）值得精读**：它回答的正是「3–5 组够不够」这个 PRD H1 的核心不确定性问题。
> 4. **Polyvore Outfits 数据集可作为我们「风格锚点库」的冷启动来源**（baseline §7.3 用途 1），但需注意其数据来自时尚电商棚拍，**与用户手机拍照的域差距是主要风险**（见 `Learning Fashion Compatibility from In-the-wild Images`）。

---

## 8. 赛道公认的产品空白 / 未解决痛点

> 以下 5 条由「竞品普遍缺什么」+「学术没做什么」+「用户量分布」三方交叉得出。每条都标了推断依据。

| # | 空白 / 痛点 | 依据 | 是否是我们的机会 |
|---|---|---|---|
| **B1** | **「打分」不解释扣分，或解释不可执行** | 40 个打分类 App 里，绝大多数只给「分数 + 泛化建议」；App Store 用户评论（穿搭日记唯一一条）夸的正是「给出的**雷点总结**很实用，看完能立马get到穿搭不合适的地方」——**用户自发把「可执行」当成核心价值**，说明这是稀缺品 | ✅ **这是我们的主战场（P2 每分必可溯）** |
| **B2** | **「个人化」普遍退化为「色彩季型」或「体型」** | Style DNA / Endam / Cladwell / 色彩日记 全都是自拍→色季→配色。**没有一家做「你的穿搭习惯」** | ✅ **这是我们的主战场（P3 个人化优先）** |
| **B3** | **「衣橱管理」有量，「穿搭判断」没量** | §2.4 的中国区数据：尽简衣橱 23,729 vs 穿搭日记 2 | ⚠️ **是机会但也是警告**：MVP 若不把「画像」做成可感知的资产，用户不会为「打分」来 |
| **B4** | **低质量输入 / 识别失败时的降级体验差** | 穿搭日记 1.1.0 更新日志：「照片里没有可识别的穿搭时会明确提示，不再返回 0 分结果」——说明**曾经返回过 0 分并被投诉** | ✅ 我们的 US-8 已覆盖；**但要注意这需要工程实现，不是文案** |
| **B5** | **建议条数越多越无用** | 竞品普遍给 3 条建议（穿搭日记会员 3 条、Style Muse 3 条）；PRD §8.3 反指标「平均建议数 ≤ 3」「置顶只能 1 条」与市场主流一致，但**没有一家敢只给 1 条** | ✅ **只给 1 条置顶建议**是一个可被感知的差异化（且已有 PRD 依据） |

**另外两条「看起来是空白但其实是坑」的（建议不要做）**：

| # | 陷阱 | 为什么是坑 |
|---|---|---|
| T1 | **社交 / 求点评社区** | 小红书 2,116 万评分、得物 541 万评分已经在做，且是免费内容供给。我们从零做社区无法冷启动（PRD §2.2 已排除，本次调研**支持该决定**） |
| T2 | **AI 试穿 / 上身效果图** | Google Doppl **已关停**；ASOS / Amazon / Zalando 在做但都是电商附属功能。生成效果的爽感不能转化为留存（见 §4 判断） |

---

## 9. 逆向候选筛选（第 2 层里哪 2–3 个最可能含可逆向逻辑）

> 判定依据（任务指定）：是否纯 Web / Electron / React SPA、是否有公开 APK、逻辑是否在客户端、是否有 prompt / 规则清单痕迹。

### 排名与结论

#### 🥇 第 1 名：Cladwell —— `app.cladwell.com`（**强烈推荐**）

| 维度 | 事实 |
|---|---|
| 是否纯 Web / SPA | **是。已实测 HTTP 200**，HTML 中是标准 **Next.js** 结构：`/_next/static/chunks/framework-*.js`、`pages/_app`、`pages/index`、`_buildManifest.js`、`__NEXT_DATA__` |
| 是否需 APK / IPA | **不需要**。全部逻辑以 JS chunk 形式可经 HTTPS 直接取得 |
| 逻辑是否在客户端 | **大概率是**（Next.js 客户端应用 + 明确的 `/api` 型后端边界可枚举）；至少**产品结构、字段设计、流程状态机、copy 文案**都在 bundle 里 |
| 有无 prompt / 规则痕迹 | **高度可能**：它有「Your colors（照片测色）」「Style Direction（问卷→风格方向）」「wardrobe template」「See it on me」，这些都需要**客户端的问卷定义、色彩判定阈值、模板规则**。逆向这些**比逆向一句 prompt 值钱得多** |
| 实际入口 | 登录/注册页已抓正文：「Let's Create Your Account … **Set Up Your Profile** — Name / **Your Wardrobe Type**（Men / Women / Both）… Discover a **wardrobe template** that fits your style. Customize and swap out items…**Continue on the Web**」 |

**为什么它是最优目标**：
1. **零取证成本**：不涉及 APK 拉取、不涉及签名、不涉及移动端加密。
2. **它的产品结构与我们的 P0-3（画像）+ P0-4（召回）+ D2（风格一致性）高度同构**——是「画像 → 搭配」这条链的成熟商业实现。
3. 它已经跑出 1M+ 下载 / 1,037 评分 / $9.99·月，**说明这套结构是可商业化验证的**，值得学它的**字段与流程设计**。

**风险**：
- ⚠️ **法律风险中高**：这是有明确商业主体的在营产品（Cladwell, Inc），且**有付费墙**。逆向其 Web bundle 的**产品结构**属常见竞品分析，但如果**复制其文案/模板/规则内容**则有版权与不正当竞争风险。**建议只提取结构与概念，不复用文案。**
- ⚠️ 技术风险低：Next.js 若开启了代码分割 + 压缩，需要 `analyze_javascript_application` 做静态重建（REA 支持 `app-pack` / `directory` / `javascript-bundle`）。
- ⚠️ 后端 API 不一定会暴露业务规则（色彩判定阈值可能在服务端）——**这是最大不确定性**。

---

#### 🥈 第 2 名：Style DNA ——（iOS + Android，1M+ 下载）

| 维度 | 事实 |
|---|---|
| 客户端可获取性 | **高**：Google Play **1M+ 下载 / 18.1K reviews**，`com.` 包名可在 Play 页面取得（本次未记录精确包名，需下一步补）；有 Android 版即**理论上可拉 APK** |
| 逻辑是否在客户端 | **部分**：12 色季 / 8 体型 / 6 风格类型（含 Kibbe）是**确定性的分类学 + 判定规则**，这类「知识库 + 映射表」**通常打包在客户端**（因为它要离线展示「你的色彩指南」长文） |
| 有无 prompt / 规则痕迹 | **高度可能**：`Item match check`（拍店里单品判断是否适合你的色季/体型）需要本地的判定逻辑或明确的规则调用。**这是我们最想要的「风格知识 taxonomy」** |
| 与我们的价值 | **极高**：那套 taxonomy 是我们规则引擎最好的外部知识源（baseline §6.4 的 STYLE 分类、DETAIL 材质/印花规则都能量化落地） |

**⚠ 本次实际取证限制**：`apkpure.com` / `apkcombo.com` / `apk.support` 全部 **403 或 TLS 中断**，**本次未取得任何 APK**。因此本条结论停留在「**技术上可取得**」而非「已取得」。

**风险**：
- ⚠️ **法律风险高**：Style DNA 是明确宣称「built on the expertise of world-renowned image consultants」的商业产品，其 taxonomy 可能是**授权内容**（形象顾问体系的 IP）。**直接复制其分类与判定阈值法律风险显著高于 Cladwell**。**建议只把它当作「知识地图」来对照自建，不做内容复制。**
- ⚠️ 技术风险中：Android APK 若做了混淆/加固，静态分析成本上升；且核心判定可能走服务端。

---

#### 🥉 第 3 名（有条件推荐）：小衣橱 - 每日穿搭点评 —— **仅为「多造型师人格」这一设计**

| 维度 | 事实 |
|---|---|
| 客户端可获取性 | **低**：仅 iOS（App Store id6773195549），需 IPA |
| 逻辑是否在客户端 | **可能**：「12 位有名字、有风格、有脾气的造型师」→ 大概率是 **12 段 persona system prompt**。若在客户端，逆向价值高（这是「多人格点评」的产品化范本） |
| 实际证据 | 描述原文：12 位 AI 造型师各有立场；「这件灰西装的肩线垮了 / 颜色对了，但比例没到 / 这双鞋下次试试换乐福」；算力制付费（新用户送 300 算力） |

**为什么排第 3 且有条件**：它的**商业价值低**（3 个评分，几乎无用户），但**产品设计质量高**——它是本次调研里唯一把「点评要具体到单品和动作」写进产品语言的。**如果我们的目标是学「怎么把扣分说成人话」，它比 Style DNA 更值得看。** 但取证成本（iOS IPA）明显高于 Cladwell，因此**建议先做 Cladwell，若有余力再做它**。

---

### 明确**不建议**逆向的（并说明理由，避免团队走错方向）

| 产品 | 为什么不值得 |
|---|---|
| **穿搭日记** | ① 评分维度已从商店页**完整取得**（5 维 + 权重），无信息缺口；② iOS only，取证成本高；③ 只有 2 个评分，**不是成功样本**，其 prompt 参考价值低 |
| **Endam** | ① 维度已从 App Store + Google Play + 官网**三处交叉取得**（Overall/Color/Fit/Occasion/Accessories）；② 唯一可能有价值的是**四段 AI 人格的 prompt**，但这类 prompt 我们自己写一天就有；③ 主体是**爱沙尼亚 App 工厂**（同期 5+ 款无关 App），说明它是模板化产品，**不代表最佳实践** |
| **Outfitly** | 100+ 下载 / 6 评论，**失败样本**；维度只有 3 个（fit/colors/occasion），信息价值低于取证成本 |
| **Meta Style Sketch** | ① 区域限量（仅印尼/新加坡/泰国），**取证路径受限**；② 它是 Meta 的产品，**法律风险最高**；③ 它的能力是「推荐 + 试穿」，**恰好是我们要避开的方向**；④ 它的价值在于「战略威胁监测」而非「逆向学习」——**建议改为产品动态监测，不逆向** |

---

## 10. 战略风险：两条必须盯住的外部信号

| # | 信号 | 为什么危险 | 建议动作 |
|---|---|---|---|
| **R-EXT-1** | **Meta Style Sketch 已经把「Instagram 历史 → 衣橱 → 按偏好推荐」跑通了** | 它拥有我们永远拿不到的数据源（用户数年的穿搭发帖史）。**如果 Meta 在 v2 加上「评分/点评」，我们的 (a) 学习画像 这一环会被彻底碾压**，剩下的只有 (c) 可解释 —— 而可解释是可以用 LLM 快速补的 | ① 把「**可解释 + 可反驳 + 可审计**」做到 Meta 不会做的程度（企业级：规则版本号、扣分溯源、可导出）。② Meta 不会做「让你上传 3–5 组穿搭」这种高摩擦的 C 端产品——**低摩擦是它的基因，高摩擦但高价值是我们的空间**。③ 持续监测其区域扩张 |
| **R-EXT-2** | **中国市场「衣橱管理」有量（尽简衣橱 23,729）、「穿搭打分」无量（穿搭日记 2）** | 如果我们的 MVP 只给「打分」，很可能重复那 40 个 0 评分 App 的结局 | ① MVP 必须让「画像」本身成为**可感知、可分享、可回看的资产**（对应 J4），而不只是打分前的冷启动步骤。② 报告形态要「像一条能被转发的点评」（承接小红书/得物的用户行为） |

---

## 11. 对我们的建议

### 11.1 MVP 形态：做什么 / 不做什么

| 建议 | 理由（引用本文） |
|---|---|
| ✅ **做「评审」，不做「生成」** | 生成路线（Cladwell / Alta / Meta / Dressly 32,914 评分）已被大厂与成熟团队占满；评审路线（§2.5）**未发现做成的** |
| ✅ **把「画像」做成可见资产，而不是冷启动表单** | §2.4：中国用户为「管好我的衣服」付费（23,729），不为「被打分」付费（2）。画像页应直接回答 J4「我的风格到底是什么」，用 top-3 风格 + 置信度 + **可回溯到具体单品**（US-2） |
| ✅ **只给 1 条置顶建议** | §8 B5：竞品都是 3 条且无人敢只给 1 条。PRD §5.4 已定「置顶只能 1 条」，市场验证支持这个决定 |
| ✅ **报告要像「一条可以被转发/求证的点评」** | §6：中国用户的真实行为是发帖求点评。报告应有强传播性的卡片形态（借鉴 Outfitly 的 score card、穿搭日记的风格报告） |
| ✅ **低质量输入必须显式降级** | §8 B4：穿搭日记是**被投诉后**才修的。我们的 US-8 要一次做对 |
| ❌ **不做「通用审美打分」** | §2.3：40 个 App 全是这个，全部 0–16 个评分 |
| ❌ **不做社交 / 社区 / 试穿** | §8 T1/T2、§4 |

### 11.2 值得逆向对标

| 优先级 | 目标 | 逆向什么 | 不逆向什么 |
|---|---|---|---|
| **P0** | **Cladwell `app.cladwell.com`**（Next.js，无需 APK） | **画像字段设计、问卷 → Style Direction 的映射、wardrobe template 规则、流程状态机、前端信息层级** | 文案、模板内容原文 |
| P1 | **Style DNA**（Android APK） | **12 色季 / 8 体型 / 6 风格类型的 taxonomy 结构**（只取结构，自建内容） | 具体判定阈值与顾问知识文本（IP 风险） |
| P2 | **小衣橱**（iOS IPA，条件执行） | **12 人格的设计与「具体到单品」的点评句式** | 其付费/算力模型（用户量太小，未验证） |

### 11.3 应该直接买 API / 用开源

| 环节 | 建议 | 理由 |
|---|---|---|
| **单品属性标注** | 先评估 **Syte / Lily AI** 的 API 报价，与自建 VLM 做成本对比 | §5：商品级属性标注已成熟可买，属「不该自建」的部分 |
| **跨模态嵌入** | 直接用 **FashionCLIP / OpenFashionCLIP**（开源可商用性需逐个核 license） | §7.2：Scientific Reports 2022 / 2023，工程成熟 |
| **搭配塔架构** | 参考 **type-aware embedding（ECCV 2018）** + **OutfitTransformer**（WACV 2023） | §7.1：文献已充分验证，不自创 |
| **「少量样例学风格」方法论** | 精读 **Learning Personal Style from Few Examples（DIS 2021）** | §7.3：直接回答 PRD H1「3–5 组够不够」 |
| **风格锚点库冷启动** | 用 **Polyvore Outfits 数据集** 起步 | §7.1：现成基准；**注意与手机拍照的域差距** |
| **规则引擎 / 可解释评分** | **必须自建**。市场无供应商（§5），文献无对应工作（§7.4） | 这正是差异化本身 |

### 11.4 对 01-prd 的三条补充建议（供 Lead 决策，本文不直接改 PRD）

1. **§1.2「为什么现在做」第 2 条可以加一个更硬的论据**：「AI 穿搭打分类 App 在美国区与中国区两个商店共约 40 个样本中，绝大多数评分人数为 0–16，无一跑出」——这比「已经饱和」这句定性描述更有说服力。
2. **§2.1 H1（画像完成率 ≥50%）的风险等级应从「中」上调**：竞品**集体绕开上传画像这一步**（§2.5 表格 (a) 列），说明这不仅是「流失点」，而是**这个品类从未被验证过的核心假设**。建议把「先报告后画像」的 A/B（§5.3 / Q1）提前到 M1 就做。
3. **§2.2 排除「AI 试穿」的理由可以补一条市场证据**：Google Doppl 已关停（Google 官方帮助页标题「Download items in Doppl before app shuts down」）——大厂自己都没跑通。

---

## 12. 证据台账（可复核）

### 12.1 已抓正文的来源（可直接引用原句）

| # | URL | 用于 |
|---|---|---|
| 1 | https://apps.apple.com/cn/app/id6761793902 | 穿搭日记：5 维权重原文、定价、更新日志、评分 2 个 |
| 2 | https://www.erzuo.com/ | 二作工作室背景、确认无 Web 应用 |
| 3 | https://apps.apple.com/cn/app/id6755446008 | Endam：描述、13 语言、4 档定价、0 评分、含广告 |
| 4 | https://endamai.com/ | Endam：官网截图数值（73/68/74/80）、4 人格、色彩季型、Play/App Store 链接 |
| 5 | https://play.google.com/store/apps/details?id=com.voventech.endam | Endam：Play 描述（color/fit/occasion/accessories 逐条） |
| 6 | https://play.google.com/store/apps/details?id=com.outfitai.outfitly | Outfitly：fit/colors/occasion、6 评论、100+ 下载 |
| 7 | https://apps.apple.com/us/app/id1358319821 | Style DNA：12 色季 / 8 体型 / 6 风格类型原文、7.5K 评分 |
| 8 | https://styledna.ai/ | Style DNA 官网（确认无 Web 应用，CTA=Join now） |
| 9 | https://www.digitaltoday.co.kr/en/view/121896/meta-launches-style-sketch-ai-fashion-app | Meta Style Sketch 机制全文 |
| 10 | https://www.antaranews.com/berita/5821119/meta-luncurkan-aplikasi-fesyen-berbasis-ai-style-sketch | Style Sketch 第二来源（交叉验证） |
| 11 | https://cladwell.com/ | Cladwell：$9.99/月、$79.99/年、Web 平台、Style Direction |
| 12 | https://app.cladwell.com/ | Cladwell Web 应用实测 200 + Next.js 结构 + 注册流程 |
| 13 | https://whering.co.uk/ | Whering 首页（社交衣橱、Chrome 扩展） |
| 14 | https://www.acloset.app/ | Acloset 首页（8M 用户、Editor's Pick、Chrome 扩展） |
| 15 | https://stylebookapp.com/ | Stylebook 首页（2009 年起、90+ 功能） |
| 16 | https://www.syte.ai/ | Syte 定位（AI Tagging / Visual Discovery） |
| 17 | https://www.lily.ai/ | Lily AI 定位（Lily Max、catalog enrichment） |
| 18 | https://www.heuritech.com/ | Heuritech 定位（趋势预测 API、被 Luxurynsight 收购） |
| 19 | https://vue.ai/ | Vue.ai 已转型为银行/企业 AI（关键更正） |
| 20 | https://www.stylumia.com/ | Stylumia → Orbix Solutions（关键更正） |
| 21 | https://www.jiemian.com/article/1590944.html | 蘑菇街 AI 穿搭（历史文章，时效存疑） |

**API 来源（结构化数据）**

| API | 用途 |
|---|---|
| `https://itunes.apple.com/search` / `/lookup` | 全部 App 的名称、开发者、评分人数、平均分、价格、完整 description（§2.3 / §2.4 / §3 的全部数字来源） |
| `https://api.openalex.org/works` | §7 全部论文的标题 / 年份 / venue / 被引 / DOI |

### 12.2 未取得 / 仅标题级证据 / 未查证的清单

| 项目 | 状态 |
|---|---|
| Outfitly 的 iOS 版是否存在、内购档位价格 | **未查证** |
| Style DNA 的订阅档位价格 | **未查证**（描述被截断） |
| Style DNA / Endam 的精确 Android 包名 | **未查证**（Endam 已知 `com.voventech.endam`；Style DNA 未记录） |
| 任何产品的公开 APK 直链 | **未取得**（APKPure/APKCombo/apk.support 均 403/连接失败） |
| ViSenze 的产品能力 | **未查证**（官网 403） |
| 淘宝 AI 试穿的具体形态与入口 | **仅标题级证据**（官方页 JS 渲染） |
| 得物 / 小红书 / 唯品会的 AI 穿搭功能现状 | **未查证**（仅有 App Store 评分数据） |
| Pinterest 的 AI 时尚能力 | **未查证** |
| ASOS See My Fit / Amazon Virtual Try-On 的具体能力 | **仅标题级证据** |
| Zalando 的 fit 技术细节 | **未查证**（正文被 cookie 导航占满） |
| Google Doppl 关停的**原文日期与公告细节** | **仅标题级证据**（官方页正文未抓取到，`labs.google/doppl/` 已 404） |
| Meta Style Sketch 的定价 | **未查证** |
| §7 各论文的「一句话贡献」 | 基于标题 + venue + 领域常识（**未逐篇读摘要**） |
| 第 2 层所有产品的**真实留存 / 付费转化** | **未查证**（仅评分人数可观测，属代理指标） |

---

## 13. 变更记录

| 版本 | 日期 | 变更 | 作者 |
|---|---|---|---|
| v0.1 | 2026-10-31 | 初稿：5 层全景 + 第 2 层逐产品维度抄录 + 品类饱和度量化 + 「画像驱动评审」空白结论 + 逆向候选筛选 + 学术综述 | market-analyst |
