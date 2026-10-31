"""AI 穿搭顾问 Agent —— 参考实现包。

模块地图：
  contracts  冻结契约（唯一数据形状）
  utils      厘点/舍入/确定性哈希
  config     运行时配置（缺 key 自动离线）
  scoring    确定性评分（INV-1/2/4）
  rules      规则资产与 DSL 引擎（rules/ 目录是内容，src/rules/ 是代码）
  parsing    VLM 单品解析（mock / qwen）
  profiling  风格画像构建
  towers     双塔向量（256 维，不训练）
  synthesis  Report 文案合成（模板优先，LLM 可选）
  pipeline   端到端编排
  api        FastAPI 接口
"""

__version__ = "1.0.0"
