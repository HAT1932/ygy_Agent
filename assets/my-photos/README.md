# 把你的真人照片放这里

这个目录是给你自己测试用的，**不会被提交到 git**（已在 `.gitignore`）。

## 1. 放哪里

```
E:\Chuang_Yi_skill\assets\my-photos\
    ├─ ref-1.jpg      ← 你平时的穿搭（参考照，建议 2–3 张）
    ├─ ref-2.jpg
    ├─ ref-3.jpg
    └─ review.jpg     ← 想被评审的那一套（1 张）
```

放这里只是图省事。**系统接受任意本地路径**（`D:\照片\a.jpg`、`C:\Users\你\Desktop\b.jpg` 都行），
相对路径按启动目录算。

## 2. 照片要求（直接影响准确率）

| 项 | 建议 | 原因 |
|---|---|---|
| 格式 | **JPG / PNG / WebP** | **iPhone 的 HEIC 默认读不了**（Pillow 不支持）→ 会被判 `IMAGE_UNDECODABLE` 并降级 mock。用"另存为 JPG"或 `pip install pillow-heif` |
| 人物 | **单人、占画面 40% 以上、全身或 3/4 身** | 多人/半身/远景会触发质量门降权 |
| 背景 | 随意（实测杂乱背景不影响） | VLM 能自己忽略背景 |
| 光线 | 均匀、别逆光 | 颜色识别依赖明度，逆光会把深色判成黑色 |
| 分辨率 | 原始照片即可 | 系统会自动缩到长边 1024 再发送 |
| 张数 | 参考照 2–3 张，别超过 5 张 | 每张解析一次，费额度 |

## 3. 先预检，再花钱

```powershell
cd E:\Chuang_Yi_skill
$env:OFFLINE_MODE='false'

python scripts/check_photo.py assets\my-photos\
# 加 --parse 会真调一次 API，把识别出的单品也打出来：
python scripts/check_photo.py assets\my-photos\ref-1.jpg --parse
```

预检会告诉你：能不能解码、缩放后多少 image token、检测到几个人、主体占比、
输入质量分、以及检测到的遮挡物。**token 数直接决定费用**（原始手机照片约 11,970 tokens，
缩放后约 768 tokens —— 差 15 倍，这就是必须预检的原因）。

## 4. 三种测法

### A. 网页（最直观）

```powershell
cd E:\Chuang_Yi_skill
python run.py --online        # 打开 http://127.0.0.1:8000/
```

1. 步骤 1：把 2–3 个参考照路径粘进「参考穿搭标识」，每行一个
   ```
   E:\Chuang_Yi_skill\assets\my-photos\ref-1.jpg
   E:\Chuang_Yi_skill\assets\my-photos\ref-2.jpg
   ```
   可选填「绝对不穿的风格」「想尝试的风格」——**强烈建议填**，实测能把画像区分度从 5.0 分提到 8.0 分。
2. 步骤 2：粘贴 `review.jpg` 的路径，选场合，点「生成评审」。
3. 看报告：总分 / 四档结论 / 五维条形 / 规则命中与证据 / 建议 / 双塔召回。

### B. 命令行（看全部字段）

```powershell
# 建画像
curl.exe -s -X POST http://127.0.0.1:8000/v1/profiles `
  -H "Content-Type: application/json" `
  -d '{\"image_refs\":[\"E:/Chuang_Yi_skill/assets/my-photos/ref-1.jpg\"],\"banned_styles\":[\"运动休闲\"]}'

# 评审（用上一步返回的 profile_id）
curl.exe -s -X POST http://127.0.0.1:8000/v1/reports `
  -H "Content-Type: application/json" `
  -d '{\"profile_id\":\"prf_xxx\",\"image_refs\":[\"E:/Chuang_Yi_skill/assets/my-photos/review.jpg\"],\"occasion\":\"commute\"}'
```

> JSON 里的反斜杠要写成 `\\` 或改用正斜杠 `/`，否则转义会报错。

### C. 不花额度（验证链路）

```powershell
python run.py --offline       # mock 解析 + 模板文案，零网络
```

## 5. 成本预期（参考价，按千 token 计费）

| 动作 | 图片 token | 大致费用 |
|---|---|---|
| 建画像（3 张参考） | 3 × 768 ≈ 2,300 | < 0.02 元 |
| 一次评审（1 张） | 768 | < 0.01 元 |
| 一次评审（全流程，含 DeepSeek 润色总评） | + 约 500 文本 token | 再加几厘 |

真正影响体验的是**延迟**：每张图 VLM 解析约 3–11 s，人形检测若走 VLM grounding 每张再加 3–5 s。

## 6. 想看到「个性化」真的生效

同一张 `review.jpg`，用两个不同画像各评一次：

- 画像 A：参考照是通勤/正装，且声明 **绝对不穿「街头潮流」**
- 画像 B：参考照是休闲/街头，不声明禁用

两次的分数和结论应该明显不同。如果几乎一样，说明该搭配没有触及画像的判别维度，
可以换一张差异更大的衣服再试。

## 7. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `IMAGE_UNDECODABLE` | HEIC 或文件损坏 | 转成 JPG |
| `NO_PERSON_DETECTED` | 照片里没拍到人 / 平铺摆拍 | 平铺图是合法输入，质量分 0.85，不扣重罚 |
| `INCOMPLETE_NO_SHOES_MAYBE_CROPPED` | 画面裁到腰腿，鞋没进画面 | 重拍全身照 |
| `MULTIPLE_PERSONS(n)` | 有多个人 | 裁成单人，或确认主体是画面最大的人 |
| `SUBJECT_QUALITY:0.6` | 质量门判定证据不足 | 五个维度的判断会整体降权（这是设计：宁可不说，也不瞎说） |
| 结论总是「一般」 | 分数被大量中小扣分拉平 | 看「全部规则命中」里哪几条扣得最多 |
