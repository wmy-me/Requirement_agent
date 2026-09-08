ALTER TABLE outbox_event
    ADD COLUMN IF NOT EXISTS last_error TEXT,
    ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;

ALTER TABLE outbox_event
    DROP CONSTRAINT IF EXISTS outbox_event_status_check;

ALTER TABLE outbox_event
    ADD CONSTRAINT outbox_event_status_check
    CHECK (status IN ('pending', 'processing', 'sent', 'failed', 'completed', 'dead_letter'));
