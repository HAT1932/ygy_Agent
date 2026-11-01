"""契约层 —— 全系统唯一的数据形状定义（FROZEN）。

来源：docs/00-baseline.md（v0.2）§5/§6/§8 与 docs/04-api-and-data-contract.md。
改动本文件必须同步 docs/04 并 bump CONTRACT_VERSION；其他模块不得自定义重复结构。

设计原则（对应 docs/03 §1.3 不变量）：
  INV-1 确定性：同一 (outfit, profile, ruleset) → 同一分、同一序
  INV-2 可加性  ：total_score = base_score + Σ delta_applied
  INV-3 可追溯  ：每条扣分必有 rule_id + evidence + suggestion
  INV-4 预算守恒：base_d + boost_budget_d == max_d
  INV-5 模板自足：explain/suggestion 模板脱离 LLM 也能直接呈现
"""
from __future__ import annotations

import colorsys
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

CONTRACT_VERSION = "1.3.0"
RULESET_VERSION = "rs-2026.10.2"

# --------------------------------------------------------------------------
# 基础枚举
# --------------------------------------------------------------------------


class DimensionCode(str, Enum):
    D1 = "D1"  # 色彩和谐度
    D2 = "D2"  # 风格一致性
    D3 = "D3"  # 身形与比例
    D4 = "D4"  # 场合适配
    D5 = "D5"  # 细节完成度


class Severity(str, Enum):
    hard = "hard"
    soft = "soft"
    boost = "boost"


class EffectType(str, Enum):
    veto = "veto"
    penalty = "penalty"
    bonus = "bonus"


class Category(str, Enum):
    COLOR = "COLOR"
    STYLE = "STYLE"
    PROPORTION = "PROPORTION"
    OCCASION = "OCCASION"
    DETAIL = "DETAIL"


class Slot(str, Enum):
    upper = "upper"
    lower = "lower"
    outer = "outer"
    shoes = "shoes"
    accessory = "accessory"
    dress = "dress"


class Verdict(str, Enum):
    strongly_recommended = "strongly_recommended"
    suitable = "suitable"
    neutral = "neutral"
    unsuitable = "unsuitable"


class RenderMode(str, Enum):
    template_only = "template_only"   # LLM 不可用时的兜底（MVP 默认走这条）
    hybrid = "hybrid"
    llm_polished = "llm_polished"


class TaskStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


# --------------------------------------------------------------------------
# 维度常量（INV-4：base_d + boost_budget_d == max_d）
# 数值来源：docs/03-rule-engine-and-scoring.md §2.2 / §3，用「厘点」存储（1 分 = 100 厘点）
# --------------------------------------------------------------------------


class DimensionSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: DimensionCode
    name: str
    max_centi: int
    weight: float
    rho: float
    base_centi: int
    boost_budget_centi: int


DIMENSIONS: tuple[DimensionSpec, ...] = (
    DimensionSpec(code=DimensionCode.D1, name="色彩和谐度", max_centi=2000, weight=0.20, rho=0.78,
                  base_centi=1560, boost_budget_centi=440),
    DimensionSpec(code=DimensionCode.D2, name="风格一致性", max_centi=2500, weight=0.25, rho=0.72,
                  base_centi=1800, boost_budget_centi=700),
    DimensionSpec(code=DimensionCode.D3, name="身形与比例", max_centi=2000, weight=0.20, rho=0.66,
                  base_centi=1320, boost_budget_centi=680),
    DimensionSpec(code=DimensionCode.D4, name="场合适配", max_centi=2000, weight=0.20, rho=0.68,
                  base_centi=1360, boost_budget_centi=640),
    DimensionSpec(code=DimensionCode.D5, name="细节完成度", max_centi=1500, weight=0.15, rho=0.64,
                  base_centi=960, boost_budget_centi=540),
)

DIM_BY_CODE: dict[DimensionCode, DimensionSpec] = {d.code: d for d in DIMENSIONS}

BASE_SCORE_CENTI: int = sum(d.base_centi for d in DIMENSIONS)          # 7000 == 70.00
TOTAL_MAX_CENTI: int = sum(d.max_centi for d in DIMENSIONS)            # 10000
TOTAL_BOOST_BUDGET_CENTI: int = sum(d.boost_budget_centi for d in DIMENSIONS)  # 3000

