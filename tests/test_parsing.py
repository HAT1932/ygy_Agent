"""解析层测试：mock 确定性、零网络、显式剧本选择、Qwen 请求契约与降级。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Settings, get_settings
from src.contracts import Slot
from src.parsing import (
    BaseParser,
    MockParser,
    N_SCRIPTS,
    ParseResult,
    QwenVLMParser,
    SCRIPTS,
    get_parser,
)
from src.parsing.base import normalize_occasion, normalize_season

ENV_KEYS = ("DASHSCOPE_API_KEY", "OFFLINE_MODE", "DEEPSEEK_API_KEY")


def _reset_settings(monkeypatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clean_config():
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 1. mock 确定性
# ---------------------------------------------------------------------------
def test_mock_is_deterministic_for_same_image_ref():
    first = MockParser().parse_outfit(["demo/commute-1"], "business", "autumn")
    second = MockParser().parse_outfit(["demo/commute-1"], "business", "autumn")
    assert isinstance(first, ParseResult)
    assert first.outfit.model_dump() == second.outfit.model_dump()
    assert first.outfit.outfit_id == second.outfit.outfit_id
    assert [g.garment_id for g in first.outfit.items] == [
        g.garment_id for g in second.outfit.items]


def test_mock_is_deterministic_across_processes():
    code = (
        "import json;"
        "from src.parsing import MockParser;"
        "o=MockParser().parse_outfit(['demo/mono-1'], 'business', 'winter').outfit;"
        "print(json.dumps(o.model_dump(mode='json'), ensure_ascii=False, sort_keys=True))"
    )
    outputs = []
    for hash_seed in ("0", "12345"):
        env = dict(os.environ)
        env.update({"PYTHONHASHSEED": hash_seed, "PYTHONIOENCODING": "utf-8"})
        env.pop("DASHSCOPE_API_KEY", None)
        completed = subprocess.run(
            [sys.executable, "-c", code], cwd=str(ROOT), env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        assert completed.returncode == 0, completed.stderr
        outputs.append(completed.stdout.strip().splitlines()[-1])
    assert outputs[0] == outputs[1]


# ---------------------------------------------------------------------------
# 2. 显式剧本选择（评测用例可读且稳定）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ref, expected_first_category", [
    ("demo/commute-1", "棉质衬衫"),
    ("demo/date-1", "真丝衬衫"),
    ("demo/sport-1", "连帽卫衣"),
    ("demo/formal-1", "西装外套"),
    ("demo/casual-1", "印花T恤"),
    ("demo/clash-1", "短袖T恤"),
    ("demo/mono-1", "针织衫"),
    ("demo/season-1", "亚麻衬衫"),
    ("demo/accessory-1", "缎面衬衫"),
    ("demo/formal_missing-1", "白衬衫"),
])
def test_explicit_prefix_selects_script(ref, expected_first_category):
    result = MockParser().parse_outfit([ref], None, None)
    assert result.outfit.items[0].category == expected_first_category
    assert result.outfit.image_ref == ref


def test_script_selection_does_not_depend_on_hash_seed():
    ref = "demo/clash-1"
    first = MockParser().parse_outfit([ref], "date", None).outfit
    second = MockParser().parse_outfit([ref], "date", None).outfit
    assert first.items[0].colors[0].hex == "#F5006A"
    assert second.items[1].colors[0].hex == "#00E64D"


def test_all_scripts_produce_valid_outfits():
    assert N_SCRIPTS == len(SCRIPTS) >= 6
    parser = MockParser()
    seen = set()
    aliases = ("commute", "date", "sport", "formal", "casual",
               "clash", "mono", "season", "accessory", "formal_missing")
    for alias in aliases:
        result = parser.parse_outfit([f"demo/{alias}-1"], "casual", "spring")
        outfit = result.outfit
        assert outfit.items, alias
        assert all(g.garment_id and g.category for g in outfit.items)
        assert all(0.0 <= c.saturation <= 1.0 for g in outfit.items for c in g.colors)
        assert all(g.confidence == g.confidence for g in outfit.items)  # 非 NaN
        seen.add(outfit.items[0].category)
    assert len(seen) == N_SCRIPTS


def test_hash_fallback_is_stable_and_valid():
    parser = MockParser()
    a = parser.parse_outfit(["ref-unknown-1"], None, None).outfit
    b = MockParser().parse_outfit(["ref-unknown-1"], None, None).outfit
    assert a.model_dump() == b.model_dump()
    assert a.items


# ---------------------------------------------------------------------------
# 3. 无 key → 零网络请求
# ---------------------------------------------------------------------------
def test_no_key_means_zero_network(monkeypatch):
    _reset_settings(monkeypatch)
    calls: list[str] = []

    def forbidden(*args, **kwargs):
        calls.append("network")
        raise AssertionError("无 key 时不允许发起任何网络请求")

    monkeypatch.setattr(httpx, "post", forbidden)
    monkeypatch.setattr(httpx, "request", forbidden)
    monkeypatch.setattr(httpx, "get", forbidden)

    parser = get_parser()
    assert isinstance(parser, MockParser)
    result = parser.parse_outfit(["demo/commute-1"], "business", "autumn")
    assert result.outfit.items and result.warnings == []

    # 即便绕过工厂直接实例化 Qwen 适配器，也不能建连
    qwen = QwenVLMParser(settings=Settings(dashscope_api_key=None, offline_mode=True))
    fallback = qwen.parse_outfit(["demo/date-1"], "date", None)
    assert fallback.outfit.items
    assert any("VLM_UNAVAILABLE_FALLBACK_MOCK" in w for w in fallback.warnings)
    assert calls == []


def test_qwen_with_key_but_offline_mode_still_no_network(monkeypatch):
    calls: list[str] = []

    def forbidden(*args, **kwargs):
        calls.append("network")
        raise AssertionError("OFFLINE_MODE=true 时不允许网络请求")

    monkeypatch.setattr(httpx, "post", forbidden)
    settings = Settings(dashscope_api_key="sk-test", offline_mode=True)
    assert settings.vlm_ready is False
    result = QwenVLMParser(settings=settings).parse_outfit(["demo/sport-1"])
    assert result.outfit.items
    assert calls == []


# ---------------------------------------------------------------------------
# 4. Qwen 请求契约（enable_thinking=false / json_object）与降级重试
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, status_code: int, content: str = "") -> None:
        self.status_code = status_code
        self.text = content
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _vlm_json() -> str:
    return json.dumps({
        "items": [
            {
                "slot": "top",
                "category": "棉质衬衫",
                "color": {"name": "米白", "hex": "#F0EBE1"},
                "material": "棉",
                "fit": "regular",
                "length": "常规",
                "formality": 0.65,
                "style_tags": ["极简"],
                "pattern": "纯色",
                "confidence": 0.9,
                "evidence": "白色有领衬衫",
            },
            {
                "slot": "bottom",
                "category": "直筒西裤",
                "color": {"name": "藏青", "hex": "#1F2A44"},
                "material": "羊毛",
                "fit": "直筒",
                "formality": 3,
                "confidence": 0.85,
            },
        ],
        "confidence": 0.88,
    }, ensure_ascii=False)


def test_qwen_payload_contract(monkeypatch):
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        return _FakeResponse(200, "```json\n" + _vlm_json() + "\n```")

    monkeypatch.setattr(httpx, "post", fake_post)
    settings = Settings(dashscope_api_key="sk-test", offline_mode=False,
                        vlm_model="qwen3.7-plus")
    parser = QwenVLMParser(settings=settings)
    result = parser.parse_outfit(["https://example.test/a.jpg"], "business", "autumn")

    payload = captured["payload"]
    assert captured["url"].endswith("/compatible-mode/v1/chat/completions")
    assert payload["model"] == "qwen3.7-plus"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["enable_thinking"] is False  # 百炼默认开思考，必须显式关
    assert captured["headers"]["Authorization"] == "Bearer sk-test"

    outfit = result.outfit
    assert len(outfit.items) == 2
    assert outfit.items[0].slot == Slot.upper
    assert outfit.items[0].formality == 4  # 0.65 → 0.65*4+1=3.6 → 4（contract 1..5）
    assert outfit.items[0].fit == "regular"
    assert outfit.items[1].fit == "regular"  # 中文别名 → 英文枚举
    assert outfit.occasion == "business"
    assert 0.0 <= outfit.confidence <= 1.0


def test_qwen_retries_once_then_falls_back_to_mock(monkeypatch, tmp_path):
    attempts: list[int] = []

    def failing_post(*args, **kwargs):
        attempts.append(1)
        return _FakeResponse(500, "boom")

    monkeypatch.setattr(httpx, "post", failing_post)
    # 必须给「可解析为真实图片」的 ref，否则会命中 fast-fail 守卫而不发请求
    img = tmp_path / "outfit.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    settings = Settings(dashscope_api_key="sk-test", offline_mode=False)
    result = QwenVLMParser(settings=settings).parse_outfit(
        [str(img)], "date", "summer")

    assert len(attempts) == 2  # 重试 1 次
    assert result.outfit.items  # 降级到 mock 仍有完整结果
    merged = " ".join(result.warnings)
    assert "VLM_PARSE_FAILED_FALLBACK_MOCK" in merged
    assert "VLM_MODEL" in merged
    assert result.outfit.parse_warnings == result.warnings


def test_qwen_fast_fails_without_network_for_demo_refs(monkeypatch):
    """演示标识（非 文件/URL/data URI）必须零网络请求直接降级，避免无谓延迟与计费。"""
    attempts: list[int] = []

    def should_not_be_called(*args, **kwargs):
        attempts.append(1)
        return _FakeResponse(500, "should not reach")

    monkeypatch.setattr(httpx, "post", should_not_be_called)
    settings = Settings(dashscope_api_key="sk-test", offline_mode=False)
    result = QwenVLMParser(settings=settings).parse_outfit(["demo/clash-1"], "date", "summer")

    assert attempts == []                  # 一次网络都没发
    assert result.outfit.items             # 仍然有可用结果（mock）
    assert any("VLM_SKIPPED_NO_FETCHABLE_IMAGE" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# 5. 输入归一化 / 形状
# ---------------------------------------------------------------------------
def test_declared_occasion_and_season_are_respected():
    result = MockParser().parse_outfit(["demo/formal-1"], "正式", "冬季")
    assert result.outfit.occasion == "formal"
    assert result.outfit.season == "winter"


def test_default_occasion_is_unspecified_not_invented():
    result = MockParser().parse_outfit(["demo/commute-1"])
    assert result.outfit.occasion == "unspecified"
    assert result.outfit.season == "unknown"
    assert result.warnings == []


def test_unknown_occasion_records_warning():
    result = MockParser().parse_outfit(["demo/commute-1"], "开会穿什么", None)
    assert result.outfit.occasion == "unspecified"
    assert any(w.startswith("UNKNOWN_OCCASION") for w in result.warnings)


def test_normalizers():
    assert normalize_occasion("通勤") == "business"
    assert normalize_occasion("  Formal ") == "formal"
    assert normalize_occasion(None) is None
    assert normalize_occasion("火星") is None
    assert normalize_season("秋") == "autumn"
    assert normalize_season("unknown") == "unknown"
    assert normalize_season("火星") is None


def test_get_parser_uses_mock_without_key(monkeypatch):
    _reset_settings(monkeypatch)
    parser = get_parser()
    assert isinstance(parser, BaseParser)
    assert isinstance(parser, MockParser)
    assert parser.mode == "offline"


def test_empty_image_refs_rejected():
    with pytest.raises(ValueError):
        MockParser().parse_outfit([], None, None)
    with pytest.raises(ValueError):
        MockParser().parse_outfit(["   "], None, None)
