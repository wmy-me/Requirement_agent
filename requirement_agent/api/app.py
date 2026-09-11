"""顶层兼容入口：转发统一应用到物理实现 src.requirement_agent.api.app。

`from requirement_agent.api.app import app` 与 `from src.requirement_agent.api.app import app`
得到**同一个** FastAPI 实例（不重复构建、不重复注册路由）。
"""

from src.requirement_agent.api.app import app, create_app

__all__ = ["app", "create_app"]
