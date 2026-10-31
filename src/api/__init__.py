"""HTTP API 层（FastAPI）。

对外只暴露一个应用工厂：``src.api.app.create_app`` 与模块级 ``app``。
唯一业务依赖是 ``src.pipeline.get_service()``，API 层不实现任何业务逻辑。
"""
from .app import app, create_app

__all__ = ["app", "create_app"]
