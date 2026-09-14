# Requirement Agent

Requirement Agent 是一个基于 Python + FastAPI 的需求管理与分析平台，围绕“需求录入 → 提取 → 分析 → 风险评估 → 审核 → 版本管理 → 审计闭环”这一完整链路展开。

本项目遵循分层设计，核心职责划分如下：

- src/requirement_agent/: 业务核心（api / workers / domain / application / agents / skills / workflows / infrastructure / config / tools）
- migrations/: 数据库迁移与初始化脚本
- tests/: 单元测试、集成测试等
- deploy/: 部署与环境配置
- docx/: 项目文档、任务清单、问题记录等

## 项目目标

- 支持需求源输入与结构化抽取
- 对需求做去重、关联、冲突分析
- 进行质量、变更和技术风险评估
- 支持人工审核与版本变更闭环
- 维护审计日志与业务状态追踪
- 提供内部 Tool 方法（可直接调用的工具层）
- 提供前端工作台用于交互式展示与使用

## 技术栈

- Python 3.12+
- FastAPI
- PostgreSQL + pgvector
- SQLAlchemy
- Pydantic / Pydantic Settings
- LangGraph 风格工作流编排
- OpenAI 兼容 / DeepSeek 兼容 LLM

## 运行环境要求

1. Python 3.12 及以上
2. PostgreSQL 已安装并可用
3. 具备可访问的 LLM API Key（如 DeepSeek / OpenAI 兼容）
4. 项目根目录中存在 `.env` 配置文件

## 快速开始

### 1. 创建虚拟环境

```bash
cd /home/wangmengyang/Software/Requirement_agent/Requirement_agent
python -m venv .venv
source .venv/bin/activate
```

### 2. 安装依赖

```bash
pip install -U pip
pip install -e .
```

### 3. 初始化数据库

确保 PostgreSQL 服务已启动，并配置好 `.env` 中的数据库连接参数。

默认库名为：

```bash
requirement_agent
```

### 4. 启动主服务（前端 + API）

主服务使用 8888 端口：

```bash
cd /home/wangmengyang/Software/Requirement_agent/Requirement_agent
.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8888 --reload
```

访问地址：

- API: http://127.0.0.1:8888
- 前端 UI: http://127.0.0.1:8888/ui
- 健康检查: http://127.0.0.1:8888/health

## 常用接口

### 需求提交

```bash
curl -X POST http://127.0.0.1:8888/api/v1/requirements/submit \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "web",
    "requester_id": "alice",
    "requester_name": "alice",
    "original_text": "用户登录需要支持手机号登录、短信验证码和权限校验。",
    "metadata": {}
  }'
```

### 需求列表

```bash
curl http://127.0.0.1:8888/api/v1/requirements
```

### 数据库健康状态

```bash
curl http://127.0.0.1:8888/api/v1/health/db
```

### LLM 健康状态

```bash
curl http://127.0.0.1:8888/api/v1/health/llm
```

## 核心能力说明

### 1. 需求抽取

抽取 Agent 会将原始需求文本转换为结构化对象，包括：

- 标题
- 摘要
- 标签
- 业务域
- 优先级
- 子需求列表

### 2. 需求分析

分析 Agent 会判断需求与历史需求之间的关系：

- 是否重复
- 是否相关
- 是否冲突
- 是否独立

### 3. 风险评估

风险 Agent 会对需求评估：

- quality_risk
- change_risk
- technical_impact_risk
- confidence

### 4. 审核与版本闭环

审核通过后，系统会拆分为：

- 需求评审记录
- 版本快照
- 审计日志
- 版本更新

### 5. 内部 Tool 方法

工具能力为**可直接调用的普通方法**（`src/requirement_agent/tools/`），
覆盖需求提交、需求检索、审核提交、需求详情 / 版本读取与主需求列表等。

## 目录结构说明

```text
Requirement_agent/
├── src/
│   └── requirement_agent/          # 业务核心包（src-layout，导入名 requirement_agent.*）
│       ├── api/                    # HTTP 接口：app / router / routes / schemas / dependencies
│       ├── workers/                # 后台 Worker 入口（:8200）
│       ├── domain/                 # 领域模型
│       ├── application/            # 应用服务（需求/检索/审核/记忆/决策规则）
│       ├── agents/                 # 智能分析 Agent（extract/analyze/retrieval/risk）
│       ├── skills/                 # Prompt / Skill 执行
│       ├── workflows/              # LangGraph 状态与节点编排
│       ├── infrastructure/         # db / llm / embedding / vector / parser / storage / channels / worker
│       ├── common/ config/         # 公共工具（时间/雪花 id）、配置
│       └── tools/                  # 内部 Tool 方法
├── main.py                         # 主服务入口（:8888）
├── migrations/
├── tests/
├── deploy/
├── docx/
├── static/
├── .env
├── pyproject.toml
├── README.md
└── uv.lock
```

## 常见问题

### 1. 端口被占用

如果启动时报：

```text
Address already in use
```

请检查并清理旧的 uvicorn 进程，再重新启动：

```bash
ss -lntp | grep 8888
ps -ef | grep uvicorn
kill <PID>
```

### 2. 主服务没有按 8888 启动

如果你使用的是：

```bash
uvicorn main:app --reload
```

默认端口依然是 8000。若需要主服务监听 8888，必须明确加上：

```bash
--port 8888
```

## 结论

这个项目已经具备以下基础能力：

- 完整的 Python/FastAPI 项目骨架
- PostgreSQL 数据层与业务表结构
- 内部 Tool 方法层
- LLM 适配器
- 真实 Agent + Skill 结构
- LangGraph 风格工作流
- 审核、版本、审计闭环
- 前端工作台基础可用

后续仍在持续完善真实业务闭环、扩展测试覆盖和系统部署能力。

## 维护建议

- 每次修改关键逻辑，优先运行相关单测
- 确保 `.env` 中不提交真实密钥
- 所有敏感信息统一放在环境变量中
