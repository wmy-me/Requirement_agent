"""requirement_agent：正式业务包骨架（重构目标命名空间）。

本包是「项目结构重组」第 1 阶段建立的骨架：只创建命名空间与包结构，
**不迁移代码、不转发旧模块、不改任何现有 import**。

目标模块布局：

    requirement_agent/
        api/              # HTTP/FastAPI 接口
        tools/            # 内部 Tool 方法（可直接调用）
        workers/          # 后台任务/消费循环
        domain/           # 领域模型
        application/      # 应用服务/用例/事务编排
        agents/           # 智能分析 Agent
        skills/           # Prompt/Schema/规则/Skill 执行
        workflows/        # LangGraph 状态与节点编排
        infrastructure/   # 数据库/LLM/向量/存储/渠道实现
        config/           # 配置

迁移策略与完整映射见：
- docs/refactoring/path-mapping.md
- docs/refactoring/compatibility-plan.md

迁移期间旧路径（src.domain / src.agents / ...）保持可用，作为兼容入口。
"""
