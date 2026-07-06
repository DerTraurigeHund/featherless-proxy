"""
Featherless Proxy - Admin routes
"""
import secrets
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from app.database import (
    DB_PATH, list_users, create_user, update_user, delete_user,
    add_credits, get_credit_transactions, list_api_keys, create_api_key, delete_api_key,
    update_api_key, list_models, create_model, delete_model, update_model,
    get_usage_stats, get_recent_logs, get_timeseries,
)
from app.auth import hash_password, get_session_from_request

router = APIRouter(prefix="/admin")

# Injected by main.py at startup for the live system monitor.
queue_mgr = None
cache = None


def init_admin(_queue_mgr, _cache):
    global queue_mgr, cache
    queue_mgr = _queue_mgr
    cache = _cache


async def require_admin(request: Request):
    session = await get_session_from_request(DB_PATH, request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not session["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return session


# --- Users ---

@router.get("/api/users")
async def api_list_users(admin: dict = Depends(require_admin)):
    return JSONResponse(await list_users(DB_PATH))


@router.post("/api/users/create")
async def api_create_user(request: Request, admin: dict = Depends(require_admin)):
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")
    credits = float(body.get("credits", 0))
    credit_limit = float(body.get("credit_limit", 0))
    is_admin = int(body.get("is_admin", 0))
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")
    user = await create_user(DB_PATH, username, hash_password(password), is_admin, credits, credit_limit)
    return JSONResponse(user)


@router.post("/api/users/{user_id}/update")
async def api_update_user(user_id: int, request: Request, admin: dict = Depends(require_admin)):
    body = await request.json()
    kwargs = {}
    if "password" in body:
        kwargs["password_hash"] = hash_password(body["password"])
    if "credits" in body:
        kwargs["credits"] = float(body["credits"])
    if "credit_limit" in body:
        kwargs["credit_limit"] = float(body["credit_limit"])
    if "is_admin" in body:
        kwargs["is_admin"] = int(body["is_admin"])
    if "enabled" in body:
        kwargs["enabled"] = int(body["enabled"])
    await update_user(DB_PATH, user_id, **kwargs)
    return JSONResponse({"ok": True})


@router.post("/api/users/{user_id}/credits")
async def api_add_credits(user_id: int, request: Request, admin: dict = Depends(require_admin)):
    body = await request.json()
    amount = float(body.get("amount", 0))
    reason = body.get("reason", "admin_topup")
    ok, balance = await add_credits(DB_PATH, user_id, amount, reason)
    return JSONResponse({"ok": ok, "balance": balance})


@router.delete("/api/users/{user_id}")
async def api_delete_user(user_id: int, admin: dict = Depends(require_admin)):
    await delete_user(DB_PATH, user_id)
    return JSONResponse({"ok": True})


@router.get("/api/users/{user_id}/transactions")
async def api_user_transactions(user_id: int, admin: dict = Depends(require_admin)):
    return JSONResponse(await get_credit_transactions(DB_PATH, user_id, limit=100))


# --- API Keys ---

@router.get("/api/keys")
async def api_list_keys(admin: dict = Depends(require_admin)):
    return JSONResponse(await list_api_keys(DB_PATH))


@router.post("/api/keys/create")
async def api_create_key(request: Request, admin: dict = Depends(require_admin)):
    body = await request.json()
    key = "fp_" + secrets.token_urlsafe(32)
    name = body.get("name", "")
    user_id = body.get("user_id")
    priority = int(body.get("priority", 2))
    enabled = int(body.get("enabled", 1))
    user = await create_api_key(DB_PATH, key, name, user_id, priority)
    # enable/disable if requested (schema default is 1)
    if enabled == 0:
        await update_api_key(DB_PATH, user["id"], enabled=0)
    return JSONResponse({"key": key, "name": name, "user_id": user_id, "priority": priority, "enabled": enabled})


@router.post("/api/keys/{key_id}/update")
async def api_update_key(key_id: int, request: Request, admin: dict = Depends(require_admin)):
    body = await request.json()
    kwargs = {}
    if "name" in body:
        kwargs["name"] = body["name"]
    if "priority" in body:
        kwargs["priority"] = int(body["priority"])
    if "enabled" in body:
        kwargs["enabled"] = int(body["enabled"])
    if "user_id" in body:
        kwargs["user_id"] = body["user_id"]
    await update_api_key(DB_PATH, key_id, **kwargs)
    return JSONResponse({"ok": True})


@router.delete("/api/keys/{key_id}")
async def api_delete_key(key_id: int, admin: dict = Depends(require_admin)):
    await delete_api_key(DB_PATH, key_id)
    return JSONResponse({"ok": True})


# --- Models ---

@router.get("/api/models")
async def api_list_models(admin: dict = Depends(require_admin)):
    return JSONResponse(await list_models(DB_PATH))


@router.post("/api/models/create")
async def api_create_model(request: Request, admin: dict = Depends(require_admin)):
    body = await request.json()
    m = await create_model(DB_PATH, body["model_id"], body["display_name"],
        int(body["concurrent_cost"]), float(body.get("input_price", 0)),
        float(body.get("cached_read_price", 0)), float(body.get("output_price", 0)))
    return JSONResponse(m)


@router.delete("/api/models/{model_id}")
async def api_delete_model(model_id: int, admin: dict = Depends(require_admin)):
    await delete_model(DB_PATH, model_id)
    return JSONResponse({"ok": True})


@router.post("/api/models/{model_id}/update")
async def api_update_model(model_id: int, request: Request, admin: dict = Depends(require_admin)):
    body = await request.json()
    await update_model(DB_PATH, model_id, **body)
    return JSONResponse({"ok": True})


# --- Stats ---

@router.get("/api/stats")
async def api_stats(hours: float = 24, admin: dict = Depends(require_admin)):
    return JSONResponse(await get_usage_stats(DB_PATH, hours=hours))


@router.get("/api/timeseries")
async def api_timeseries(hours: float = 24, bucket_minutes: int = 60, admin: dict = Depends(require_admin)):
    return JSONResponse(await get_timeseries(DB_PATH, hours=hours, bucket_minutes=bucket_minutes))


@router.get("/api/logs")
async def api_logs(limit: int = 50, admin: dict = Depends(require_admin)):
    return JSONResponse(await get_recent_logs(DB_PATH, limit=limit))


@router.get("/api/transactions")
async def api_all_transactions(limit: int = 100, admin: dict = Depends(require_admin)):
    return JSONResponse(await get_credit_transactions(DB_PATH, limit=limit))


# --- Live system monitor (queue + cache) ---

@router.get("/api/system")
async def api_system(admin: dict = Depends(require_admin)):
    queue_stats = queue_mgr.stats() if queue_mgr else {}
    cache_stats = await cache.stats() if cache else {}
    return JSONResponse({"queue": queue_stats, "cache": cache_stats})