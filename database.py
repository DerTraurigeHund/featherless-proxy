"""
Featherless Proxy - Database layer v2
Users, credits, API keys, models, usage logs.
"""
import aiosqlite
import time

DB_PATH = "proxy.db"


async def init_db(db_path: str = DB_PATH):
    async with aiosqlite.connect(db_path) as db:
        with open("schema.sql", "r") as f:
            await db.executescript(f.read())
        await db.commit()


# --- Users ---

async def create_user(db_path, username, password_hash, is_admin=0, credits=0, credit_limit=0):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "INSERT INTO users (username, password_hash, is_admin, credits, credit_limit, created_at) VALUES (?,?,?,?,?,?)",
            (username, password_hash, is_admin, credits, credit_limit, time.time())
        )
        await db.commit()
        return {"id": cur.lastrowid, "username": username, "is_admin": is_admin, "credits": credits}


async def get_user_by_username(db_path, username):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM users WHERE username = ? AND enabled = 1", (username,))).fetchone()
        return dict(row) if row else None


async def get_user_by_id(db_path, user_id):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))).fetchone()
        return dict(row) if row else None


async def list_users(db_path):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM users ORDER BY created_at DESC")).fetchall()
        return [dict(r) for r in rows]


async def update_user(db_path, user_id, **kwargs):
    allowed = ("password_hash", "is_admin", "credits", "credit_limit", "enabled")
    async with aiosqlite.connect(db_path) as db:
        for k, v in kwargs.items():
            if k in allowed:
                await db.execute(f"UPDATE users SET {k} = ? WHERE id = ?", (v, user_id))
        await db.commit()


async def delete_user(db_path, user_id):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.execute("DELETE FROM api_keys WHERE user_id = ?", (user_id,))
        await db.commit()


