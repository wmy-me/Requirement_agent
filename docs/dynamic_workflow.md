# 动态需求分析图

分析图由 `workflows.graphs.build_analysis_graph()` 构造；正式写入仍只由
`ReviewService.submit_decision()` 的人工审核事务执行。

```text
START → router ──普通问答──→ answer → END
                 ├─信息不足─→ clarify → END
                 └─需求材料──→ extract → router → retrieve → router
                                      → analyze → router → risk → router
                                      → prepare_review → decide → router → END
```

`router` 的动作集合固定为 `answer`、`clarify`、`extract`、`retrieve`、`analyze`、
`risk`、`prepare_review`、`finish`。如果配置 `MODEL_ROUTES` 的 `router` 项，模型只能
返回 JSON 建议；Pydantic 和 Python 前置条件会复核它。非法 JSON、模型异常/超时、跳过
检索或风险的建议均回退为确定性计划，并在 `error_state` 与 `route_selected` 事件中留痕。

`prepare_review` 至少要求 `extract/retrieve/analyze/risk` 均完成；因此没有检索证据时，
模型无法跳过检索并声称冲突。路由上限为 12，图递归预算为 32，另有 120 秒的图内时限。

持久化边界：来源分析的最终路由轨迹在 `requirement_source.metadata.routing`，运行事件在
`agent_run_event`；对话暂停/恢复继续使用既有 `agent_run.checkpoint`，本改造没有启用
LangGraph checkpointer，避免两个可恢复状态来源相互冲突。

澄清补充的失效规则：暂停/恢复没有改变 `source_text` 时可复用已完成节点；用户补充会
拼入新的 `source_text`，因此 `extract` 以及其依赖的候选、关系分析、风险和待审决策全部
失效并重算。原因是当前抽取器不支持可证明安全的字段级增量合并。被补充消息替代的
`waiting_clarification` run 会标为 `cancelled`，其 checkpoint 留作审计，不会被下一轮误取。

## 配置

默认不额外调用路由模型，使用安全的确定性路由。要启用模型建议，在现有 JSON 配置中加入：

```json
{
  "router": {"provider": "deepseek", "model": "deepseek-chat"}
}
```

即使启用该项，`DEEPSEEK_API_KEY`（或 `OPENAI_API_KEY`）和对应 Base URL 仍须按项目既有
LLM 配置提供；未配置或失败时会退回规则路由。
