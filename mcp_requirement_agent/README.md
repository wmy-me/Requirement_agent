# 需求管理 MCP 服务（最小可用版）

本目录提供一个最小可运行的 MCP 服务，对接 PostgreSQL + pgvector 的需求管理表结构。

## 1) 安装依赖

```bash
cd mcp_requirement_agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

注意：

- 不要使用 Python 2 运行（如果 `python --version` 是 2.x，请改用 `python3` 或先激活 `.venv`）。
- 建议先激活 `.venv`，再执行 `python server.py`。

## 2) 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`：

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres
MCP_TRANSPORT=streamable-http
MCP_HOST=0.0.0.0
MCP_PORT=8000
FASTMCP_CHECK_FOR_UPDATES=off
EMBED_MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
```

Embedding 说明：

- `sentence-transformers` 是可选依赖。
- 若未安装，MCP 会自动使用本地 deterministic fallback 向量（可用于演示，但语义效果弱于真实 embedding 模型）。

## 3) 启动服务

### streamable-http（推荐，便于对接 Dify）

```bash
source .venv/bin/activate
FASTMCP_CHECK_FOR_UPDATES=off python server.py
```

启动后默认地址：

- `http://0.0.0.0:8000/mcp`

### stdio（本地 MCP 客户端调试）

```bash
MCP_TRANSPORT=stdio python server.py
```

## 4) 已暴露工具列表

- health_check
- get_history_requirements
- get_master_requirements
- get_requirement_versions
- search_similar_requirements
- search_similar_requirements_by_text
- create_master_requirement
- create_requirement_version
- update_master_requirement
- append_audit_log
- upsert_knowledge
- upsert_knowledge_by_text

## 5) 工具入参说明（重点）

- `search_similar_requirements`：需要传 `query_vector`（`list[float]`）。
- `search_similar_requirements_by_text`：只需传纯文本，MCP 内部自动做 embedding。
- `upsert_knowledge`：需要传 `embedding`（`list[float]`）。
- `upsert_knowledge_by_text`：只需传文本字段，MCP 内部自动做 embedding。

## 6) Dify 接入（新增）

在 Dify 中添加 MCP 工具源时：

- MCP URL 填：`http://<你的主机IP>:8000/mcp`
- 若 Dify 与 MCP 同机，先尝试：`http://127.0.0.1:8000/mcp`
- 若 Dify 跑在 Docker 容器内，`127.0.0.1` 通常指向容器自身，请改成宿主机 IP（例如 `172.17.0.1` 或你的局域网 IP）

推荐先联调这 3 个工具：

- `health_check`
- `get_master_requirements`
- `search_similar_requirements_by_text`

## 7) 常见问题

1. 报错 `ImportError: Using SOCKS proxy, but the 'socksio' package is not installed`

- 原因：FastMCP 启动时做在线版本检查，触发代理链路。
- 处理：确保 `.env` 中有 `FASTMCP_CHECK_FOR_UPDATES=off`。

2. 报错 `SyntaxError` 且指向函数 `->` 注解

- 原因：用了 Python 2 解释器。
- 处理：使用 `python3` 或激活 `.venv` 后再运行。

3. 启动测试出现 `exit_code=124`

- 含义：通常是用了 `timeout` 命令做短时验证，被主动终止。
- 不是服务异常。
