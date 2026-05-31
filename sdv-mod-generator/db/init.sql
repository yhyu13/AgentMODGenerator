-- SDV Mod Generator — Database Schema
-- PostgreSQL DDL

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    discord_id VARCHAR(64) UNIQUE,
    display_name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Mod requests table
CREATE TABLE IF NOT EXISTS mod_requests (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(64) UNIQUE NOT NULL,
    user_id VARCHAR(64),
    prompt TEXT NOT NULL,
    phase VARCHAR(32) NOT NULL DEFAULT 'p1_shop_channel',
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    generators JSONB DEFAULT '[]',
    hint JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Mod outputs table (generated files)
CREATE TABLE IF NOT EXISTS mod_outputs (
    id SERIAL PRIMARY KEY,
    request_id VARCHAR(64) UNIQUE NOT NULL REFERENCES mod_requests(request_id) ON DELETE CASCADE,
    zip_key VARCHAR(512),
    zip_url TEXT,
    files_preview JSONB DEFAULT '[]',
    t1_errors JSONB DEFAULT '[]',
    t2_feedback TEXT,
    t2_score INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Mod history table (for user context/memory)
CREATE TABLE IF NOT EXISTS mod_history (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(64),
    request_id VARCHAR(64) REFERENCES mod_requests(request_id) ON DELETE SET NULL,
    prompt TEXT NOT NULL,
    summary TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_mod_requests_user_id ON mod_requests(user_id);
CREATE INDEX IF NOT EXISTS idx_mod_requests_status ON mod_requests(status);
CREATE INDEX IF NOT EXISTS idx_mod_requests_request_id ON mod_requests(request_id);
CREATE INDEX IF NOT EXISTS idx_mod_history_user_id ON mod_history(user_id);
CREATE INDEX IF NOT EXISTS idx_mod_outputs_request_id ON mod_outputs(request_id);