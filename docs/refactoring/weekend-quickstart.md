# 周末新机快速上手 & 下一步计划

> 面向：换到另一台电脑继续开发
> 依据：当前仓库状态（`master`，HEAD = 全部已推送）
> 一句话：**`git clone` + 拷贝 `.env` + `pip install -e .` + 跑 7 个迁移 = 能跑。**

---

## 0. 现状：代码已全在 GitHub

```
远端：https://github.com/wmy-me/Requirement_agent.git
本地 master == origin/master（无未推送提交），工作区干净
```
→ 新电脑 `git clone` 即拿到**全部代码**（含 `migrations/`、`tests/`、`docs/`、`.idea/runConfigurations/`）。

---

## 1. 前置环境

| 依赖 | 要求 | 说明 |
|---|---|---|
| Python | **3.12+** | `pyproject.toml` 要求 `>=3.12` |
| PostgreSQL | 16 + **pgvector 扩展** | 最省事：直接用镜像 `pgvector/pgvector:pg16` |
| MinIO | 可选 | 不装/连不上时，附件自动降级写到本地 `storage/uploads/` |
| LLM | DeepSeek（chat）+ oneapi Doubao（embedding） | key 在 `.env` |

---

## 2. 五步跑起来

```bash
# 0) 拿代码
git clone https://github.com/wmy-me/Requirement_agent.git
cd Requirement_agent

# 1) ★ 放入 .env（必须，见 §3）
#    从旧机器拷贝到项目根目录

# 2) 建虚拟环境 + 安装（src-layout，必须 -e 可编辑安装）
python -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e .
#（若用 uv：uv sync）

# 3) 建库 + 跑全部迁移（001~007，缺一不可）
createdb requirement_agent
for f in migrations/0*.sql; do psql -d requirement_agent -f "$f"; done

# 4) 启动主服务
.venv/bin/python main.py          # → http://0.0.0.0:8888
# 或：.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8888 --reload
```

访问：
- 前端 UI：`http://127.0.0.1:8888/ui`
- 健康检查：`http://127.0.0.1:8888/health`、`/api/v1/health/db`、`/api/v1/health/llm`

**说明**：后台 outbox 消费循环**随主服务一起启动**（lifespan 内线程），所以不必单独跑 worker。Worker（`:8200`）是可选独立入口。

---

## 3. ★ 必须额外带的文件（`git clone` 拿不到）

`.gitignore` 忽略、因此**不在仓库里**的文件：

| 文件/目录 | 必须？ | 说明 |
|---|---|---|
| **`.env`** | ✅ **最关键** | DB 口令、MinIO、DeepSeek、Embedding 的 key 全在此；**不带它起不来**。手动拷贝到项目根 |
| `docx/` | 建议 | 设计文档 + 审计报告（续开发要用） |
| `storage/uploads/` | 可选 | 本地回退的上传件（用 MinIO 则不需要） |
| `.venv/` | ❌ 别带 | 平台相关，新机重建 |
| `.idea/`（除 runConfigurations） | ❌ 别带 | IDE 个人配置；运行配置已入库会自动带上 |

`.env` 需要的键（值从旧机拷贝，**勿泄露**）：
```
POSTGRES_HOST/PORT/USER/PASSWORD/DB
MINIO_ENDPOINT/ACCESS_KEY/SECRET_KEY
DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
EMBEDDING_BASE_URL / EMBEDDING_MODEL / EMBEDDING_DIMENSION / EMBEDDING_API_KEY
API_AUTH_TOKEN / API_ACTOR_ID
```

---

## 4. 想把现有数据也带过去（可选）

```bash
# 旧机器：导出
pg_dump -Fc requirement_agent > reqagent.dump

# 新机器：先建空库 + 跑迁移，再导入
pg_restore -d requirement_agent reqagent.dump
```
（若之前用本地回退存附件，再把 `storage/` 一起拷过去。用 MinIO 则数据在 MinIO 里。）

---

## 5. PyCharm 一次性设置

