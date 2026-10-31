"""双塔测试：维度/L2、固定 seed 投影可复现、跨进程稳定、画像向量与向量检索。"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.contracts import Outfit, StyleProfile
from src.parsing import MockParser
from src.profiling import build_profile
from src.towers import (
    DIM,
    IMG_DIM,
    PROJECTION_SEED,
    STRUCT_DIM,
    STRUCT_RAW_DIM,
    TowerEncoder,
    VectorStore,
    projection_sha256,
    struct_features,
)


def _outfit(ref: str = "demo/commute-1", occasion: str = "business") -> Outfit:
    return MockParser().parse_outfit([ref], occasion, "autumn").outfit


def _norm(vec: list[float]) -> float:
    return math.sqrt(sum(v * v for v in vec))


# ---------------------------------------------------------------------------
# 1. 维度 / L2 / 确定性
# ---------------------------------------------------------------------------
def test_dim_is_256_and_l2_normalized():
    encoder = TowerEncoder()
    vec = encoder.encode_outfit(_outfit())
    assert len(vec) == 256
    assert abs(_norm(vec) - 1.0) < 1e-9
    assert all(isinstance(v, float) for v in vec)


def test_same_outfit_same_vector_and_different_outfits_differ():
    encoder = TowerEncoder()
    outfit = _outfit("demo/clash-1", "date")
    first = encoder.encode_outfit(outfit)
    second = TowerEncoder().encode_outfit(outfit)
    assert first == second

    other = encoder.encode_outfit(_outfit("demo/sport-1", "sport"))
    cosine = sum(a * b for a, b in zip(first, other))
    assert cosine < 0.999


def test_vector_depends_on_content_not_outfit_id():
    encoder = TowerEncoder()
    outfit = _outfit("demo/mono-1", "business")
    renamed = outfit.model_copy(update={"outfit_id": "oft_renamed"})
    assert encoder.encode_outfit(outfit) == encoder.encode_outfit(renamed)


def test_img_and_struct_blocks_are_fixed():
    encoder = TowerEncoder()
    cfg = encoder.config
    assert cfg["img_dim"] == IMG_DIM == 160
    assert cfg["struct_dim"] == STRUCT_DIM == 96
    assert cfg["img_dim"] + cfg["struct_dim"] == cfg["dim"] == DIM == 256
    assert struct_features(_outfit()).shape == (STRUCT_RAW_DIM,)


# ---------------------------------------------------------------------------
# 2. config / 固定 seed 投影可复现（B-06）
# ---------------------------------------------------------------------------
def test_config_contract_keys_and_values():
    cfg = TowerEncoder().config
    assert set(cfg) == {"mode", "dim", "projection_sha256", "img_dim",
                        "struct_dim", "alpha", "beta"}
    assert cfg["mode"] == "hash"
    assert cfg["alpha"] == pytest.approx(0.6)
    assert cfg["beta"] == pytest.approx(0.4)
    assert len(cfg["projection_sha256"]) == 64
    int(cfg["projection_sha256"], 16)  # hex


def test_projection_sha256_is_stable_across_instances():
    first = TowerEncoder()
    second = TowerEncoder()
    assert first.projection_sha256 == second.projection_sha256
    assert projection_sha256() == first.projection_sha256
    assert np.array_equal(first.projection_matrix(), second.projection_matrix())


def test_projection_matrix_reproducible_from_fixed_seed():
    """按 docs/02 §6.2.3 的约定（seed=20261031, N(0, 1/raw)）重算，应得到同一 sha256。"""
    encoder = TowerEncoder()
    rng = np.random.default_rng(PROJECTION_SEED)
    manual = rng.standard_normal((STRUCT_DIM, STRUCT_RAW_DIM)) / math.sqrt(STRUCT_RAW_DIM)
    manual = np.ascontiguousarray(manual, dtype="<f8")
    header = (f"seed={PROJECTION_SEED};img_dim={IMG_DIM};struct_dim={STRUCT_DIM};"
              f"raw_dim={STRUCT_RAW_DIM};mode=hash").encode("ascii")
    expected = hashlib.sha256(header + b"|" + manual.tobytes()).hexdigest()
    assert encoder.projection_sha256 == expected
    assert np.allclose(encoder.projection_matrix(), manual)


def test_projection_sha256_stable_across_processes():
    code = (
        "import json;"
        "from src.towers import TowerEncoder;"
        "print(json.dumps(TowerEncoder().config, sort_keys=True))"
    )
    outputs = []
    for hash_seed in ("0", "98765"):
        env = dict(os.environ)
        env.update({"PYTHONHASHSEED": hash_seed, "PYTHONIOENCODING": "utf-8"})
        completed = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                                   env=env, capture_output=True, text=True, timeout=120)
        assert completed.returncode == 0, completed.stderr
        outputs.append(json.loads(completed.stdout.strip().splitlines()[-1]))
    assert outputs[0] == outputs[1]


# ---------------------------------------------------------------------------
# 3. 画像向量
# ---------------------------------------------------------------------------
def _profile() -> StyleProfile:
    refs = ("demo/commute-1", "demo/date-1", "demo/mono-1")
    outfits = [MockParser().parse_outfit([ref], "business", "autumn").outfit
               for ref in refs]
    return build_profile(outfits)


def test_encode_profile_is_256_l2_and_deterministic():
    encoder = TowerEncoder()
    profile = _profile()
    first = encoder.encode_profile(profile)
    second = TowerEncoder().encode_profile(profile)
    assert len(first) == 256
    assert abs(_norm(first) - 1.0) < 1e-9
    assert first == second


def test_encode_profile_uses_preference_vector():
    encoder = TowerEncoder()
    profile = _profile()
    assert profile.preference_vector is not None
    assert len(profile.preference_vector) == 256
    expected = [v / _norm(profile.preference_vector) for v in profile.preference_vector]
    assert np.allclose(encoder.encode_profile(profile), expected, atol=1e-12)


def test_encode_profile_without_preference_vector_degrades_gracefully():
    encoder = TowerEncoder()
    profile = _profile().model_copy(update={"preference_vector": None})
    vec = encoder.encode_profile(profile)
    assert len(vec) == 256
    assert abs(_norm(vec) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# 4. VectorStore
# ---------------------------------------------------------------------------
def test_vector_store_search_orders_by_cosine_and_ties_by_id():
    encoder = TowerEncoder()
    outfits = {
        "oft_c": _outfit("demo/commute-1"),
        "oft_d": _outfit("demo/date-1"),
        "oft_s": _outfit("demo/sport-1"),
    }
    store = VectorStore()
    for outfit_id, outfit in outfits.items():
        store.add(outfit_id, encoder.encode_outfit(outfit))

    query = encoder.encode_outfit(outfits["oft_c"])
    results = store.search(query, k=3)
    assert [rid for rid, _ in results][0] == "oft_c"
    similarities = [sim for _, sim in results]
    assert similarities == sorted(similarities, reverse=True)
    assert all(-1.0 <= sim <= 1.0 for sim in similarities)
    assert abs(similarities[0] - 1.0) < 1e-9  # 自己与自己
    assert len(store.search(query, k=99)) == 3
    assert store.search(query, k=0) == []


def test_vector_store_overwrites_and_rejects_dimension_mismatch():
    store = VectorStore()
    store.add("oft_1", [1.0, 0.0, 0.0])
    store.add("oft_1", [0.0, 1.0, 0.0])
    assert len(store) == 1
    assert store.get("oft_1") == [0.0, 1.0, 0.0]

    with pytest.raises(ValueError):
        store.add("oft_2", [1.0, 0.0])
    with pytest.raises(ValueError):
        store.search([1.0, 0.0], k=1)


def test_vector_store_tie_break_is_deterministic():
    store = VectorStore()
    store.add("oft_b", [1.0, 0.0])
    store.add("oft_a", [1.0, 0.0])
    assert [rid for rid, _ in store.search([1.0, 0.0], k=2)] == ["oft_a", "oft_b"]
