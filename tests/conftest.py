"""pytest 全局装配。

**为什么 `API_AUTH_TOKEN` 要在这里设，而不是散在各个测试文件里。**

B1 起 API 需要鉴权，而 token 是 `settings` 单例在**模块导入期**读进内存的。
此前每个测试文件各自 `os.environ.setdefault("API_AUTH_TOKEN", ...)` —— 那只有在
「某个设了它的文件恰好最先被 pytest 导入」时才成立。收集顺序一变（改名、加文件、
按目录跑），settings 就可能在没有 token 的情况下构造出来，表现为**一大片莫名其妙的 401**。

`conftest.py` 由 pytest 在**所有测试模块之前**导入，是唯一确定的时机点。

值取 `test-api-token`，与各文件里的 `client` 默认头一致；它走的是
`API_AUTH_TOKEN` 这个**兼容入口**（等同于一个 admin token），
所以既有测试不需要关心角色。要测 401/403 请另建不带头的 TestClient
（见 `tests/integration/test_api_auth.py`）。
"""

from __future__ import annotations

import os

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")
