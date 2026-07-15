"""
Featherless Proxy - Database layer v2
Users, credits, API keys, models, usage logs.
"""
import aiosqlite
import time
from pathlib import Path

DB_PATH = "proxy.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


async def init_db(db_path: str = DB_PATH):
    async with aiosqlite.connect(db_path) as db:
        with open(SCHEMA_PATH, "r") as f:
            await db.executescript(f.read())
        await db.commit()
        await _run_migrations(db)


async def _run_migrations(db):
    """Lightweight schema/data migrations tracked via SQLite PRAGMA user_version."""
    row = await (await db.execute("PRAGMA user_version")).fetchone()
    version = row[0] if row else 0
    if version < 1:
        # Promote all existing P2 API keys to P3 as part of introducing P1-P4.
        await db.execute("UPDATE api_keys SET priority = 3 WHERE priority = 2")
        await db.execute("PRAGMA user_version = 1")
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


async def update_api_key(db_path, key_id, **kwargs):
    allowed = ("name", "priority", "enabled", "user_id")
    async with aiosqlite.connect(db_path) as db:
        for k, v in kwargs.items():
            if k in allowed:
                await db.execute(f"UPDATE api_keys SET {k} = ? WHERE id = ?", (v, key_id))
        await db.commit()


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


async def get_leaderboard(db_path, hours=None, limit=50):
    """Aggregate usage per user for the cross-user leaderboard/comparison view.

    hours=None (falsy) means all-time — no timestamp filter applied.
    Only users with at least one logged request are included.
    """
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        params = []
        where = "WHERE u.enabled = 1"
        if hours:
            where += " AND l.timestamp >= ?"
            params.append(time.time() - (hours * 3600))

        rows = await (await db.execute(
            f"""SELECT u.id as user_id, u.username,
                COUNT(l.id) as requests,
                COALESCE(SUM(l.input_tokens), 0) as input_tokens,
                COALESCE(SUM(l.cached_read_tokens), 0) as cached_read_tokens,
                COALESCE(SUM(l.output_tokens), 0) as output_tokens,
                COALESCE(SUM(l.cost), 0) as cost,
                COALESCE(SUM(l.cache_hit), 0) as cache_hits
               FROM usage_logs l
               JOIN users u ON u.id = l.user_id
               {where}
               GROUP BY u.id
               HAVING requests > 0
               ORDER BY cost DESC
               LIMIT ?""", params + [limit]
        )).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            d["total_tokens"] = d["input_tokens"] + d["cached_read_tokens"] + d["output_tokens"]
            result.append(d)
        return result