# 判定阈值（作用于「厘点」，避免浮点边界问题）：
#   >= 8000 强烈推荐 | 6500-7999 适合 | 5000-6499 一般 | < 5000 不适合
VERDICT_THRESHOLDS = (
    (8000, Verdict.strongly_recommended),
    (6500, Verdict.suitable),
    (5000, Verdict.neutral),
)
VERDICT_FLOOR = Verdict.unsuitable

assert BASE_SCORE_CENTI == 7000, "base_score 必须为 70.00（docs/03 §3）"
assert TOTAL_BOOST_BUDGET_CENTI == 3000


def verdict_of(total_centi: int, vetoed: bool) -> Verdict:
    if vetoed:
        return Verdict.unsuitable
    for threshold, verdict in VERDICT_THRESHOLDS:
        if total_centi >= threshold:
            return verdict
    return VERDICT_FLOOR


# --------------------------------------------------------------------------
# 颜色与单品
# --------------------------------------------------------------------------

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


class Color(BaseModel):
    """单个颜色。hue/saturation/lightness 由 hex 确定性推导，不需要外部模型。"""

    hex: str
    name: str = ""
    family: str = ""          # 受控词表见 docs/03 §7.5：neutral/warm/cool/vivid/earth/metal/...
    confidence: float = 1.0
    hue: float = Field(default=0.0, ge=0.0, le=360.0)
    saturation: float = Field(default=0.0, ge=0.0, le=1.0)
    lightness: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def _derive_hsv(cls, data: Any) -> Any:
        if isinstance(data, dict) and "hex" in data:
            m = _HEX_RE.match(str(data["hex"]))
            if m:
                v = m.group(1)
                r, g, b = (int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
                h, l, s = colorsys.rgb_to_hls(r, g, b)
                data = {**data, "hex": f"#{v.upper()}", "hue": h * 360.0,
                        "lightness": l, "saturation": s}
        return data


class Garment(BaseModel):
    garment_id: str
    slot: Slot
    category: str = ""
    colors: list[Color] = Field(default_factory=list)
    material: str | None = None
    fit: str | None = None            # 修身 / 直筒 / 宽松 / 落肩 …
    silhouette: str | None = None
    length: str | None = None         # 短款 / 常规 / 长款
    formality: int | None = Field(default=None, ge=1, le=5)
    style_tags: list[str] = Field(default_factory=list)
    pattern: str | None = None        # 纯色 / 条纹 / 格纹 …
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: str | None = None

    @property
    def primary_color(self) -> Color | None:
        return self.colors[0] if self.colors else None


class Outfit(BaseModel):
    outfit_id: str
    items: list[Garment] = Field(default_factory=list)
    occasion: str | None = None
    season: str | None = None
    image_ref: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    parse_warnings: list[str] = Field(default_factory=list)
    # 真视觉 embedding（FashionCLIP 512 维，L2 归一化）；为空则双塔回退哈希后端。
    # 这是过渡字段：有了它，160 维「伪视觉」哈希才能被真视觉语义替换（docs/10 F-10）。
    image_embedding: list[float] | None = None

    def by_slot(self, slot: Slot) -> list[Garment]:
        return [g for g in self.items if g.slot == slot]

    def first(self, slot: Slot) -> Garment | None:
        items = self.by_slot(slot)
        return items[0] if items else None

    @property
    def slots_present(self) -> set[Slot]:
        return {g.slot for g in self.items}


# --------------------------------------------------------------------------
# 风格画像
# --------------------------------------------------------------------------


class StyleTagScore(BaseModel):
    tag: str
    weight: float = Field(ge=0.0, le=1.0)


class StyleProfile(BaseModel):
    profile_id: str
    tags: list[StyleTagScore] = Field(default_factory=list)      # 已按权重降序
    banned_styles: list[str] = Field(default_factory=list)       # 用户明确不穿的风格
    explore_tags: list[str] = Field(default_factory=list)        # 用户想尝试的风格（F-08）
    preferred_formality: int | None = Field(default=None, ge=1, le=5)
    preference_vector: list[float] | None = None                  # 256 维
    n_ref_outfits: int = 0
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_outfit_ids: list[str] = Field(default_factory=list)
    tag_evidence: dict[str, list[str]] = Field(default_factory=dict)  # 标签 → 证据（可回溯，US-2）

    @property
    def top_tags(self) -> list[str]:
        return [t.tag for t in self.tags]


# --------------------------------------------------------------------------
# 规则与 DSL
# --------------------------------------------------------------------------

OPS = {
    "gt", "gte", "lt", "lte", "eq", "ne",
    "in", "not_in", "between",
    "contains_any", "contains_all", "contains_none",
    "abs_diff_gt", "abs_diff_lt",
    "exists", "is_true", "is_false",
}
COMPARATORS = {"gt", "gte", "lt", "lte"}


class Condition(BaseModel):
    """递归条件树。形态三选一：组合（all/any/not）或 叶子（field+op[+value/other_field]）。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    all: list["Condition"] | None = None
    any: list["Condition"] | None = None
    not_: "Condition | None" = Field(default=None, alias="not")

    field: str | None = None
    op: str | None = None
    value: Any = None
    other_field: str | None = None   # abs_diff_* 专用

    @model_validator(mode="after")
    def _check(self) -> "Condition":
        combinators = [c for c in (self.all is not None, self.any is not None, self.not_ is not None) if c]
        is_leaf = self.field is not None or self.op is not None
        if combinators and is_leaf:
            raise ValueError("条件不能同时是组合与叶子")
        if len(combinators) > 1:
            raise ValueError("all / any / not 只能出现一个")
        if not combinators:
            if self.field is None or self.op is None:
                raise ValueError("叶子条件必须同时提供 field 与 op")
            if self.op not in OPS:
                raise ValueError(f"未知 op: {self.op!r}")
            if self.op in {"abs_diff_gt", "abs_diff_lt"} and not self.other_field:
                raise ValueError(f"{self.op} 必须提供 other_field")
            if self.op not in {"exists", "is_true", "is_false",
                               "abs_diff_gt", "abs_diff_lt"} and self.value is None:
                raise ValueError(f"op={self.op} 必须提供 value")
        return self

    @property
    def kind(self) -> Literal["all", "any", "not", "leaf"]:
        if self.all is not None:
            return "all"
        if self.any is not None:
            return "any"
        if self.not_ is not None:
            return "not"
        return "leaf"


Condition.model_rebuild()


class ScoreImpact(BaseModel):
    dimension: DimensionCode
    delta: float            # 负=扣分，正=加分；单位为「分」，引擎转换为厘点


class RuleTestCases(BaseModel):
    positive: list[dict[str, Any]] = Field(default_factory=list)
    negative: list[dict[str, Any]] = Field(default_factory=list)


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    version: int = 1
    category: Category
    title: str
    severity: Severity
    scope: Literal["outfit", "garment", "cross_garment"] = "outfit"
    condition: Condition
    effect: EffectType
    score_impact: ScoreImpact
    explain_template: str
    suggestion_template: str | None = None
    reinforce_template: str | None = None
    evidence_fields: list[str] = Field(default_factory=list)
    source: str = "domain_expert"
    status: Literal["draft", "active", "deprecated"] = "active"
    suppresses: list[str] = Field(default_factory=list)
    mutex_group: str | None = None
    render_priority: int = 50
    test_cases: RuleTestCases = Field(default_factory=RuleTestCases)

    @model_validator(mode="after")
    def _consistency(self) -> "Rule":
        if self.severity == Severity.hard and self.effect != EffectType.veto:
            raise ValueError(f"{self.rule_id}: hard 规则必须 effect=veto")
        if self.severity != Severity.hard and self.effect == EffectType.veto:
            raise ValueError(f"{self.rule_id}: 只有 hard 规则可 veto")
        if self.severity == Severity.boost:
            if self.effect != EffectType.bonus:
                raise ValueError(f"{self.rule_id}: boost 规则必须 effect=bonus")
            if self.score_impact.delta <= 0:
                raise ValueError(f"{self.rule_id}: boost 的 delta 必须为正")
            if not self.reinforce_template:
                raise ValueError(f"{self.rule_id}: boost 规则必须提供 reinforce_template")
        else:
            if self.effect == EffectType.bonus:
                raise ValueError(f"{self.rule_id}: 非 boost 规则不得 effect=bonus")
            if self.score_impact.delta >= 0:
                raise ValueError(f"{self.rule_id}: 扣分规则 delta 必须为负")
            if not self.suggestion_template:
                raise ValueError(f"{self.rule_id}: 扣分规则必须提供 suggestion_template")
        if not self.explain_template.strip():
            raise ValueError(f"{self.rule_id}: explain_template 不能为空")
        return self


class RuleHit(BaseModel):
    rule_id: str
    title: str
    category: Category
    severity: Severity
    dimension: DimensionCode
    delta_schema: float                       # 规则声明的 delta
    delta_applied: float = 0.0                 # 预算截断后实际生效
    clipped: bool = False
    triggered_value: Any = None
    evidence: str = ""
    explanation: str = ""
    suggestion: str | None = None
    reinforce: str | None = None
    render_priority: int = 50


# --------------------------------------------------------------------------
# 特征（双塔 + 解析产物，供 DSL 的 derived.* 与 D2 使用）
# --------------------------------------------------------------------------


class Features(BaseModel):
    """上下文构建的特征集合。extra 允许扩展，便于规则作者加字段而不改契约。"""

    model_config = ConfigDict(extra="allow")

    style_cosine: float = Field(default=0.0, ge=-1.0, le=1.0)
    style_coverage: float = Field(default=1.0, ge=0.0, le=1.0)   # min(1, n_ref/3)
    color_hue_span_deg: float = 0.0
    color_max_saturation: float = 0.0
    color_distinct_families: int = 0
    formality_span: float = 0.0
    # 门控：每个维度的证据可用度 0..1；低则跳过该维度规则（保持 base_d）
    assessability: dict[str, float] = Field(default_factory=dict)

    def assess(self, dim: DimensionCode) -> float:
        return float(self.assessability.get(dim.value, 1.0))


# --------------------------------------------------------------------------
# 评分结果
# --------------------------------------------------------------------------


class DimensionScore(BaseModel):
    code: DimensionCode
    name: str
    score: float          # 0..max，展示用（round_half_up 到 0.1）
    max: float
    weight: float
    base: float           # base_d
    assessability: float
    penalty_used: float
    boost_used: float
    # 厘点整数（唯一真值，展示字段由它推导）
    score_centi: int = 0
    base_centi: int = 0
    max_centi: int = 0
    penalty_centi: int = 0
    boost_centi: int = 0
    gated: bool = False   # 因证据不足被门控跳过（该维度保持 base）


class ScoreLedgerEntry(BaseModel):
    seq: int
    rule_id: str
    dimension: DimensionCode
    delta_schema: float
    delta_applied: float
    clipped: bool = False
    reason: str = ""


class ScoringResult(BaseModel):
    total_centi: int
    base_centi: int
    vetoed: bool
    verdict: Verdict
    dimensions: list[DimensionScore]
    hits: list[RuleHit]
    ledger: list[ScoreLedgerEntry]


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


class StyleMatch(BaseModel):
    user_top_styles: list[str] = Field(default_factory=list)
    outfit_styles: list[str] = Field(default_factory=list)
    cosine: float = 0.0
    matched: list[str] = Field(default_factory=list)
    drifted: list[str] = Field(default_factory=list)


class Anchor(BaseModel):
    outfit_id: str
    similarity: float
    thumb_url: str | None = None


class RecallItem(BaseModel):
    """双塔召回结果：用户塔向量 × 搭配库向量 的 top-K。

    这是「双塔召回」在产品上的可见形态（验收标准第 4 条）：
      · 用户塔 = 画像向量（由参考穿搭聚合）
      · 搭配塔 = 候选搭配向量（来自独立搭配库，不是用户的输入）
      · cosine(user, item) 排序取 top-K
    """

    item_id: str
    title: str
    similarity: float
    style_tags: list[str] = Field(default_factory=list)
    occasion: str | None = None
    item_summary: str = ""      # "藏青针织衫 + 燕麦直筒裤 + 黑乐福鞋"


class Suggestion(BaseModel):
    priority: int = 1
    target: str
    action: str
    reason_rule_ids: list[str] = Field(default_factory=list)
    expected_gain: float = 0.0


class Diagnostic(BaseModel):
    """一条**人可读**的解析诊断（docs/10 F-16）。

    与 ``parse_warnings`` 的区别：后者是给规则引擎吃的机器码，前者是给用户看的。
    同 code 的多条原始告警合并成一条，``refs`` 标明是**第几张**参考（1-based）。
    """

    code: str                                   # 归并键，如 IMAGE_TOKENS / SUBJECT:OCCLUDED
    severity: Literal["info", "warn", "error"] = "info"
    label: str = ""                             # 中文一句话
    detail: str = ""                            # 聚合后的具体值
    impact: str = ""                            # 对分数/结论的影响
    count: int = 1                              # 原始告警出现次数
    refs: list[int] = Field(default_factory=list)  # 归属的参考序号（1-based）


class ReportMeta(BaseModel):
    vlm_model: str = "mock"
    llm_model: str = "template"
    embedding_model: str = "hash-256"
    ruleset_version: str = RULESET_VERSION
    contract_version: str = CONTRACT_VERSION
    render_mode: RenderMode = RenderMode.template_only
    # 数据来源：vlm = 真实模型解析的真实图片；mock = 降级/离线，**结论不可用于评估**
    # 硬保险：防止"以为在测自己的照片，其实在跑假数据"（docs/10 F-13）。
    data_source: Literal["vlm", "mock"] = "mock"
    parse_warnings: list[str] = Field(default_factory=list)
    # 人可读诊断（docs/10 F-16）：分级 + 归属 + 影响。parse_warnings 保持原样不动。
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    evidence_summary: str = ""
    embedding_config: dict[str, Any] | None = None
    latency_ms: dict[str, int] = Field(default_factory=dict)


class Report(BaseModel):
    report_id: str
    outfit_id: str
    profile_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    verdict: Verdict
    vetoed: bool = False
    total_score: float
    total_centi: int = 0
    base_score: float = BASE_SCORE_CENTI / 100.0
    base_centi: int = BASE_SCORE_CENTI
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    dimension_scores: list[DimensionScore]
    rule_hits: list[RuleHit] = Field(default_factory=list)
    score_ledger: list[ScoreLedgerEntry] = Field(default_factory=list)

    style_match: StyleMatch = Field(default_factory=StyleMatch)
    anchors: list[Anchor] = Field(default_factory=list)
    recall: list[RecallItem] = Field(default_factory=list)      # 双塔召回结果（验收标准第 4 条）
    suggestions: list[Suggestion] = Field(default_factory=list)
    summary: str = ""
    meta: ReportMeta = Field(default_factory=ReportMeta)

    @model_validator(mode="after")
    def _invariants(self) -> "Report":
        # INV-2（厘点整数口径，无浮点误差）：total_centi = base_centi + Σ delta_applied_centi
        applied_centi = sum(int(round(h.delta_applied * 100)) for h in self.rule_hits)
        if self.total_centi == 0 and self.total_score:
            self.total_centi = int(round(self.total_score * 100))
        if self.total_centi != self.base_centi + applied_centi:
            raise ValueError(
                f"INV-2 违反: base({self.base_centi}) + Σdelta({applied_centi}) "
                f"= {self.base_centi + applied_centi} != total_centi({self.total_centi})")
        # 维度分之和 == 总分
        dim_sum = sum(d.score_centi for d in self.dimension_scores)
        if dim_sum != self.total_centi:
            raise ValueError(f"维度分之和 {dim_sum} != total_centi {self.total_centi}")
        # veto 必须判不适合
        if self.vetoed and self.verdict != Verdict.unsuitable:
            raise ValueError("vetoed 的 Report 必须 verdict=unsuitable")
        return self


# --------------------------------------------------------------------------
# API 请求 / 响应
# --------------------------------------------------------------------------


class CreateProfileRequest(BaseModel):
    image_refs: list[str] = Field(min_length=1, max_length=10)
    note: str | None = None
    # 用户主观偏好（可选）：让「个人画像」不只是聚合统计，还能表达主观意愿。
    # 不填也能跑；填了会激活 R-STYLE-001（hard 禁用风格）与 R-STYLE-009（探索加分）。
    banned_styles: list[str] = Field(default_factory=list, max_length=20)
    explore_tags: list[str] = Field(default_factory=list, max_length=20)
    persist: bool = True


class CreateProfileResponse(BaseModel):
    profile_id: str
    profile: StyleProfile
    outfits: list[Outfit] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    evidence_summary: str = ""


class ParseOutfitRequest(BaseModel):
    image_refs: list[str] = Field(min_length=1, max_length=5)
    occasion: str | None = None
    season: str | None = None


class ParseOutfitResponse(BaseModel):
    outfit: Outfit
    warnings: list[str] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    evidence_summary: str = ""


class CreateReportRequest(BaseModel):
    profile_id: str
    outfit_id: str | None = None
    image_refs: list[str] | None = Field(default=None, max_length=5)
    occasion: str | None = None
    season: str | None = None

    @model_validator(mode="after")
    def _need_outfit(self) -> "CreateReportRequest":
        if not self.outfit_id and not self.image_refs:
            raise ValueError("必须提供 outfit_id 或 image_refs 之一")
        return self


class TaskResponse(BaseModel):
    task_id: str
    status: TaskStatus


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    contract_version: str = CONTRACT_VERSION
    ruleset_version: str = RULESET_VERSION
    offline_mode: bool = True
    rule_count: int = 0
    library_count: int = 0        # 双塔召回的候选池大小
    providers: dict[str, str] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    code: str
    message: str
    retryable: bool = False
    detail: dict[str, Any] | None = None
