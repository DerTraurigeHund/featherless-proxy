"""
Featherless Proxy - Auth helpers
Password hashing (PBKDF2-HMAC-SHA256 with per-password salt) and persistent
session management (DB-backed). Legacy unsalted SHA-256 hashes are still
verified and transparently upgraded on next login.
"""
import hashlib
import hmac
import secrets
import time
import aiosqlite

DB_PATH = "proxy.db"

_PBKDF2_ITERATIONS = 240_000
_PBKDF2_ALGO = "sha256"


def hash_password(password: str) -> str:
    """Return a salted PBKDF2 hash string: ``pbkdf2$<iters>$<salt>$<hash>``."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def _verify_pbkdf2(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, hash_hex = stored.split("$", 3)
        dk = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode(),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    if password_hash.startswith("pbkdf2$"):
        return _verify_pbkdf2(password, password_hash)
    # Legacy unsalted SHA-256 fallback.
    legacy = hashlib.sha256(password.encode()).hexdigest()
    return hmac.compare_digest(legacy, password_hash)


def needs_rehash(password_hash: str) -> bool:
    """True if the stored hash uses an outdated scheme and should be upgraded."""
    if not password_hash or not password_hash.startswith("pbkdf2$"):
        return True
    try:
        iters = int(password_hash.split("$", 3)[1])
    except (ValueError, IndexError):
        return True
    return iters < _PBKDF2_ITERATIONS


async def create_session(db_path: str, user_id: int, username: str, is_admin: bool) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO sessions (token, user_id, username, is_admin, expires, created_at) VALUES (?,?,?,?,?,?)",
            (token, user_id, username, is_admin, now + 86400, now)
        )
        await db.commit()
    return token


async def get_session(db_path: str, token: str) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM sessions WHERE token = ? AND expires > ?", (token, time.time())
        )).fetchone()
        if row:
            return dict(row)
    return None


async def destroy_session(db_path: str, token: str):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        await db.commit()


async def cleanup_sessions(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
        await db.commit()


async def get_session_from_request(db_path: str, request) -> dict | None:
    # Check cookie
    cookie = request.headers.get("cookie", "")
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("session="):
            token = part[8:]
            return await get_session(db_path, token)
    # Check Authorization header
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        return await get_session(db_path, token)
    return None