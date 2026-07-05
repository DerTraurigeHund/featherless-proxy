"""
Featherless Proxy - Main application v2
User accounts, credits, admin panel, user panel.
"""
import os
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

from database import init_db, DB_PATH, get_user_by_username
from auth import hash_password, verify_password, needs_rehash, create_session, destroy_session, get_session_from_request
from cache import ResponseCache
from queue_manager import QueueManager
from api_routes import router as api_router, init_routes
from admin_routes import router as admin_router, init_admin
from user_routes import router as user_router

# Config
FEATHERLESS_API_BASE = os.getenv("FEATHERLESS_API_BASE", "https://api.featherless.ai/v1")
FEATHERLESS_API_KEY = os.getenv("FEATHERLESS_API_KEY", "")
MAX_CONCURRENT_CONNECTIONS = int(os.getenv("MAX_CONCURRENT_CONNECTIONS", "16"))
CACHE_TTL = int(os.getenv("CACHE_TTL", "60"))
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "proxy.db")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "32768"))

import database
database.DB_PATH = DATABASE_PATH

# The route modules captured ``DB_PATH`` at import time via ``from database
# import DB_PATH``. Re-point them at the configured database so the whole app
# uses one consistent DB even when DATABASE_PATH differs from the default.
import api_routes as _api_routes
import admin_routes as _admin_routes
import user_routes as _user_routes
import auth as _auth
for _mod in (_api_routes, _admin_routes, _user_routes, _auth):
    _mod.DB_PATH = DATABASE_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("featherless-proxy")

cache = ResponseCache(ttl=CACHE_TTL)
queue_mgr = QueueManager(max_connections=MAX_CONCURRENT_CONNECTIONS)
http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    await init_db(DATABASE_PATH)

    # Create default admin if no users exist
    from database import list_users, create_user
    users = await list_users(DATABASE_PATH)
    if not users:
        await create_user(DATABASE_PATH, "admin", hash_password("admin"), is_admin=1, credits=0, credit_limit=0)
        logger.info("Created default admin user: admin/admin")

    http_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0))
    init_routes(cache, queue_mgr, http_client, FEATHERLESS_API_BASE, FEATHERLESS_API_KEY, MAX_CONCURRENT_CONNECTIONS, MAX_TOKENS)
    init_admin(queue_mgr, cache)
    await queue_mgr.start()
    await cache.start_cleanup()
    logger.info(f"Proxy started on {HOST}:{PORT} (max_conn={MAX_CONCURRENT_CONNECTIONS})")
    yield
    await cache.stop_cleanup()
    await queue_mgr.stop()
    await http_client.aclose()


app = FastAPI(title="Featherless Proxy", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Register routers
app.include_router(api_router)
app.include_router(admin_router)
app.include_router(user_router)


# --- Auth routes ---

@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    username = body.get("username", "")
    password = body.get("password", "")
    user = await get_user_by_username(DATABASE_PATH, username)
    if not user or not verify_password(password, user["password_hash"]):
        return JSONResponse({"error": "Invalid credentials"}, status_code=401)
    if not user["enabled"]:
        return JSONResponse({"error": "Account disabled"}, status_code=403)
    # Transparently upgrade legacy/low-cost password hashes on successful login.
    if needs_rehash(user["password_hash"]):
        from database import update_user
        await update_user(DATABASE_PATH, user["id"], password_hash=hash_password(password))
    token = await create_session(DATABASE_PATH, user["id"], user["username"], user["is_admin"])
    resp = JSONResponse({"ok": True, "is_admin": bool(user["is_admin"]), "username": user["username"]})
    resp.set_cookie("session", token, httponly=True, max_age=86400, samesite="lax")
    return resp


@app.post("/api/logout")
async def logout(request: Request):
    cookie = request.headers.get("cookie", "")
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("session="):
            token = part[8:]
            await destroy_session(DATABASE_PATH, token)
            break
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp


@app.get("/api/session")
async def check_session(request: Request):
    session = await get_session_from_request(DATABASE_PATH, request)
    if not session:
        return JSONResponse({"authenticated": False})
    return JSONResponse({"authenticated": True, "username": session["username"], "is_admin": session["is_admin"]})


# --- Pages ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    session = await get_session_from_request(DATABASE_PATH, request)
    if not session or not session["is_admin"]:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request=request, name="admin.html")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    # No server-side session check — user.js handles auth via /user/api/me
    return templates.TemplateResponse(request=request, name="dashboard.html")


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    uvicorn.run("main:app", host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8080")), reload=False)