1. `git clone` 后 `Open` 项目；
2. **设置解释器**：Settings → Project → Python Interpreter → 选新机 `.venv/bin/python`（每台机器路径不同，IDE 会提示）；
3. 源码根随 `.iml` 一起从仓库带上（`src` 为源根，匹配 src-layout），**无需手动配**；
4. 运行配置已入库，clone 即可见：
   - **主服务 (FastAPI :8888)** → `main.py`
   - **后台 Worker (:8200)** → `apps/worker/__main__.py`

---

## 6. 下一步计划（阶段 2：LLM / Agent / Prompt / Skill / Embedding / 检索配置整改）

依据 `docx/LLM_Agent_Prompt_Skill_审计报告_2026-09-11.md`：

| 优先级 | 事项 |
|---|---|
| P1 | **Prompt 去重**：三个 Skill 各自内联 prompt，`skills/prompts.py` 未被引用 → 收敛为单一事实源 + 版本号 |
| P1 | **`.env` 未生效的 `OPENAI_*`**（provider=deepseek 不读取）→ 清理或启用 |
| P1 | **`analysis_mode`**（strict/balanced/broad）是被接受但**从未使用**的死参数 → 接线或移除 |
| P2 | **模型参数透传**（temperature / timeout / retry / 退避）——当前 Provider 一个都不传 |
| P2 | **LLM 可观测性**：调用计数/耗时/token/错误结构化日志 |
| P2 | **向量检索**：3 张表 4096 维**无索引**（pgvector 上限 2000，靠精确扫描）→ 规划模型/降维 |

### 阶段 1 遗留待决项（`docs/refactoring/phase-1-review-packet.md` §8）
1. `apps/`（api 薄壳 + worker）是否也迁入 `requirement_agent/workers/`？
2. `requirement_key`（`REQ-000001`）是否去序列化？
3. `/health` 重复注册、OpenAPI tags 双层重复 —— 是否授权修正（会变更 OpenAPI）？

---

## 7. 常用命令速查

```bash
# 跑测试
.venv/bin/python -m pytest tests/unit -q     # 期望 42 passed / 2 skipped
.venv/bin/python -m pytest -q                # 期望 43 passed / 2 skipped

# 编译检查
.venv/bin/python -m compileall -q src apps main.py scripts tests

# 提交需求（无鉴权，直接 POST）
curl -X POST http://127.0.0.1:8888/api/v1/requirements/submit \
  -H "Content-Type: application/json" \
  -d '{"source_type":"web","requester_id":"u1","requester_name":"张三","original_text":"……需求正文……"}'

# 审核通过
curl -X POST http://127.0.0.1:8888/api/v1/reviews/submit \
  -H "Content-Type: application/json" \
  -d '{"source_id": <id>, "decision": "approved", "reviewer_name": "需求负责人"}'

# 重度/存量数据重索引（embedding 重建）
.venv/bin/python -m scripts.reindex_embeddings
```

---

## 8. 排障速查

| 现象 | 原因 / 处理 |
|---|---|
| 本地 `curl 127.0.0.1:8888` 返回 **502** | 多半是**本机代理**（`http_proxy`）在拦截 → 用 `curl --noproxy '*'`，或 `unset http_proxy https_proxy HTTP_PROXY` |
| 应用起不来 / 报 DB 连接错 | 检查 `.env` 的 `POSTGRES_*`；确认已建库并跑完迁移 001~007 |
| 报 `ModuleNotFoundError: requirement_agent` | 未做可编辑安装 → `.venv/bin/pip install -e .` |
| 上传的文档在 MinIO 找不到 | 检查是走 **`/requirements/ingest`**（会存）还是**对话附件**（现已改为也存）；看 `/api/v1/health/llm` 与 MinIO 连通性 |
| 审核返回 **409** | 该 source 当前不是 `pending_review`（已通过/退回）→ 刷新待办列表再操作 |
| 向量检索命中为 0 | 确认 `requirement_embedding` 有数据（审核通过后由 outbox 消费生成）；可跑 `scripts/reindex_embeddings` |
