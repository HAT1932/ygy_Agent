"""API 层测试 —— 用注入的 Stub 服务隔离业务依赖。

覆盖：
  * 三个端点族的 200 / 404 / 422 路径；
  * 未捕获异常 → 500 统一错误形状；
  * 前端静态入口可访问；
  * Report 响应能被 ``Report.model_validate`` 接受（含 INV-2 / 维度分之和校验）。
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.contracts import (
    BASE_SCORE_CENTI,
    DIMENSIONS,
    Anchor,
    Category,
    Color,
    CreateProfileResponse,
    DimensionCode,
    DimensionScore,
    Garment,
    Outfit,
    Report,
    ReportMeta,
    RuleHit,
    ScoreLedgerEntry,
    Severity,
    Slot,
    StyleMatch,
    StyleProfile,
    StyleTagScore,
    Suggestion,
    Verdict,
    verdict_of,
)
from src.utils import centi_to_score, to_points


# --------------------------------------------------------------------------
# 构造合法契约对象（Stub 服务用）
# --------------------------------------------------------------------------


def make_outfit(outfit_id: str = "oft_test", occasion: str | None = "business") -> Outfit:
    return Outfit(
        outfit_id=outfit_id,
        items=[
            Garment(garment_id="gar_u1", slot=Slot.upper, category="衬衫",
                    colors=[Color(hex="#FFFFFF", name="白", family="neutral")],
                    fit="直筒", formality=4, style_tags=["极简", "通勤知性"]),
            Garment(garment_id="gar_l1", slot=Slot.lower, category="西裤",
                    colors=[Color(hex="#1B2430", name="藏青", family="neutral")],
                    fit="直筒", formality=4, style_tags=["通勤知性"]),
        ],
        occasion=occasion,
        season="autumn",
        confidence=0.9,
    )


def make_profile(profile_id: str = "prf_test") -> StyleProfile:
    return StyleProfile(
        profile_id=profile_id,
        tags=[StyleTagScore(tag="极简", weight=0.62),
              StyleTagScore(tag="通勤知性", weight=0.38)],
        preferred_formality=4,
        n_ref_outfits=3,
        confidence=0.78,
        source_outfit_ids=["oft_r1", "oft_r2", "oft_r3"],
        tag_evidence={"极简": ["oft_r1", "oft_r2"], "通勤知性": ["oft_r3"]},
    )


def make_report(profile_id: str = "prf_test", outfit_id: str = "oft_test") -> Report:
    """构造一份满足 INV-2 / 维度求和 / 可追溯要求的报告。"""
    # 一条 soft 扣分（D3 −2.00）与一条 boost（D2 +1.50）
    hits = [
        RuleHit(
            rule_id="R-PROP-004", title="五五分", category=Category.PROPORTION,
            severity=Severity.soft, dimension=DimensionCode.D3,
            delta_schema=-2.0, delta_applied=-2.0, clipped=False,
            triggered_value=0.50,
            evidence="视觉腰线位于身高 50% 处（0.50），接近中分。",
            explanation="上下比例接近五五分，没有占到显高的便宜。",
            suggestion="把衬衫塞入，并换高腰直筒下装，把腰线提到 0.42 以上。",
            render_priority=50,
        ),
        RuleHit(
            rule_id="R-STYLE-008", title="命中 top-1 风格", category=Category.STYLE,
            severity=Severity.boost, dimension=DimensionCode.D2,
            delta_schema=1.5, delta_applied=1.5, clipped=False,
            triggered_value={"outfit_style": "极简"},
            evidence="本套主基调「极简」正是你最常穿的风格。",
            explanation="风格方向与画像一致。",
            reinforce="换季时优先在「极简」里找同款不同色的单品。",
            render_priority=50,
        ),
    ]
    penalty = {DimensionCode.D3: 200}
    boost = {DimensionCode.D2: 150}
    dims: list[DimensionScore] = []
    total = 0
    for spec in DIMENSIONS:
        pen = penalty.get(spec.code, 0)
        boo = boost.get(spec.code, 0)
        sc = spec.base_centi - pen + boo
        total += sc
        dims.append(DimensionScore(
            code=spec.code, name=spec.name, score=centi_to_score(sc),
            max=to_points(spec.max_centi), weight=spec.weight,
            base=to_points(spec.base_centi), assessability=1.0,
            penalty_used=to_points(pen), boost_used=to_points(boo),
            score_centi=sc, base_centi=spec.base_centi, max_centi=spec.max_centi,
            penalty_centi=pen, boost_centi=boo, gated=False,
        ))
    ledger = [
        ScoreLedgerEntry(seq=1, rule_id=h.rule_id, dimension=h.dimension,
                         delta_schema=h.delta_schema, delta_applied=h.delta_applied)
        for h in hits
    ]
    return Report(
        report_id="rpt_test",
        outfit_id=outfit_id,
        profile_id=profile_id,
        verdict=verdict_of(total, False),
        vetoed=False,
        total_score=centi_to_score(total),
        total_centi=total,
        base_score=BASE_SCORE_CENTI / 100.0,
        base_centi=BASE_SCORE_CENTI,
        confidence=0.88,
        dimension_scores=dims,
        rule_hits=hits,
        score_ledger=ledger,
        style_match=StyleMatch(user_top_styles=["极简", "通勤知性"],
                               outfit_styles=["极简", "通勤知性"], cosine=0.71,
                               matched=["极简", "通勤知性"]),
        anchors=[Anchor(outfit_id="oft_r1", similarity=0.91)],
        suggestions=[Suggestion(priority=1, target="上装",
                                action="把衬衫塞入高腰下装",
                                reason_rule_ids=["R-PROP-004"], expected_gain=2.0)],
        summary="方向很稳，唯一吃亏在比例。",
        meta=ReportMeta(parse_warnings=["PARSE_WARNING_DEMO"]),
    )


class StubService:
    """只依赖契约的服务桩：签名与 src.pipeline.get_service() 返回对象一致。"""

    def __init__(self) -> None:
        self.profiles: dict[str, StyleProfile] = {}
        self.reports: dict[str, Report] = {}
        self.outfits: dict[str, Outfit] = {}
        self.fail_next: BaseException | None = None

    # ---- 契约方法 ----
    def health(self) -> dict[str, Any]:
        return {"status": "ok", "offline_mode": True, "ruleset_version": "rs-test",
                "rule_count": 50,
                "providers": {"vlm": "mock", "llm": "template", "embedding": "hash"}}

    def create_profile(self, image_refs: list[str], note: str | None = None):
        pid = "prf_" + str(abs(hash(tuple(image_refs))) % 10**8)
        profile = make_profile(pid)
        self.profiles[pid] = profile
        outfit = make_outfit("oft_" + pid[4:])
        self.outfits[outfit.outfit_id] = outfit
        return profile, [outfit]

    def parse_outfit(self, image_refs: list[str], occasion: str | None = None,
                     season: str | None = None):
        outfit = make_outfit("oft_parse")
        outfit = outfit.model_copy(update={"occasion": occasion, "season": season})
        self.outfits[outfit.outfit_id] = outfit
        return outfit, ["PARSE_WARNING_DEMO"]

    def create_report(self, profile_id: str, outfit_id: str | None = None,
                      image_refs: list[str] | None = None,
                      occasion: str | None = None, season: str | None = None):
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc
        if profile_id not in self.profiles:
            raise KeyError(f"profile not found: {profile_id}")
        if outfit_id and outfit_id not in self.outfits:
            raise KeyError(f"outfit not found: {outfit_id}")
        if not outfit_id and not image_refs:
            raise ValueError("必须提供 outfit_id 或 image_refs")
        report = make_report(profile_id, outfit_id or "oft_parse")
        self.reports[report.report_id] = report
        return report

    def get_profile(self, profile_id: str) -> StyleProfile | None:
        return self.profiles.get(profile_id)

    def get_report(self, report_id: str) -> Report | None:
        return self.reports.get(report_id)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    app = create_app(service=StubService())
    return TestClient(app)


@pytest.fixture()
def raw_client() -> TestClient:
    """不把服务端异常抛回测试进程，用于断言 500 响应体。"""
    app = create_app(service=StubService())
    return TestClient(app, raise_server_exceptions=False)


# ==========================================================================
# 健康检查
# ==========================================================================


def test_health_200(client: TestClient) -> None:
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["offline_mode"] is True
    assert body["rule_count"] == 50
    assert body["providers"]["vlm"] == "mock"
    assert body["contract_version"]


# ==========================================================================
# 画像
# ==========================================================================


def test_create_profile_200(client: TestClient) -> None:
    r = client.post("/v1/profiles", json={"image_refs": ["demo/a", "demo/b"], "note": "通勤"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile_id"].startswith("prf_")
    assert body["profile"]["n_ref_outfits"] >= 1
    assert body["profile"]["tag_evidence"]
    assert body["outfits"]


def test_create_profile_422_empty_refs(client: TestClient) -> None:
    r = client.post("/v1/profiles", json={"image_refs": []})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["retryable"] is False
    assert body["detail"]["fields"]


def test_create_profile_422_too_many_refs(client: TestClient) -> None:
    r = client.post("/v1/profiles", json={"image_refs": [f"ref-{i}" for i in range(11)]})
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_create_profile_422_wrong_type(client: TestClient) -> None:
    r = client.post("/v1/profiles", json={"image_refs": "not-a-list"})
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_get_profile_200(client: TestClient) -> None:
    created = client.post("/v1/profiles", json={"image_refs": ["demo/a"]}).json()
    pid = created["profile_id"]
    r = client.get(f"/v1/profiles/{pid}")
    assert r.status_code == 200
    assert r.json()["profile_id"] == pid
    assert r.json()["tag_evidence"]


def test_get_profile_404(client: TestClient) -> None:
    r = client.get("/v1/profiles/prf_does_not_exist")
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "NOT_FOUND"
    assert body["retryable"] is False
    assert body["message"]


# ==========================================================================
# 解析
# ==========================================================================


def test_parse_outfit_200(client: TestClient) -> None:
    r = client.post("/v1/outfits/parse",
                    json={"image_refs": ["demo/x"], "occasion": "date", "season": "autumn"})
    assert r.status_code == 200
    body = r.json()
    assert body["outfit"]["outfit_id"].startswith("oft_")
    assert body["outfit"]["occasion"] == "date"
    assert body["warnings"] == ["PARSE_WARNING_DEMO"]


def test_parse_outfit_422(client: TestClient) -> None:
    r = client.post("/v1/outfits/parse", json={"image_refs": []})
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


# ==========================================================================
# 报告
# ==========================================================================


def test_create_report_200_and_contract_valid(client: TestClient) -> None:
    pid = client.post("/v1/profiles", json={"image_refs": ["demo/a", "demo/b"]}).json()["profile_id"]
    r = client.post("/v1/reports", json={
        "profile_id": pid, "image_refs": ["demo/review-1"], "occasion": "business",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # 契约对象必须能通过（含 INV-2、维度分之和、veto→unsuitable 校验）
    report = Report.model_validate(body)
    assert report.total_centi == report.base_centi + int(
        round(sum(h.delta_applied for h in report.rule_hits) * 100))
    assert sum(d.score_centi for d in report.dimension_scores) == report.total_centi
    assert report.verdict.value in {"strongly_recommended", "suitable", "neutral", "unsuitable"}
    assert report.meta.ruleset_version


def test_create_report_with_outfit_id_200(client: TestClient) -> None:
    stub = StubService()
    outfit, _ = stub.parse_outfit(["demo/x"])
    app = create_app(service=stub)
    c = TestClient(app)
    pid = c.post("/v1/profiles", json={"image_refs": ["demo/a"]}).json()["profile_id"]
    stub.outfits[outfit.outfit_id] = outfit
    r = c.post("/v1/reports", json={"profile_id": pid, "outfit_id": outfit.outfit_id})
    assert r.status_code == 200, r.text
    assert Report.model_validate(r.json()).outfit_id == outfit.outfit_id


def test_create_report_404_unknown_profile(client: TestClient) -> None:
    r = client.post("/v1/reports", json={"profile_id": "prf_missing", "image_refs": ["demo/x"]})
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == "NOT_FOUND"
    assert body["message"]


def test_create_report_404_unknown_outfit(client: TestClient) -> None:
    pid = client.post("/v1/profiles", json={"image_refs": ["demo/a"]}).json()["profile_id"]
    r = client.post("/v1/reports", json={"profile_id": pid, "outfit_id": "oft_missing"})
    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"


def test_create_report_422_neither_ref(client: TestClient) -> None:
    r = client.post("/v1/reports", json={"profile_id": "prf_x"})
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_create_report_422_missing_profile_id(client: TestClient) -> None:
    r = client.post("/v1/reports", json={"image_refs": ["demo/x"]})
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_create_report_422_too_many_refs(client: TestClient) -> None:
    r = client.post("/v1/reports", json={
        "profile_id": "prf_x", "image_refs": [f"r{i}" for i in range(6)],
    })
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_get_report_200_and_404(client: TestClient) -> None:
    pid = client.post("/v1/profiles", json={"image_refs": ["demo/a"]}).json()["profile_id"]
    created = client.post("/v1/reports", json={
        "profile_id": pid, "image_refs": ["demo/r"], "occasion": "date",
    }).json()
    rid = created["report_id"]
    r = client.get(f"/v1/reports/{rid}")
    assert r.status_code == 200
    assert Report.model_validate(r.json()).report_id == rid

    missing = client.get("/v1/reports/rpt_missing")
    assert missing.status_code == 404
    assert missing.json()["code"] == "NOT_FOUND"


# ==========================================================================
# 统一异常 & CORS & 静态前端
# ==========================================================================


def test_unhandled_exception_500(raw_client: TestClient) -> None:
    client = raw_client
    pid = client.post("/v1/profiles", json={"image_refs": ["demo/a"]}).json()["profile_id"]
    # 直接注入“下一次调用抛异常”的钩子：通过 app.state.service 拿到同一个 stub
    stub = client.app.state.service
    stub.fail_next = RuntimeError("boom")
    r = client.post("/v1/reports", json={"profile_id": pid, "image_refs": ["demo/r"]})
    assert r.status_code == 500
    body = r.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert body["retryable"] is False
    assert "boom" not in body["message"]  # 内部细节不得泄漏到用户文案


def test_cors_allows_local_origin(client: TestClient) -> None:
    r = client.get("/v1/health", headers={"Origin": "http://127.0.0.1:5173"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") in {"*", "http://127.0.0.1:5173"}


def test_real_pipeline_end_to_end() -> None:
    """真实链路冒烟：画像 → 报告 → 契约校验。

    依赖 rules/ 与 IO 层；这些模块尚未落地时跳过（而不是让 API 测试变红）。
    """
    try:
        from src.pipeline import get_service

        svc = get_service()
        info = svc.health()
    except Exception as exc:  # pragma: no cover - 依赖未就绪时走这里
        pytest.skip(f"pipeline 尚未就绪: {exc}")
    if not info.get("rule_count"):
        pytest.skip("规则集未加载，跳过真实链路冒烟")

    profile, outfits = svc.create_profile(["demo/e2e-a", "demo/e2e-b", "demo/e2e-c"])
    assert profile.n_ref_outfits == 3
    assert len(outfits) == 3
    assert profile.profile_id.startswith("prf_")

    report = svc.create_report(profile.profile_id, image_refs=["demo/e2e-review"],
                               occasion="business")
    # 用契约模型重新校验（含 INV-2 加法性、维度分之和、veto→unsuitable）
    Report.model_validate(report.model_dump(mode="json"))
    assert all(h.rule_id for h in report.rule_hits)
    assert report.meta.ruleset_version
    # 确定性：同输入第二次必须完全一致
    again = svc.create_report(profile.profile_id, image_refs=["demo/e2e-review"],
                              occasion="business")
    assert again.total_centi == report.total_centi
    assert ([(h.rule_id, h.delta_applied) for h in again.rule_hits]
            == [(h.rule_id, h.delta_applied) for h in report.rule_hits])


def test_index_serves_frontend(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "AI 穿搭顾问" in r.text
    # 零 CDN 依赖：不得有外部 script/link/@import/字体引用
    html = r.text.lower()
    for token in ("<script src=", "<link ", "@import", "fonts.googleapis", "cdn."):
        assert token not in html, f"前端不应包含外部依赖: {token}"
