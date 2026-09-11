"""requirement_agent：正式业务包（项目结构重组目标）。

模块布局：

    requirement_agent/
        api/              # HTTP/FastAPI 接口（app / router / routes / schemas / dependencies）
        tools/            # 内部 Tool 方法（可直接调用）
        workers/          # 后台任务入口
        domain/           # 领域模型
        application/      # 应用服务 / 用例 / 事务编排
        agents/           # 智能分析 Agent
        skills/           # Prompt / Schema / 规则 / Skill 执行
        workflows/        # LangGraph 状态与节点编排
        infrastructure/   # 数据库 / LLM / 向量 / 存储 / 渠道实现
        common/           # 公共小工具（时间 / 雪花 id）
        config/           # 配置

原 `src/{domain,application,agents,skills,graph,infrastructure,config,common}` 已全部迁入本包。
"""
