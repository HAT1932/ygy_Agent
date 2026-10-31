"""AI 穿搭顾问 · Demo 启动脚本。

用法：
    python run.py                  # 默认 http://127.0.0.1:8000（前端在 /）
    python run.py --port 9000      # 换端口
    python run.py --host 0.0.0.0   # 局域网可访问（真机演示）

无任何 API key 时自动进入离线模式（mock 解析 + 模板文案），全链路仍可跑通。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AI 穿搭顾问 Demo 服务")
    p.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"),
                   help="监听地址（默认 127.0.0.1）")
    p.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")),
                   help="监听端口（默认 8000）")
    p.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "info"),
                   help="uvicorn 日志级别（默认 info）")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--offline", dest="offline", action="store_true", default=None,
                      help="强制离线：mock 解析 + 模板文案，零网络请求")
    mode.add_argument("--online", dest="offline", action="store_false",
                      help="强制在线：使用 .env 里的 key 调真实 VLM/LLM")
    args = p.parse_args(argv)
    # 契约允许端口被环境覆盖，但显式参数应优先
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # 允许直接 `python run.py` 时把仓库根加入 import 路径
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    # 不强制写死 OFFLINE_MODE：以 .env / 环境变量为准，命令行开关优先级最高。
    # （早期版本无条件 setdefault(true)，会导致填了 key 也走离线，属于隐蔽坑。）
    if args.offline is True:
        os.environ["OFFLINE_MODE"] = "true"
    elif args.offline is False:
        os.environ["OFFLINE_MODE"] = "false"

    try:
        import uvicorn
    except ImportError:  # pragma: no cover - 依赖缺失时给出可操作提示
        print("缺少依赖 uvicorn，请先执行：pip install -r requirements.txt", file=sys.stderr)
        return 1

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    print("=" * 62)
    print("  AI 穿搭顾问 Agent · Demo")
    print(f"  前端页面   {url}/")
    print(f"  API 文档   {url}/docs")
    print(f"  健康检查   {url}/v1/health")
    print("=" * 62)

    uvicorn.run(
        "src.api.app:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        reload=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