async def deduct_credits(db_path, user_id, amount, reason, model=None, request_id=None):
    """Atomically deduct credits and log the transaction."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT credits FROM users WHERE id = ?", (user_id,))).fetchone()
        if not row:
            return False, 0
        balance = row["credits"] - amount
        await db.execute("UPDATE users SET credits = ? WHERE id = ?", (balance, user_id))
        await db.execute(
            "INSERT INTO credit_transactions (user_id, amount, reason, model, request_id, balance_after, timestamp) VALUES (?,?,?,?,?,?,?)",
            (user_id, -amount, reason, model, request_id, balance, time.time())
        )
        await db.commit()
        return True, balance


async def add_credits(db_path, user_id, amount, reason="admin_topup"):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT credits FROM users WHERE id = ?", (user_id,))).fetchone()
        if not row:
            return False, 0
        balance = row["credits"] + amount
        await db.execute("UPDATE users SET credits = ? WHERE id = ?", (balance, user_id))
        await db.execute(
            "INSERT INTO credit_transactions (user_id, amount, reason, balance_after, timestamp) VALUES (?,?,?,?,?)",
            (user_id, amount, reason, balance, time.time())
        )
        await db.commit()
        return True, balance


async def get_credit_transactions(db_path, user_id=None, limit=50):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        if user_id:
            rows = await (await db.execute(
                "SELECT * FROM credit_transactions WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
                (user_id, limit)
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM credit_transactions ORDER BY timestamp DESC LIMIT ?", (limit,)
            )).fetchall()
        return [dict(r) for r in rows]


# --- API Keys ---

async def create_api_key(db_path, key, name, user_id=None, priority=2):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "INSERT INTO api_keys (key, name, user_id, priority, created_at) VALUES (?,?,?,?,?)",
            (key, name, user_id, priority, time.time())
        )
        await db.commit()
        return {"id": cur.lastrowid, "key": key, "name": name, "user_id": user_id, "priority": priority}


async def get_api_key_by_key(db_path, key):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            """SELECT ak.*, u.credits as user_credits, u.credit_limit as user_credit_limit,
               u.is_admin as user_is_admin, u.enabled as user_enabled
               FROM api_keys ak
               LEFT JOIN users u ON ak.user_id = u.id
               WHERE ak.key = ? AND ak.enabled = 1""", (key,)
        )).fetchone()
        return dict(row) if row else None


async def list_api_keys(db_path, user_id=None):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        if user_id:
            rows = await (await db.execute(
                "SELECT * FROM api_keys WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT ak.*, u.username FROM api_keys ak LEFT JOIN users u ON ak.user_id = u.id ORDER BY created_at DESC"
            )).fetchall()
        return [dict(r) for r in rows]


async def delete_api_key(db_path, key_id):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
        await db.commit()


# --- Models ---

async def create_model(db_path, model_id, display_name, concurrent_cost, input_price, cached_read_price, output_price):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "INSERT INTO models (model_id, display_name, concurrent_cost, input_price, cached_read_price, output_price, created_at) VALUES (?,?,?,?,?,?,?)",
            (model_id, display_name, concurrent_cost, input_price, cached_read_price, output_price, time.time())
        )
        await db.commit()
        return {"id": cur.lastrowid}


async def get_model_by_id(db_path, model_id):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute("SELECT * FROM models WHERE model_id = ?", (model_id,))).fetchone()
        return dict(row) if row else None


async def list_models(db_path):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT * FROM models ORDER BY created_at DESC")).fetchall()
        return [dict(r) for r in rows]


async def delete_model(db_path, model_id):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM models WHERE id = ?", (model_id,))
        await db.commit()


async def update_model(db_path, model_id, **kwargs):
    allowed = ("display_name", "concurrent_cost", "input_price", "cached_read_price", "output_price")
    async with aiosqlite.connect(db_path) as db:
        for k, v in kwargs.items():
            if k in allowed:
                await db.execute(f"UPDATE models SET {k} = ? WHERE id = ?", (v, model_id))
        await db.commit()


# --- Usage Logs ---

async def log_usage(db_path, user_id, api_key_id, api_key_name, model,
                    input_tokens, cached_read_tokens, output_tokens, cache_hit, cost, priority):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO usage_logs (timestamp, user_id, api_key_id, api_key_name, model, input_tokens, cached_read_tokens, output_tokens, cache_hit, cost, priority)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (time.time(), user_id, api_key_id, api_key_name, model, input_tokens, cached_read_tokens, output_tokens,
             1 if cache_hit else 0, cost, priority)
        )
        await db.commit()


async def get_usage_stats(db_path, hours=24, user_id=None):
    cutoff = time.time() - (hours * 3600)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        params = [cutoff]
        where = "WHERE timestamp >= ?"
        if user_id:
            where += " AND user_id = ?"
            params.append(user_id)

        row = await (await db.execute(
            f"""SELECT COUNT(*) as total_requests, SUM(input_tokens) as total_input_tokens,
                SUM(cached_read_tokens) as total_cached_read_tokens, SUM(output_tokens) as total_output_tokens,
                SUM(cost) as total_cost, SUM(cache_hit) as cache_hits, AVG(cost) as avg_cost
               FROM usage_logs {where}""", params
        )).fetchone()

        model_rows = await (await db.execute(
            f"""SELECT model, COUNT(*) as requests, SUM(input_tokens) as input_tokens,
                SUM(cached_read_tokens) as cached_read_tokens, SUM(output_tokens) as output_tokens,
                SUM(cost) as cost, SUM(cache_hit) as cache_hits
               FROM usage_logs {where} GROUP BY model ORDER BY cost DESC""", params
        )).fetchall()

        key_rows = await (await db.execute(
            f"""SELECT api_key_name, COUNT(*) as requests, SUM(input_tokens) as input_tokens,
                SUM(cached_read_tokens) as cached_read_tokens, SUM(output_tokens) as output_tokens,
                SUM(cost) as cost, SUM(cache_hit) as cache_hits
               FROM usage_logs {where} GROUP BY api_key_name ORDER BY cost DESC""", params
        )).fetchall()

        return {
            "overall": dict(row) if row else {},
            "per_model": [dict(r) for r in model_rows],
            "per_key": [dict(r) for r in key_rows],
            "hours": hours,
        }


async def get_recent_logs(db_path, limit=50, user_id=None):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        if user_id:
            rows = await (await db.execute(
                "SELECT * FROM usage_logs WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?", (user_id, limit)
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM usage_logs ORDER BY timestamp DESC LIMIT ?", (limit,)
            )).fetchall()
        return [dict(r) for r in rows]


async def get_timeseries(db_path, hours=24, bucket_minutes=60, user_id=None):
    cutoff = time.time() - (hours * 3600)
    # Clamp granularity: at least 1-minute buckets, and never return more than
    # ~1500 points so a fine interval over a long range can't blow up the payload.
    bucket_minutes = max(1, int(bucket_minutes))
    max_buckets = 1500
    if (hours * 60) / bucket_minutes > max_buckets:
        bucket_minutes = int((hours * 60) / max_buckets) + 1
    bucket_seconds = bucket_minutes * 60
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        params = [bucket_seconds, bucket_seconds, cutoff]
        where = "WHERE timestamp >= ?"
        if user_id:
            where += " AND user_id = ?"
            params.append(user_id)
        rows = await (await db.execute(
            f"""SELECT CAST(timestamp / ? AS INTEGER) * ? as bucket,
                COUNT(*) as requests, SUM(input_tokens) as input_tokens,
                SUM(cached_read_tokens) as cached_read_tokens, SUM(output_tokens) as output_tokens,
                SUM(cost) as cost, SUM(cache_hit) as cache_hits
               FROM usage_logs {where} GROUP BY bucket ORDER BY bucket ASC""", params
        )).fetchall()

    # Gap-fill: the GROUP BY only yields buckets that contain data. Emit a
    # continuous series across the whole range (empty buckets = zeros) so the
    # chart timeline is evenly spaced instead of collapsing to a few points.
    data = {int(r["bucket"]): dict(r) for r in rows}
    now = time.time()
    start = int(cutoff // bucket_seconds) * bucket_seconds
    end = int(now // bucket_seconds) * bucket_seconds
    series = []
    b = start
    while b <= end:
        series.append(data.get(b, {
            "bucket": b, "requests": 0, "input_tokens": 0, "cached_read_tokens": 0,
            "output_tokens": 0, "cost": 0, "cache_hits": 0,
        }))
        b += bucket_seconds
    return series