async def list_public_users(db_path):
    """Minimal enabled-user list (id + username) used by the compare-usage
    user picker — deliberately excludes credits/admin flags/etc."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT id, username FROM users WHERE enabled = 1 ORDER BY username COLLATE NOCASE"
        )).fetchall()
        return [dict(r) for r in rows]


async def get_usage_totals_for_users(db_path, user_ids, hours=None):
    """Per-user usage totals for an explicit set of users (the "compare" view).

    Unlike get_leaderboard, this always returns a row for every requested user
    — including zero usage — since comparison is more useful when it also
    shows "this user did nothing in this period" rather than omitting them.
    """
    if not user_ids:
        return []
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        params = []
        time_clause = ""
        if hours:
            time_clause = " AND l.timestamp >= ?"
            params.append(time.time() - (hours * 3600))
        placeholders = ",".join("?" for _ in user_ids)
        params.extend(user_ids)

        rows = await (await db.execute(
            f"""SELECT u.id as user_id, u.username,
                COUNT(l.id) as requests,
                COALESCE(SUM(l.input_tokens), 0) as input_tokens,
                COALESCE(SUM(l.cached_read_tokens), 0) as cached_read_tokens,
                COALESCE(SUM(l.output_tokens), 0) as output_tokens,
                COALESCE(SUM(l.cost), 0) as cost,
                COALESCE(SUM(l.cache_hit), 0) as cache_hits
               FROM users u
               LEFT JOIN usage_logs l ON l.user_id = u.id{time_clause}
               WHERE u.enabled = 1 AND u.id IN ({placeholders})
               GROUP BY u.id
               ORDER BY u.username COLLATE NOCASE""", params
        )).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            d["total_tokens"] = d["input_tokens"] + d["cached_read_tokens"] + d["output_tokens"]
            result.append(d)
        return result


async def get_usage_by_model_for_users(db_path, user_ids, hours=None):
    """Per-user, per-model usage breakdown for the compare view's model chart."""
    if not user_ids:
        return []
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        placeholders = ",".join("?" for _ in user_ids)
        params = list(user_ids)
        where = f"WHERE u.enabled = 1 AND l.user_id IN ({placeholders})"
        if hours:
            where += " AND l.timestamp >= ?"
            params.append(time.time() - (hours * 3600))

        rows = await (await db.execute(
            f"""SELECT u.id as user_id, u.username, l.model,
                COUNT(l.id) as requests,
                COALESCE(SUM(l.input_tokens), 0) as input_tokens,
                COALESCE(SUM(l.cached_read_tokens), 0) as cached_read_tokens,
                COALESCE(SUM(l.output_tokens), 0) as output_tokens,
                COALESCE(SUM(l.cost), 0) as cost,
                COALESCE(SUM(l.cache_hit), 0) as cache_hits
               FROM usage_logs l
               JOIN users u ON u.id = l.user_id
               {where}
               GROUP BY u.id, l.model
               ORDER BY u.username COLLATE NOCASE, cost DESC""", params
        )).fetchall()
        return [dict(r) for r in rows]


async def get_timeseries_for_users(db_path, user_ids, hours=24, bucket_minutes=60):
    """Per-user, gap-filled time series (bucketed) for the compare view's
    trend chart — lets users see not just totals but *when* usage happened."""
    if not user_ids:
        return {"buckets": [], "bucket_seconds": 0, "series": {}}
    cutoff = time.time() - (hours * 3600)
    bucket_minutes = max(1, int(bucket_minutes))
    max_buckets = 1500
    if (hours * 60) / bucket_minutes > max_buckets:
        bucket_minutes = int((hours * 60) / max_buckets) + 1
    bucket_seconds = bucket_minutes * 60

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        placeholders = ",".join("?" for _ in user_ids)
        params = [bucket_seconds, bucket_seconds, cutoff] + list(user_ids)
        rows = await (await db.execute(
            f"""SELECT user_id, CAST(timestamp / ? AS INTEGER) * ? as bucket,
                COUNT(*) as requests, SUM(input_tokens) as input_tokens,
                SUM(cached_read_tokens) as cached_read_tokens, SUM(output_tokens) as output_tokens,
                SUM(cost) as cost, SUM(cache_hit) as cache_hits
               FROM usage_logs
               WHERE timestamp >= ? AND user_id IN ({placeholders})
               GROUP BY user_id, bucket ORDER BY bucket ASC""", params
        )).fetchall()

    by_user = {}
    for r in rows:
        by_user.setdefault(r["user_id"], {})[int(r["bucket"])] = dict(r)

    now = time.time()
    start = int(cutoff // bucket_seconds) * bucket_seconds
    end = int(now // bucket_seconds) * bucket_seconds
    buckets = []
    b = start
    while b <= end:
        buckets.append(b)
        b += bucket_seconds

    series = {}
    for uid in user_ids:
        data = by_user.get(uid, {})
        series[uid] = [data.get(bk, {
            "bucket": bk, "requests": 0, "input_tokens": 0, "cached_read_tokens": 0,
            "output_tokens": 0, "cost": 0, "cache_hits": 0,
        }) for bk in buckets]

    return {"buckets": buckets, "bucket_seconds": bucket_seconds, "series": series}


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