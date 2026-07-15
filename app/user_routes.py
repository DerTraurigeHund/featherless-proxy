"""
Featherless Proxy - User routes
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from app.database import (
    DB_PATH, get_user_by_id, get_usage_stats, get_recent_logs,
    get_timeseries, get_credit_transactions, list_api_keys,
    create_api_key, delete_api_key, update_user, get_leaderboard,
    list_public_users, get_usage_totals_for_users, get_usage_by_model_for_users,
    get_timeseries_for_users,
)
from app.auth import get_session_from_request

router = APIRouter(prefix="/user")

# Injected by main.py at startup.
queue_mgr = None


def init_user(_queue_mgr):
    global queue_mgr
    queue_mgr = _queue_mgr


async def require_session(request: Request):
    session = await get_session_from_request(DB_PATH, request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


@router.get("/api/me")
async def get_me(session: dict = Depends(require_session)):
    user = await get_user_by_id(DB_PATH, session["user_id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    q = queue_mgr.stats() if queue_mgr else {}
    return JSONResponse({
        "id": user["id"], "username": user["username"],
        "credits": user["credits"], "credit_limit": user["credit_limit"],
        "is_admin": bool(user["is_admin"]),
        "queue": {
            "max_connections": q.get("max_connections", 0),
            "used_connections": q.get("used_connections", 0),
            "free_connections": q.get("free_connections", 0),
            "utilization": q.get("utilization", 0),
            "queue_size": q.get("queue_size", 0),
            "oldest_wait_seconds": q.get("oldest_wait_seconds", 0),
            "promote_after_seconds": q.get("promote_after_seconds", 0),
        }
    })


@router.get("/api/system")
async def user_system(session: dict = Depends(require_session)):
    """Public queue capacity info for the user dashboard."""
    q = queue_mgr.stats() if queue_mgr else {}
    return JSONResponse({
        "max_connections": q.get("max_connections", 0),
        "used_connections": q.get("used_connections", 0),
        "free_connections": q.get("free_connections", 0),
        "utilization": q.get("utilization", 0),
        "queue_size": q.get("queue_size", 0),
        "oldest_wait_seconds": q.get("oldest_wait_seconds", 0),
        "promote_after_seconds": q.get("promote_after_seconds", 0),
        "queue_by_priority": q.get("queue_by_priority", {}),
    })


@router.get("/api/stats")
async def my_stats(hours: float = 24, session: dict = Depends(require_session)):
    return JSONResponse(await get_usage_stats(DB_PATH, hours=hours, user_id=session["user_id"]))


@router.get("/api/timeseries")
async def my_timeseries(hours: float = 24, bucket_minutes: int = 60, session: dict = Depends(require_session)):
    return JSONResponse(await get_timeseries(DB_PATH, hours=hours, bucket_minutes=bucket_minutes, user_id=session["user_id"]))


@router.get("/api/logs")
async def my_logs(limit: int = 50, session: dict = Depends(require_session)):
    return JSONResponse(await get_recent_logs(DB_PATH, limit=limit, user_id=session["user_id"]))


@router.get("/api/keys")
async def my_keys(session: dict = Depends(require_session)):
    return JSONResponse(await list_api_keys(DB_PATH, user_id=session["user_id"]))


@router.post("/api/keys/create")
async def create_my_key(request: Request, session: dict = Depends(require_session)):
    import secrets as sec
    body = await request.json()
    key = "fp_" + sec.token_urlsafe(32)
    name = body.get("name", "")
    # Users get P3 by default (Normal priority).
    await create_api_key(DB_PATH, key, name, session["user_id"], 3)
    return JSONResponse({"key": key, "name": name})


@router.delete("/api/keys/{key_id}")
async def delete_my_key(key_id: int, session: dict = Depends(require_session)):
    # Users can only delete their own keys
    keys = await list_api_keys(DB_PATH, user_id=session["user_id"])
    if not any(k["id"] == key_id for k in keys):
        raise HTTPException(status_code=403, detail="Not your key")
    await delete_api_key(DB_PATH, key_id)
    return JSONResponse({"ok": True})


@router.post("/api/password")
async def change_password(request: Request, session: dict = Depends(require_session)):
    from app.auth import hash_password, verify_password
    body = await request.json()
    current = body.get("current_password", "")
    new_pw = body.get("new_password", "")
    if not current or not new_pw:
        raise HTTPException(status_code=400, detail="Current and new password required")
    if len(new_pw) < 4:
        raise HTTPException(status_code=400, detail="Password too short (min 4 chars)")
    user = await get_user_by_id(DB_PATH, session["user_id"])
    if not verify_password(current, user["password_hash"]):
        raise HTTPException(status_code=403, detail="Current password incorrect")
    await update_user(DB_PATH, session["user_id"], password_hash=hash_password(new_pw))
    return JSONResponse({"ok": True})


@router.get("/api/transactions")
async def my_transactions(limit: int = 50, session: dict = Depends(require_session)):
    return JSONResponse(await get_credit_transactions(DB_PATH, user_id=session["user_id"], limit=limit))


@router.get("/api/models")
async def my_models(session: dict = Depends(require_session)):
    """Users can see available models with prices."""
    from app.database import list_models as list_all_models
    models = await list_all_models(DB_PATH)
    return JSONResponse([{
        "model_id": m["model_id"],
        "display_name": m["display_name"],
        "concurrent_cost": m["concurrent_cost"],
        "input_price": m["input_price"],
        "cached_read_price": m["cached_read_price"],
        "output_price": m["output_price"],
    } for m in models])


@router.get("/api/leaderboard")
async def leaderboard(hours: float | None = None, limit: int = 50, session: dict = Depends(require_session)):
    """Cross-user usage leaderboard — requests/tokens/cost per user, so users
    can compare their own usage against everyone else. hours omitted = all-time."""
    rows = await get_leaderboard(DB_PATH, hours=hours, limit=limit)
    return JSONResponse({"rows": rows, "hours": hours})


@router.get("/api/users")
async def list_comparable_users(session: dict = Depends(require_session)):
    """Minimal (id, username) user list for the compare-usage picker."""
    return JSONResponse(await list_public_users(DB_PATH))


@router.get("/api/compare")
async def compare_usage(user_ids: str, hours: float | None = None, session: dict = Depends(require_session)):
    """Detailed side-by-side usage comparison for an explicit set of users:
    overall totals (requests/tokens/cost/cache-hits) plus a per-model breakdown,
    so users can compare not just totals but which models drive their usage
    and how much of it was served from cache vs. freshly computed."""
    try:
        ids = [int(x) for x in user_ids.split(",") if x.strip() != ""]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_ids")
    ids = list(dict.fromkeys(ids))[:12]  # de-dupe, cap comparison size
    if not ids:
        raise HTTPException(status_code=400, detail="user_ids required")
    totals = await get_usage_totals_for_users(DB_PATH, ids, hours=hours)
    by_model = await get_usage_by_model_for_users(DB_PATH, ids, hours=hours)
    return JSONResponse({"hours": hours, "totals": totals, "by_model": by_model})


@router.get("/api/compare/timeseries")
async def compare_timeseries(user_ids: str, hours: float = 24, bucket_minutes: int = 60, session: dict = Depends(require_session)):
    """Per-user usage trend over time, for overlaying one line per user on
    the compare view's chart (totals alone hide *when* usage happened)."""
    try:
        ids = [int(x) for x in user_ids.split(",") if x.strip() != ""]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_ids")
    ids = list(dict.fromkeys(ids))[:12]
    if not ids:
        raise HTTPException(status_code=400, detail="user_ids required")
    res = await get_timeseries_for_users(DB_PATH, ids, hours=hours, bucket_minutes=bucket_minutes)
    return JSONResponse(res)