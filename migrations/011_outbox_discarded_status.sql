-- 011: outbox_event.status 增加 'discarded'（死信的人工放弃态）
--
-- 背景：死信此前只有两种归宿 —— 一直躺着，或由运维手工 UPDATE 回 pending。
-- 没有任何「放弃」语义，既无法记录「这条我看过了、决定不处理」，也无法把它从死信
-- 列表里摘掉，于是死信列表只增不减。
--
-- 值域里原有的 'sent' / 'failed' 从未被任何代码写入过（历史遗留），这里不挪用它们，
-- 另加语义明确的 'discarded'。它是**终态**：claim_pending 只认 pending 与卡死的
-- processing，因此不会被重新认领执行。

ALTER TABLE outbox_event DROP CONSTRAINT IF EXISTS outbox_event_status_check;

ALTER TABLE outbox_event
    ADD CONSTRAINT outbox_event_status_check
    CHECK (status IN (
        'pending', 'processing', 'sent', 'failed',
        'completed', 'dead_letter', 'discarded'
    ));
