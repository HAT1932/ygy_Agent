"""运行时配置。全部来自环境变量；缺 key 时自动进入离线模式（demo 仍可全链路跑通）。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """极简 .env 读取（不引第三方依赖）。已存在的环境变量优先。"""
    for name in (".env.local", ".env"):
        p = ROOT / name
        if not p.exists():
            continue
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


_load_dotenv()


class Settings(BaseModel):
    app_env: str = "dev"
    log_level: str = "INFO"

    # 规则集
    ruleset_version: str = "rs-2026.10.1"
    rules_dir: Path = ROOT / "rules"

    # VLM（阿里云百炼，OpenAI 兼容模式）
    dashscope_api_key: str | None = None
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    vlm_model: str = "qwen3.7-plus"
    vlm_enable_thinking: bool = False

    # 文本 LLM（DeepSeek）
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"

    # Embedding
    embedding_mode: str = "hash"     # hash | dashscope
    embedding_dim: int = 256

    # 锚点召回相似度下限。契约默认 0.60（语义 embedding）；hash 模式余弦区间被压缩，
    # 实测分布约 0.10–0.40，因此该参数必须按 embedding 后端标定，不能写死。
    anchor_min_sim: float = 0.60

    offline_mode: bool = True

    @property
    def vlm_ready(self) -> bool:
        return bool(self.dashscope_api_key) and not self.offline_mode

    @property
    def llm_ready(self) -> bool:
        return bool(self.deepseek_api_key) and not self.offline_mode


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_env=os.getenv("APP_ENV", "dev"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        ruleset_version=os.getenv("RULESET_VERSION", "rs-2026.10.1"),
        dashscope_api_key=os.getenv("DASHSCOPE_API_KEY") or None,
        dashscope_base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        vlm_model=os.getenv("VLM_MODEL", "qwen3.7-plus"),
        vlm_enable_thinking=_env_bool("VLM_ENABLE_THINKING", False),
        deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None,
        deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        llm_model=os.getenv("LLM_MODEL", "deepseek-flash"),
        embedding_mode=os.getenv("EMBEDDING_MODE", "hash"),
        embedding_dim=int(os.getenv("EMBEDDING_DIM", "256")),
        anchor_min_sim=float(os.getenv("ANCHOR_MIN_SIM", "0.60")),
        offline_mode=_env_bool("OFFLINE_MODE", True),
    )
