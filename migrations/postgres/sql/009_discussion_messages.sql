-- Additive migration: existing tables and relationships are unchanged.
CREATE TABLE discussion_messages (
    id UUID PRIMARY KEY,
    match_id UUID NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX discussion_messages_history
    ON discussion_messages (match_id, user_id, chat_id, created_at, id);

-- There is one forward-only discussion per match and Telegram user/chat.
CREATE UNIQUE INDEX discussion_messages_one_start
    ON discussion_messages (match_id, user_id, chat_id)
    WHERE metadata->>'kind' = 'context';
