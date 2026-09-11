"""根入口：薄包装，转发到统一应用入口 src.requirement_agent.api.app。

- `python main.py` → 在 8888 端口启动统一 app（reload）。
- `import main; main.app` → 与 `from src.requirement_agent.api.app import app` 是同一实例。
"""

import uvicorn

from src.requirement_agent.api.app import app

__all__ = ["app"]


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8888, reload=True)
