"""端到端编排 —— 把「解析 → 画像 → 双塔 → 规则 → 评分 → 合成 → Report」串成一条链。

这是 API 层唯一的依赖点（`get_service()`），也是唯一知道"顺序"的模块。
底层模块（parsing/profiling/towers/rules/scoring/synthesis）互不感知，便于单独测试。

对应 docs/00-baseline.md §4 主流程，并按 [B-12] 修正后的顺序：
  ⑥ 搭配结构化 → ⑥.5 特征计算与双塔召回 → ⑦ 规则执行 → ⑧-b style_match → ⑨ 合成
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np

from .config import get_settings
from .contracts import (
    Anchor,
    CreateProfileResponse,
    DimensionCode,
    Features,
    Outfit,
    ParseOutfitResponse,
    Report,
    ReportMeta,
    RenderMode,
    RuleHit,
    Slot,
    StyleMatch,
    StyleProfile,
    Suggestion,
    verdict_of,
)
from .utils import make_id, to_centi

DATA_DIR = Path(__file__).resolve().parent.parent / ".data"

# 锚点相似度下限的默认值（契约默认 0.60）；运行时以 settings.anchor_min_sim 为准，
# 因为 hash-embedding 与语义 embedding 的余弦分布不同，阈值必须按后端标定。
ANCHOR_MIN_SIMILARITY: float = 0.60


# --------------------------------------------------------------------------
# 特征与可用度
# --------------------------------------------------------------------------

def _mean(xs: list[float], default: float = 0.0) -> float:
    return float(sum(xs) / len(xs)) if xs else default


def compute_assessability(outfit: Outfit, profile: StyleProfile) -> dict[str, float]:
    """计算每个维度的「证据可用度」0..1。

    这是 baseline R-01 与 docs/03 §4.8 的落地：证据不足时**不下判断**，
    该维度规则被门控跳过、分数保持 base_d（宁可不说，也不瞎说）。
    """
    items = outfit.items
    prim = [g.primary_color for g in items if g.primary_color is not None]
    color_conf = _mean([c.confidence for c in prim], 0.0) if prim else 0.0
    base_conf = outfit.confidence or _mean([g.confidence for g in items], 0.0)

    # D1 色彩：需要至少两个有颜色的单品，且颜色置信度可接受
    d1 = min(1.0, color_conf * (1.0 if len(prim) >= 2 else 0.55))

    # D2 风格：需要搭配有风格标签 + 画像有一定样本量
    has_tags = 1.0 if any(g.style_tags for g in items) else 0.35
    coverage = min(1.0, profile.n_ref_outfits / 3.0) if profile.n_ref_outfits else 0.0
    d2 = min(1.0, 0.55 * has_tags + 0.55 * coverage)

    # D3 比例：需要上装 + 下装（或连衣裙）
    has_upper = bool(outfit.by_slot(Slot.upper)) or bool(outfit.by_slot(Slot.dress))
    has_lower = bool(outfit.by_slot(Slot.lower)) or bool(outfit.by_slot(Slot.dress))
    d3 = min(1.0, base_conf * (1.0 if (has_upper and has_lower) else 0.4))

    # D4 场合：必须知道场景，否则不判
    d4 = 1.0 if outfit.occasion else 0.3

    # D5 细节：单品越多越能看出细节，配饰缺失会降低
    d5 = min(1.0, 0.5 + 0.15 * len(items) + (0.2 if outfit.by_slot(Slot.accessory) else 0.0))

    # 输入质量（人形检测/主体裁剪的副产物）：多人、主体过小、疑似裁切、遮挡
    # 会整体压低所有维度的评估信心 —— 这是 baseline R-01「证据不足不下判断」的落地。
    # 注意：平铺摆拍图（无人物）只轻微降权（SUBJECT_QUALITY≈0.85），不算证据不足。
    quality = _subject_quality(outfit)
    if quality < 1.0:
        d1, d2, d3, d4, d5 = (v * quality for v in (d1, d2, d3, d4, d5))

    return {d.value: round(v, 3) for d, v in
            ((DimensionCode.D1, d1), (DimensionCode.D2, d2), (DimensionCode.D3, d3),
             (DimensionCode.D4, d4), (DimensionCode.D5, d5))}


def summarize_warnings(warnings: list[str]) -> list[str]:
    """告警去重并计数：同一告警出现 8 次 → ``xxx ×8``（保留首次出现顺序）。

    为什么需要：一个 8 张参考的请求会生成 8 组重复告警，把真正的关键信息淹掉。
    """
    counts: dict[str, int] = {}
    order: list[str] = []
    for w in warnings or []:
        if w not in counts:
            counts[w] = 0
            order.append(w)
        counts[w] += 1
    return [w if counts[w] == 1 else f"{w} ×{counts[w]}" for w in order]


def _data_source(parser: Any, outfit: Outfit) -> str:
    """判定这份结果的解析数据是**真实模型**产出还是**降级 mock**。

    这是防"以为在测自己的照片、其实在跑假数据"的硬保险（docs/10 F-13）：
    只要解析走了 mock（离线、无 key、图片不可读、请求失败），就标记 mock。
    """
    if str(getattr(parser, "name", "mock")) == "mock":
        return "mock"
    if any("FALLBACK_MOCK" in str(w) for w in (outfit.parse_warnings or [])):
        return "mock"
    return "vlm"


def _subject_quality(outfit: Outfit) -> float:
    """从 parse_warnings 里取裁剪器给出的输入质量（缺省 1.0，不惩罚）。"""
    for w in (outfit.parse_warnings or []):
        if isinstance(w, str) and w.startswith("SUBJECT_QUALITY:"):
            try:
                return max(0.0, min(1.0, float(w.split(":", 1)[1])))
            except ValueError:
                return 1.0
    return 1.0


def calibrate_style_cosine(raw: float, backend: str) -> float:
    """把后端相关的余弦**归一到稳定口径**，让规则阈值与 embedding 后端解耦（F-03 方案 A）。

    问题：同一对搭配，哈希后端给 0.30、FashionCLIP 给 0.79，而规则阈值（0.35 / 0.45–0.58）
    是按哈希写死的 → 换后端即全部失效。

    做法：为每个后端给一组"锚点"（同一套搭配的典型余弦 ↔ 明显不相关搭配的典型余弦），
    把原始余弦线性映射到统一刻度上。规则永远只吃映射后的值。
    """
    if backend != "visual":
        return max(-1.0, min(1.0, raw))
    # 视觉后端锚点（实测：自身≈0.95，无关≈0.60；见 docs/10 F-10 的 7 图两两分布）
    same, same_out = 0.95, 0.95
    off, off_out = 0.62, 0.22
    slope = (same_out - off_out) / (same - off)
    mapped = off_out + (raw - off) * slope
    return max(0.0, min(0.99, mapped))


def _image_backend_of(encoder: Any) -> str:
    try:
        return str(encoder.manifest().get("last_image_backend") or "hash")
    except Exception:  # noqa: BLE001
        return "hash"


def compute_features(outfit: Outfit, profile: StyleProfile, encoder: Any) -> Features:
    """组装 DSL 可引用、且 D2 需要的特征。全部确定性。"""
    pv = np.asarray(encoder.encode_profile(profile), dtype=np.float64)
    ov = np.asarray(encoder.encode_outfit(outfit), dtype=np.float64)
    backend = _image_backend_of(encoder)
    if pv.size and ov.size:
        denom = float(np.linalg.norm(pv) * np.linalg.norm(ov))
        raw_cosine = float(np.dot(pv, ov) / denom) if denom else 0.0
    else:
        raw_cosine = 0.0
    cosine = calibrate_style_cosine(raw_cosine, backend)

    prim = [g.primary_color for g in outfit.items if g.primary_color is not None]
    hues = [c.hue for c in prim]
    sats = [c.saturation for c in prim]
    families = {c.family for c in prim if c.family}
    formality = [g.formality for g in outfit.items if g.formality is not None]

    return Features(
        style_cosine=round(max(-1.0, min(1.0, cosine)), 6),
        style_coverage=min(1.0, profile.n_ref_outfits / 3.0) if profile.n_ref_outfits else 0.0,
        color_hue_span_deg=round(max(hues) - min(hues), 2) if len(hues) >= 2 else 0.0,
        color_max_saturation=round(max(sats), 4) if sats else 0.0,
        color_distinct_families=len(families),
        formality_span=float(max(formality) - min(formality)) if len(formality) >= 2 else 0.0,
        assessability=compute_assessability(outfit, profile),
    )


# --------------------------------------------------------------------------
# 风格匹配
# --------------------------------------------------------------------------

def _style_match(profile: StyleProfile, outfit: Outfit, cosine: float) -> StyleMatch:
    user_top = profile.top_tags[:3]
    outfit_styles: list[str] = []
    for g in outfit.items:
        for t in g.style_tags:
            if t not in outfit_styles:
                outfit_styles.append(t)

    matched = [t for t in outfit_styles if t in profile.top_tags]
    # 漂移：画像首选风格在搭配中缺失时，指出最接近的替换方向
    drifted = [f"{t} → {outfit_styles[0]}" for t in user_top[:1] if t not in outfit_styles and outfit_styles]
    return StyleMatch(
        user_top_styles=user_top,
        outfit_styles=outfit_styles,
        cosine=round(cosine, 4),
        matched=matched,
        drifted=drifted,
    )


# --------------------------------------------------------------------------
# 服务
# --------------------------------------------------------------------------


class DemoService:
    """同步服务。所有依赖懒加载，便于在模块尚未就绪时给出清晰错误。"""

    def __init__(self, persist: bool = False) -> None:
        self.settings = get_settings()
        self.persist = persist
        self._profiles: dict[str, StyleProfile] = {}
        self._reports: dict[str, Report] = {}
        self._outfits: dict[str, Outfit] = {}
        self._rules_dir = self.settings.rules_dir

        self._rules = None
        self._engine = None
        self._encoder = None
        self._store = None
        self._parser = None
        self._library = None

        if persist:
            DATA_DIR.mkdir(exist_ok=True)
            self._load()

    # ---------- 懒加载依赖 ----------

    @property
    def rules(self):
        if self._rules is None:
            from .rules.loader import load_rules
            self._rules = load_rules(self._rules_dir)
        return self._rules

    @property
    def engine(self):
        if self._engine is None:
            from .rules.engine import RuleEngine
            self._engine = RuleEngine(self.rules)
        return self._engine

    @property
    def encoder(self):
        if self._encoder is None:
            from .towers import TowerEncoder
            self._encoder = TowerEncoder()
        return self._encoder

    @property
    def store(self):
        if self._store is None:
            from .towers import VectorStore
            self._store = VectorStore()
        return self._store

    @property
    def parser(self):
        if self._parser is None:
            from .parsing import get_parser
            self._parser = get_parser()
        return self._parser

    @property
    def library(self):
        """搭配库索引（双塔召回的候选池）。懒加载：只在首次生成 report 时编码。"""
        if self._library is None:
            from .towers import visual as _visual
            from .towers.library import LibraryIndex, load_library
            items = load_library()
            # 两侧必须同后端：只有"解析侧确实能产出视觉向量"时才给库补视觉向量
            allow_visual = bool(self.settings.vlm_ready and _visual.available()
                               and (os.getenv("VISUAL_BACKEND") or "auto").lower()
                               not in ("off", "hash", "0", "false"))
            self._library = LibraryIndex(items, self.encoder, allow_visual=allow_visual)
        return self._library

    # ---------- 持久化（可选，演示用）----------

    def _load(self) -> None:
        p = DATA_DIR / "state.json"
        if not p.exists():
            return
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            self._profiles = {k: StyleProfile.model_validate(v) for k, v in raw.get("profiles", {}).items()}
            self._reports = {k: Report.model_validate(v) for k, v in raw.get("reports", {}).items()}
            self._outfits = {k: Outfit.model_validate(v) for k, v in raw.get("outfits", {}).items()}
        except Exception:  # 演示场景下损坏的缓存不应阻断启动
            pass

    def _save(self) -> None:
        if not self.persist:
            return
        DATA_DIR.mkdir(exist_ok=True)
        payload = {
            "profiles": {k: v.model_dump(mode="json") for k, v in self._profiles.items()},
            "reports": {k: v.model_dump(mode="json") for k, v in self._reports.items()},
            "outfits": {k: v.model_dump(mode="json") for k, v in self._outfits.items()},
        }
        (DATA_DIR / "state.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- 公开 API ----------

    def parse_outfit(self, image_refs: list[str], occasion: str | None = None,
                     season: str | None = None) -> tuple[Outfit, list[str]]:
        result = self.parser.parse_outfit(image_refs, occasion, season)
        outfit = result.outfit
        if occasion:
            outfit = outfit.model_copy(update={"occasion": occasion})
        if season:
            outfit = outfit.model_copy(update={"season": season})
        self._outfits[outfit.outfit_id] = outfit
        return outfit, list(result.warnings)

    def create_profile(self, image_refs: list[str], note: str | None = None,
                       banned_styles: list[str] | None = None,
                       explore_tags: list[str] | None = None
                       ) -> tuple[StyleProfile, list[Outfit]]:
        from .profiling import build_profile

        outfits: list[Outfit] = []
        warnings: list[str] = []
        # 每个 image_ref 视为「一组参考穿搭」（一张搭配照）
        for ref in image_refs:
            outfit, w = self.parse_outfit([ref], None, None)
            outfits.append(outfit)
            warnings.extend(w)

        banned = [s.strip() for s in (banned_styles or []) if s and s.strip()]
        explore = [s.strip() for s in (explore_tags or []) if s and s.strip()]

        profile_id = make_id("prf", sorted(image_refs), sorted(banned), sorted(explore),
                             self.settings.ruleset_version)
        profile = build_profile(outfits, profile_id=profile_id)
        # 用户主观偏好覆盖聚合结果（主观意愿优先于统计推断）—— 这是 F-08 的关键一环：
        # 有了 banned_styles，唯一那条 hard 风格规则（R-STYLE-001）才可能生效，
        # 一次 veto 就能产生 20+ 分的画像差异。
        if banned or explore:
            profile = profile.model_copy(update={
                "banned_styles": banned,
                "explore_tags": explore,
            })
        self._profiles[profile.profile_id] = profile

        # 参考搭配进入向量库，供 reports 的 anchors 召回
        for o in outfits:
            vec = self.encoder.encode_outfit(o)
            self.store.add(o.outfit_id, list(vec))

        self._save()
        return profile, outfits

    def get_profile(self, profile_id: str) -> StyleProfile | None:
        return self._profiles.get(profile_id)

    def get_report(self, report_id: str) -> Report | None:
        return self._reports.get(report_id)

    def get_outfit(self, outfit_id: str) -> Outfit | None:
        return self._outfits.get(outfit_id)

    def create_report(self, profile_id: str, outfit_id: str | None = None,
                      image_refs: list[str] | None = None,
                      occasion: str | None = None, season: str | None = None) -> Report:
        t0 = time.perf_counter()
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise KeyError(f"profile not found: {profile_id}")

        if outfit_id:
            outfit = self._outfits.get(outfit_id)
            if outfit is None:
                raise KeyError(f"outfit not found: {outfit_id}")
            if occasion:
                outfit = outfit.model_copy(update={"occasion": occasion})
            if season:
                outfit = outfit.model_copy(update={"season": season})
        elif image_refs:
            outfit, _ = self.parse_outfit(image_refs, occasion, season)
        else:
            raise ValueError("必须提供 outfit_id 或 image_refs")

        t_parse = time.perf_counter()

        # ⑥.5 特征（含双塔余弦）
        features = compute_features(outfit, profile, self.encoder)
        # ⑦ 规则（引擎内部完成门控/抑制/互斥/排序）
        raw_hits: list[RuleHit] = self.engine.run(outfit, profile, features)
        # 预算截断 + 维度分 + 总分（确定性）
        from .scoring import score
        result = score(raw_hits, features)
        t_rules = time.perf_counter()

        # ⑧-b 风格匹配
        style_match = _style_match(profile, outfit, features.style_cosine)

        # 双塔召回：用搭配向量找相近参考搭配。
        # 阈值来自配置（契约默认 0.60）：**hash 模式的余弦区间被压缩**（实测 0.10–0.40），
        # 用 0.60 会导致锚点永远为空。所以阈值必须随 embedding 后端标定，见 .env 注释。
        ov = self.encoder.encode_outfit(outfit)
        min_sim = self.settings.anchor_min_sim
        anchors = [Anchor(outfit_id=oid, similarity=round(sim, 4))
                   for oid, sim in self.store.search(list(ov), k=5)
                   if sim >= min_sim][:3]

        # 双塔召回（验收标准第 4 条）：用户塔向量 × 搭配库向量 → top-K
        # 这是真召回：候选来自独立搭配库，不是用户自己的输入。
        pv = self.encoder.encode_profile(profile)
        recall_items = self.library.to_recall_items(self.library.recall(pv, k=5))

        # ⑨ 合成（模板优先；LLM 仅在可用时润色）
        from .synthesis import synthesize
        ctx = {
            "outfit": outfit,
            "profile": profile,
            "verdict": result.verdict,
            "total_score": round(result.total_centi / 100.0, 2),
            "vetoed": result.vetoed,
            "dimension_scores": result.dimensions,
            "rule_hits": result.hits,
            "style_match": style_match,
            "features": features,
        }
        summary, suggestions, render_mode = synthesize(ctx)
        t_end = time.perf_counter()

        report_id = make_id("rpt", profile_id, outfit.outfit_id,
                            self.settings.ruleset_version, len(result.hits))
        confidence = round(min(
            outfit.confidence or 0.8,
            features.assess(DimensionCode.D1),
            0.4 + 0.6 * min(1.0, profile.n_ref_outfits / 3.0),
        ), 3)

        report = Report(
            report_id=report_id,
            outfit_id=outfit.outfit_id,
            profile_id=profile.profile_id,
            verdict=result.verdict,
            vetoed=result.vetoed,
            total_score=round(result.total_centi / 100.0, 2),
            total_centi=result.total_centi,
            confidence=confidence,
            dimension_scores=result.dimensions,
            rule_hits=result.hits,
            score_ledger=result.ledger,
            style_match=style_match,
            anchors=anchors,
            recall=recall_items,
            suggestions=suggestions,
            summary=summary,
            meta=ReportMeta(
                vlm_model=getattr(self.parser, "name", "mock"),
                llm_model=self.settings.llm_model if self.settings.llm_ready else "template",
                embedding_model=str(self.encoder.config.get("mode", "hash")),
                ruleset_version=self.settings.ruleset_version,
                render_mode=render_mode,
                data_source=_data_source(self.parser, outfit),
                parse_warnings=summarize_warnings(outfit.parse_warnings),
                embedding_config=self.encoder.config,
                latency_ms={
                    "parse": int((t_parse - t0) * 1000),
                    "rules_score": int((t_rules - t_parse) * 1000),
                    "synthesis": int((t_end - t_rules) * 1000),
                    "total": int((t_end - t0) * 1000),
                },
            ),
        )
        self._reports[report.report_id] = report
        self._save()
        return report

    def health(self) -> dict:
        providers = {"vlm": self.settings.vlm_model if self.settings.vlm_ready else "mock",
                     "llm": self.settings.llm_model if self.settings.llm_ready else "template",
                     "embedding": self.settings.embedding_mode}
        try:
            count = len(self.rules)
        except Exception:
            count = 0
        try:
            lib_count = len(self.library)
        except Exception:
            lib_count = 0
        return {"status": "ok", "offline_mode": self.settings.offline_mode,
                "ruleset_version": self.settings.ruleset_version,
                "rule_count": count, "library_count": lib_count, "providers": providers,
                "profiles": len(self._profiles), "reports": len(self._reports)}


    def reset(self) -> None:
        """清空内存与磁盘状态（评测/回归必须从干净状态开始，避免跨用例污染）。"""
        self._profiles.clear()
        self._reports.clear()
        self._outfits.clear()
        if self._store is not None:
            try:
                self._store.reset()          # type: ignore[attr-defined]
            except AttributeError:
                pass
        if self.persist:
            p = DATA_DIR / "state.json"
            if p.exists():
                p.unlink()


_SERVICE: DemoService | None = None


def get_service(*, fresh: bool = False, persist: bool = True) -> DemoService:
    """取服务实例。

    fresh=True 返回**不复用、不落盘**的新实例 —— 评测与测试必须用这种方式，
    否则上一次运行的画像/报告会污染下一次（违反确定性与可复现验收）。
    """
    global _SERVICE
    if fresh:
        return DemoService(persist=False)
    if _SERVICE is None:
        _SERVICE = DemoService(persist=persist)
    return _SERVICE
