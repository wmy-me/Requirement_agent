-- 013: run 的阶段与断点（对话状态机 §1.2a —— checkpoint 落库）
--
-- 背景：四个 LLM 步骤（extract→retrieve→analyze→risk）跑完、正在流式输出叙事时
-- 客户端断开，**已经花掉 token 算好的结果会被整个丢弃**（append_assistant_message
-- 只在管线全部走完后调用，except CancelledError 只写 run 状态、不落任何中间产物）。
-- 用户重发就是再花一遍钱重算。
--
-- 本轮只做「不丢结果」（B 批）：每个阶段结束把产物写进 checkpoint，stage 推进一格。
-- 续跑（C 批）依据这两个字段跳过已完成阶段，从断点继续。

ALTER TABLE agent_run
    ADD COLUMN IF NOT EXISTS stage TEXT NOT NULL DEFAULT 'queued',
    ADD COLUMN IF NOT EXISTS checkpoint JSONB NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_agent_run_conversation_stage
    ON agent_run (conversation_id, status, updated_at);
