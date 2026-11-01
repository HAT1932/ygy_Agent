"""诊断分级测试（docs/10 F-16）。

被 8 张真实照片一次逼出来的问题：原始告警全被拼成一行 `；` 分隔，
用户分不清「已缩放 352 tokens」「已裁出主体」「已提取视觉向量」是**成功**
还是**失败**。所以这里锁三件事：
  ① 分级：成功信息必须是 info，真问题才是 warn/error；
  ② 归属：诊断要说出「第几张」；
  ③ 影响：每条 warn 必须带一句"会影响哪一块"，否则用户会把
     「证据不足」误读成「穿得不好」。
"""
from __future__ import annotations

import pytest

from src.contracts import Diagnostic, ReportMeta
from src.diagnostics import (ERROR, INFO, WARN, MAX_RECOMMENDED_REFS, analyze,
                             build_diagnostics, build_evidence_summary, code_of,
                             is_registered, label_of, severity_of)

# 真机（8 张 outfit 照片）实际吐出来的一批原始告警
REAL_RUN = [
    "IMAGE_TOKENS:352",
    "SUBJECT_CROPPED:vlm:area=0.6872:full_body=True",
    "VISUAL_EMBEDDING:fashionclip:1",
    "SUBJECT_QUALITY:1.0",
]

REAL_RUN_PROBLEM = [
    "SUBJECT:OCCLUDED:手机",
    "INCOMPLETE_NO_SHOES_MAYBE_CROPPED",
]


def test_code_of_strips_detail():
    assert code_of("IMAGE_TOKENS:352") == "IMAGE_TOKENS"
    assert code_of("SUBJECT:OCCLUDED:手机") == "SUBJECT:OCCLUDED"
    assert code_of("SUBJECT_CROPPED:vlm:area=0.68:full_body=True") == "SUBJECT_CROPPED"
    # 四种降级原因归并成一条，避免 UI 里出现 4 条几乎一样的「已降级」
    assert code_of("VLM_UNAVAILABLE_FALLBACK_MOCK") == "FALLBACK_MOCK"
    assert code_of("VLM_PARSE_FAILED_FALLBACK_MOCK:attempt1:Timeout") == "FALLBACK_MOCK"


@pytest.mark.parametrize("code", [
    "IMAGE_TOKENS", "SUBJECT_CROPPED", "SUBJECT_QUALITY", "VISUAL_EMBEDDING",
    "INCOMPLETE_NO_SHOES_MAYBE_CROPPED", "INCOMPLETE_NO_LOWER_BODY",
    "IMAGE_UNDECODABLE", "FALLBACK_MOCK", "PROFILE_REFS_ABOVE_RECOMMENDED",
])
def test_every_code_emitted_by_parsers_is_registered(code):
    """新增告警码必须补映射表，否则会以 warn + 裸 code 的形式漏到 UI。"""
    assert is_registered(code)
    assert label_of(code) != code


def test_success_signals_are_info_not_warning():
    """这四条是「一切正常」，绝不能出现在告警区。"""
    for raw in REAL_RUN:
        sev = severity_of(code_of(raw))
        assert sev == INFO, f"{raw} 被误判成 {sev}"


def test_real_problems_are_warn_or_error():
    for raw in REAL_RUN_PROBLEM:
        assert severity_of(code_of(raw)) in (WARN, ERROR)
    assert severity_of("FALLBACK_MOCK") == ERROR
    assert severity_of("IMAGE_UNDECODABLE") == ERROR


def test_grouping_counts_occurrences_and_attributes_photos():
    per_photo = [list(REAL_RUN), list(REAL_RUN), ["SUBJECT:OCCLUDED:手机"]]
    diags = build_diagnostics(per_photo)
    by_code = {d.code: d for d in diags}
    assert by_code["IMAGE_TOKENS"].count == 2
    assert by_code["IMAGE_TOKENS"].refs == [1, 2]
    assert by_code["SUBJECT:OCCLUDED"].refs == [3]
    assert by_code["SUBJECT:OCCLUDED"].detail == "手机"


def test_detail_aggregates_values_humanly():
    per_photo = [["IMAGE_TOKENS:300"], ["IMAGE_TOKENS:608"]]
    d = {x.code: x for x in build_diagnostics(per_photo)}["IMAGE_TOKENS"]
    assert "300–608" in d.detail

    q = [["SUBJECT_QUALITY:1.0"], ["SUBJECT_QUALITY:1.0"], ["SUBJECT_QUALITY:0.6"]]
    d = {x.code: x for x in build_diagnostics(q)}["SUBJECT_QUALITY"]
    assert "最低 0.6" in d.detail and "1.0×2" in d.detail


def test_sorted_error_then_warn_then_info():
    per_photo = [list(REAL_RUN) + ["FALLBACK_MOCK", "SUBJECT:OCCLUDED:包"]]
    diags = build_diagnostics(per_photo)
    ranks = [{"error": 0, "warn": 1, "info": 2}[d.severity] for d in diags]
    assert ranks == sorted(ranks)
    assert diags[0].severity == ERROR


def test_every_warning_has_impact_text():
    """没有 impact 的 warn 等于没解释，用户会把降权当成差评。"""
    per_photo = [list(REAL_RUN_PROBLEM)]
    for d in build_diagnostics(per_photo):
        assert d.impact, f"{d.code} 缺少影响说明"


def test_unknown_code_falls_back_but_stays_visible():
    diags = build_diagnostics([["SOMETHING_NEW:42"]])
    assert len(diags) == 1
    assert diags[0].severity == WARN
    assert diags[0].code == "SOMETHING_NEW"


def test_evidence_summary_states_coverage():
    per_photo = [list(REAL_RUN), list(REAL_RUN), ["SUBJECT:OCCLUDED:手机"]]
    diags, summary = analyze(per_photo)
    assert "3 张参考" in summary
    assert "视觉向量 2/3" in summary
    assert "主体裁剪 2/3" in summary
    assert "需处理" in summary


def test_evidence_summary_clean_run():
    per_photo = [list(REAL_RUN)] * 2
    _, summary = analyze(per_photo)
    assert "无异常" in summary


def test_evidence_summary_noun_for_report():
    """报告里只有 1 张「照片」，不是「参考」——措辞错了会让人以为画像有 1 张。"""
    _, summary = analyze([list(REAL_RUN)], noun="照片")
    assert "1 张照片" in summary and "参考" not in summary


def test_refs_above_recommended_gets_hint():
    per_photo = [list(REAL_RUN)] * 8
    diags, _ = analyze(per_photo, [f"PROFILE_REFS_ABOVE_RECOMMENDED:8"])
    hit = [d for d in diags if d.code == "PROFILE_REFS_ABOVE_RECOMMENDED"]
    assert hit and hit[0].severity == WARN
    assert "8 张" in hit[0].detail
    # 5 张以内不该有这条
    diags5 = build_diagnostics([list(REAL_RUN)] * MAX_RECOMMENDED_REFS)
    assert not [d for d in diags5 if d.code == "PROFILE_REFS_ABOVE_RECOMMENDED"]


def test_determinism():
    per_photo = [list(REAL_RUN), list(REAL_RUN_PROBLEM)]
    a = [d.model_dump() for d in build_diagnostics(per_photo)]
    b = [d.model_dump() for d in build_diagnostics(per_photo)]
    assert a == b


def test_contract_fields_default_empty():
    meta = ReportMeta()
    assert meta.diagnostics == []
    assert meta.evidence_summary == ""
    d = Diagnostic(code="IMAGE_TOKENS", label="x")
    assert d.severity == "info" and d.refs == [] and d.count == 1
