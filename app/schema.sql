-- Featherless Proxy DB Schema v2
-- Adds users, credits, and admin support

-- Users (for login)
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    credits REAL NOT NULL DEFAULT 0,
    credit_limit REAL NOT NULL DEFAULT 0,  -- 0 = no limit, otherwise max credits that can be used
    created_at REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

-- API keys now belong to users
-- Existing API keys get user_id = NULL (admin-owned)
CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    user_id INTEGER,
    priority INTEGER NOT NULL DEFAULT 2,  -- always P2 for users
    created_at REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Credit transactions (for audit log)
CREATE TABLE IF NOT EXISTS credit_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount REAL NOT NULL,          -- positive = added, negative = used
    reason TEXT NOT NULL,          -- "admin_topup", "request_cost", "refund"
    model TEXT,
    request_id INTEGER,
    balance_after REAL NOT NULL,
    timestamp REAL NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Models (unchanged)
CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    concurrent_cost INTEGER NOT NULL DEFAULT 1,
    input_price REAL NOT NULL DEFAULT 0,
    cached_read_price REAL NOT NULL DEFAULT 0,
    output_price REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

-- Usage logs (add user_id)
CREATE TABLE IF NOT EXISTS usage_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    user_id INTEGER,
    api_key_id INTEGER,
    api_key_name TEXT,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_read_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 2,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    expires REAL NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);