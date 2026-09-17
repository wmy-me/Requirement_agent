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

import pytest

os.environ.setdefault("API_AUTH_TOKEN", "test-api-token")


@pytest.fixture(autouse=True)
def _disable_model_invocation_recording():
    """**整个测试套件默认关闭模型调用记录。**

    B3.1 起 `LLMProvider._observe` 会把每次调用写进 `model_invocation` 表，
    且默认是**自动开启**的（惰性装配，见 `infrastructure/llm/invocation.py`）。

    这在一个到处打真实库、真实 embedding 网关的测试套件里意味着：跑一轮全量测试
    就往开发库写十几行遥测 —— 实测一轮留下 17 行 `run_id` 为 NULL 的孤儿
    （来自那些直接调检索/抽取、没有绑定 run 的测试）。
    它们既不参与断言，又会随时间堆积，让人以为「系统在跑分析」。

    要验记录的测试**自己显式打开**（`set_recorder(收集函数)`），
    这样「哪些测试会写库」是看得见的。
    """
    from requirement_agent.infrastructure.llm.invocation import set_recorder

    set_recorder(None)
    yield
    set_recorder(None)
