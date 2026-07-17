"""
Featherless Proxy - API routes (OpenAI-compatible)
"""
import json
import time
import logging
import asyncio

import httpx
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, StreamingResponse

from app.database import (
    DB_PATH, get_model_by_id, get_api_key_by_key, log_usage, deduct_credits,
)
from app.cache import ResponseCache
from app.queue_manager import QueueManager, QueueTimeout

logger = logging.getLogger("featherless-proxy")
router = APIRouter()

# These are set by main.py at startup
cache: ResponseCache | None = None
queue_mgr: QueueManager | None = None
http_client: httpx.AsyncClient | None = None
FEATHERLESS_API_BASE = ""
FEATHERLESS_API_KEY = ""
MAX_CONCURRENT_CONNECTIONS = 16
MAX_TOKENS = 8192*4
STREAM_HEARTBEAT_INTERVAL = 25.0

# Upstream retry behaviour: transient Featherless failures (HTTP 429, 5xx and
# connection errors) are retried up to UPSTREAM_MAX_RETRIES times with a
# linear backoff before the error is passed to the client.
UPSTREAM_MAX_RETRIES = 20
RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY = 10.0

# The proxy frees/claims queue slots faster than Featherless registers the
# closed/opened upstream connections. After waiting in the queue, pause
# briefly before opening the upstream connection.
QUEUE_GRACE_DELAY = 1.0
QUEUE_GRACE_THRESHOLD = 0.1


def _should_retry(status_code: int) -> bool:
    """Transient upstream failures that are worth retrying."""
    return status_code == 429 or status_code >= 500


def _retry_delay(attempt: int) -> float:
    """Linear backoff capped at RETRY_MAX_DELAY (1s, 2s, ..., 10s, 10s)."""
    return min(RETRY_BASE_DELAY * attempt, RETRY_MAX_DELAY)


def init_routes(_cache, _queue_mgr, _http_client, _api_base, _api_key, _max_conn, _max_tokens):
    global cache, queue_mgr, http_client, FEATHERLESS_API_BASE, FEATHERLESS_API_KEY, MAX_CONCURRENT_CONNECTIONS, MAX_TOKENS
    cache = _cache
    queue_mgr = _queue_mgr
    http_client = _http_client
    FEATHERLESS_API_BASE = _api_base
    FEATHERLESS_API_KEY = _api_key
    MAX_CONCURRENT_CONNECTIONS = _max_conn
    # MAX_TOKENS = _max_tokens


def _compute_cost(input_tokens, cached_read_tokens, output_tokens,
                  input_price, cached_read_price, output_price):
    return round(
        (input_tokens / 1_000_000) * input_price +
        (cached_read_tokens / 1_000_000) * cached_read_price +
        (output_tokens / 1_000_000) * output_price, 6)


def _resolve_model(model_config) -> dict:
    if model_config:
        return {
            "cost": model_config["concurrent_cost"],
            "input_price": model_config["input_price"],
            "cached_read_price": model_config["cached_read_price"],
            "output_price": model_config["output_price"],
        }
    return {"cost": 1, "input_price": 0, "cached_read_price": 0, "output_price": 0}


async def verify_api_key(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    key = auth[7:].strip()
    key_data = await get_api_key_by_key(DB_PATH, key)
    if not key_data:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if key_data.get("user_id") is not None and not key_data.get("user_enabled"):
        raise HTTPException(status_code=403, detail="User account disabled")
    return key_data


def _is_admin_key(api_key_data: dict) -> bool:
    # Keys without an owning user are admin/system keys.
    if api_key_data.get("user_id") is None:
        return True
    return bool(api_key_data.get("user_is_admin"))


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, api_key_data: dict = Depends(verify_api_key)):
    body = await request.json()
    model = body.get("model", "")
    messages = body.get("messages", [])
    temperature = body.get("temperature", 0)
    stream = bool(body.get("stream", False))

    cfg = _resolve_model(await get_model_by_id(DB_PATH, model))
    user_id = api_key_data.get("user_id")
    user_credits = api_key_data.get("user_credits") or 0
    is_admin = _is_admin_key(api_key_data)
    priority = api_key_data.get("priority", 2)

    # Credit gate (admins / system keys bypass).
    if not is_admin and user_credits <= 0:
        raise HTTPException(status_code=402, detail="Insufficient credits")

    # Chunk-based cache lookup → cached-read ratio for billing.
    cached_ratio = 0.0
    cached = await cache.get(model, messages, temperature=temperature)
    if cached:
        cached_ratio = cached.get("_cached_ratio", 0.0)
        logger.info(
            f"Cache {cached.get('_cache_type')} match model={model} "
            f"key={api_key_data.get('name')} ratio={cached_ratio:.2%} "
            f"chunks={cached.get('_matched_chunks')}/{cached.get('_total_chunks')}")

    if stream:
        return await _handle_streaming(request, body, model, messages, temperature,
            cfg, api_key_data, user_id, is_admin, priority, cached_ratio)

    return await _handle_blocking(request, body, model, messages, temperature,
        cfg, api_key_data, user_id, is_admin, priority, cached_ratio)


async def _handle_blocking(request, body, model, messages, temperature, cfg,
                           api_key_data, user_id, is_admin, priority, cached_ratio):
    acquire_start = time.time()
    try:
        reservation = await queue_mgr.acquire(
            priority=priority, cost=cfg["cost"],
            name=api_key_data.get("name", ""), model=model, timeout=300.0)
    except QueueTimeout:
        raise HTTPException(status_code=504, detail="Queue timeout")

    try:
        # If we had to queue for a slot, give Featherless a brief moment to
        # register the freed upstream connection before opening a new one.
        if time.time() - acquire_start > QUEUE_GRACE_THRESHOLD:
            await asyncio.sleep(QUEUE_GRACE_DELAY)

        forward_body = dict(body)
        forward_body["stream"] = False
        if "max_tokens" not in forward_body and MAX_TOKENS > 0:
            forward_body["max_tokens"] = MAX_TOKENS
        headers = {"Authorization": f"Bearer {FEATHERLESS_API_KEY}",
                   "Content-Type": "application/json"}

        # Retry transient upstream failures instead of passing them through.
        resp = None
        error_status = 502
        error_text = "Upstream unavailable"
        for attempt in range(1, UPSTREAM_MAX_RETRIES + 1):
            try:
                resp = await http_client.post(f"{FEATHERLESS_API_BASE}/chat/completions",
                    headers=headers, json=forward_body, timeout=300.0)
            except httpx.RequestError as e:
                logger.warning(
                    f"Upstream connection error model={model} "
                    f"attempt={attempt}/{UPSTREAM_MAX_RETRIES}: {e}")
                resp = None
                error_status = 502
                error_text = f"Upstream connection error: {e}"
            else:
                if resp.status_code == 200:
                    break
                error_status = resp.status_code
                error_text = resp.text
                logger.warning(
                    f"Upstream error {error_status} model={model} "
                    f"attempt={attempt}/{UPSTREAM_MAX_RETRIES}")
                resp = None
                if not _should_retry(error_status):
                    break
            if attempt < UPSTREAM_MAX_RETRIES:
                await asyncio.sleep(_retry_delay(attempt))
        if resp is None:
            raise HTTPException(status_code=error_status, detail=error_text)
        result = resp.json()

        usage = result.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        cached_read_tokens = 0
        if usage.get("prompt_tokens_details"):
            cached_read_tokens = usage["prompt_tokens_details"].get("cached_tokens", 0)
        if not cached_read_tokens:
            cached_read_tokens = usage.get("cached_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        # Fallback: estimate output tokens if upstream reports an implausibly low value.
        if output_tokens < 10:
            resp_content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            if resp_content:
                estimated = len(resp_content) // 4
                if estimated > output_tokens:
                    output_tokens = estimated

        # Apply matched chunk ratio to upstream input_tokens for cached-read billing.
        if cached_read_tokens == 0 and cached_ratio > 0:
            cached_read_tokens = int(input_tokens * cached_ratio)
        input_tokens = max(0, input_tokens - cached_read_tokens)

        cost = _compute_cost(input_tokens, cached_read_tokens, output_tokens,
            cfg["input_price"], cfg["cached_read_price"], cfg["output_price"])

        if not is_admin and user_id:
            ok, _ = await deduct_credits(DB_PATH, user_id, cost, "request_cost", model)
            if not ok:
                raise HTTPException(status_code=402, detail="Credit deduction failed")

        assistant_msg = {"role": "assistant",
                         "content": result.get("choices", [{}])[0].get("message", {}).get("content", "")}
        await cache.set(model, messages + [assistant_msg], result, temperature=temperature)
        await cache.set(model, messages, result, temperature=temperature)

        await log_usage(DB_PATH, user_id, api_key_data["id"], api_key_data["name"], model,
            input_tokens, cached_read_tokens, output_tokens, cached_ratio > 0, cost, priority)
        return result
    finally:
        await reservation.release()


async def _handle_streaming(request, body, model, messages, temperature, cfg,
                            api_key_data, user_id, is_admin, priority, cached_ratio):

    async def generate():
        reservation = None
        upstream_response = None
        full_content = ""
        response_id = ""
        response_model = model
        response_created = int(time.time())
        usage_logged = False
        aborted = False
        acquire_task = None
        try:
            # Wait for a queue slot by running queue_mgr.acquire in a background task.
            # While waiting, we periodically wake up via asyncio.wait_for with
            # asyncio.shield so that we do not lose our queue position, allowing us
            # to emit SSE comment lines (heartbeats) to prevent proxy timeouts.
            acquire_start = time.time()
            acquire_task = asyncio.create_task(queue_mgr.acquire(
                priority=priority, cost=cfg["cost"],
                name=api_key_data.get("name", ""), model=model, timeout=300.0))

            queue_deadline = time.time() + 300.0
            while not acquire_task.done():
                remaining = queue_deadline - time.time()
                if remaining <= 0:
                    acquire_task.cancel()
                    yield (f"data: {json.dumps({'error': {'message': 'Queue timeout', 'code': 504}})}\n\n").encode()
                    return
                try:
                    reservation = await asyncio.wait_for(
                        asyncio.shield(acquire_task),
                        timeout=min(STREAM_HEARTBEAT_INTERVAL, remaining))
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        aborted = True
                        return
                    # SSE comment line — keeps the HTTP response alive without
                    # producing a visible event on the client side.
                    yield b":\n\n"

            if await request.is_disconnected():
                aborted = True
                return

            # If we had to queue for a slot, give Featherless a brief moment to
            # register the freed upstream connection before opening a new one.
            if time.time() - acquire_start > QUEUE_GRACE_THRESHOLD:
                await asyncio.sleep(QUEUE_GRACE_DELAY)

            forward_body = dict(body)
            forward_body["stream"] = True
            forward_body["stream_options"] = {"include_usage": True}
            if "max_tokens" not in forward_body and MAX_TOKENS > 0:
                forward_body["max_tokens"] = MAX_TOKENS
            headers = {"Authorization": f"Bearer {FEATHERLESS_API_KEY}",
                       "Content-Type": "application/json", "Accept": "text/event-stream"}

            # Retry transient upstream failures instead of passing them through.
            # Heartbeats are emitted during backoff so the client connection
            # stays alive across the whole retry sequence.
            last_error_status = 502
            last_error_body = b"Upstream unavailable"
            for attempt in range(1, UPSTREAM_MAX_RETRIES + 1):
                if await request.is_disconnected():
                    aborted = True
                    return
                try:
                    candidate = await http_client.send(
                        http_client.build_request("POST", f"{FEATHERLESS_API_BASE}/chat/completions",
                            headers=headers, json=forward_body), stream=True)
                except httpx.RequestError as e:
                    logger.warning(
                        f"Upstream connection error model={model} "
                        f"attempt={attempt}/{UPSTREAM_MAX_RETRIES}: {e}")
                    last_error_status = 502
                    last_error_body = f"Upstream connection error: {e}".encode()
                else:
                    if candidate.status_code == 200:
                        upstream_response = candidate
                        break
                    last_error_status = candidate.status_code
                    last_error_body = await candidate.aread()
                    await candidate.aclose()
                    logger.warning(
                        f"Upstream error {last_error_status} model={model} "
                        f"attempt={attempt}/{UPSTREAM_MAX_RETRIES}")
                    if not _should_retry(last_error_status):
                        break
                if attempt < UPSTREAM_MAX_RETRIES:
                    backoff_end = time.time() + _retry_delay(attempt)
                    while time.time() < backoff_end:
                        await asyncio.sleep(min(STREAM_HEARTBEAT_INTERVAL, backoff_end - time.time()))
                        if time.time() < backoff_end:
                            if await request.is_disconnected():
                                aborted = True
                                return
                            yield b":\n\n"

            if upstream_response is None:
                yield (f"data: {json.dumps({'error': {'message': last_error_body.decode(errors='replace'), 'code': last_error_status}})}\n\n").encode()
                return

            async for chunk in upstream_response.aiter_bytes():
                if await request.is_disconnected():
                    aborted = True
                    break
                chunk_str = chunk.decode("utf-8", errors="replace")
                for line in chunk_str.split("\n"):
                    line = line.strip()
                    if line.startswith("data: ") and line != "data: [DONE]":
                        try:
                            data = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        if "id" in data and not response_id:
                            response_id = data["id"]
                        if "model" in data:
                            response_model = data["model"]
                        if "created" in data:
                            response_created = data["created"]
                        for choice in data.get("choices", []):
                            delta = choice.get("delta", {})
                            if "content" in delta and delta["content"]:
                                full_content += delta["content"]
                        if data.get("usage") and not usage_logged:
                            await _log_stream_usage(
                                data["usage"], full_content, cached_ratio, cfg,
                                api_key_data, user_id, is_admin, model, priority)
                            usage_logged = True
                yield chunk

            if not aborted:
                yield b"data: [DONE]\n\n"

            # NOTE: caching must not depend on full_content being non-empty.
            # Tool-call-only replies (very common in agentic coding sessions)
            # have delta.tool_calls but no delta.content, so full_content stays
            # "" even on a perfectly successful turn. The incoming `messages`
            # (i.e. the conversation so far) still need to be cached in that
            # case, otherwise the whole running conversation never enters the
            # cache and every later turn is a guaranteed miss.
            if not aborted:
                cached_resp = {
                    "id": response_id, "object": "chat.completion",
                    "created": response_created, "model": response_model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": full_content}, "finish_reason": "stop"}],
                }
                await cache.set(model, messages + [{"role": "assistant", "content": full_content}], cached_resp, temperature=temperature)
                await cache.set(model, messages, cached_resp, temperature=temperature)
                if not usage_logged:
                    est_input = sum(len(str(m.get("content", ""))) // 4 for m in messages)
                    crd = int(est_input * cached_ratio) if cached_ratio > 0 else 0
                    est_input = max(0, est_input - crd)
                    out = len(full_content) // 4
                    cost = _compute_cost(est_input, crd, out,
                        cfg["input_price"], cfg["cached_read_price"], cfg["output_price"])
                    if not is_admin and user_id:
                        await deduct_credits(DB_PATH, user_id, cost, "request_cost", model)
                    await log_usage(DB_PATH, user_id, api_key_data["id"], api_key_data["name"], model,
                        est_input, crd, out, cached_ratio > 0, cost, priority)

        except Exception as e:
            logger.error(f"Stream error: {e}")
            aborted = True
            yield (f"data: {json.dumps({'error': {'message': str(e)}})}\n\n").encode()
        finally:
            if acquire_task is not None and not acquire_task.done():
                acquire_task.cancel()
            if upstream_response is not None:
                await upstream_response.aclose()
            if reservation is not None:
                await reservation.release(aborted=aborted)

    return StreamingResponse(generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


async def _log_stream_usage(usage, full_content, cached_ratio, cfg,
                            api_key_data, user_id, is_admin, model, priority):
    input_tokens = usage.get("prompt_tokens", 0)
    crd = 0
    if usage.get("prompt_tokens_details"):
        crd = usage["prompt_tokens_details"].get("cached_tokens", 0)
    if not crd:
        crd = usage.get("cached_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)

    if output_tokens < (len(full_content) // 8) and full_content:
        output_tokens = len(full_content) // 4

    if crd == 0 and cached_ratio > 0:
        crd = int(input_tokens * cached_ratio)
    input_tokens = max(0, input_tokens - crd)

    cost = _compute_cost(input_tokens, crd, output_tokens,
        cfg["input_price"], cfg["cached_read_price"], cfg["output_price"])
    if not is_admin and user_id:
        await deduct_credits(DB_PATH, user_id, cost, "request_cost", model)
    await log_usage(DB_PATH, user_id, api_key_data["id"], api_key_data["name"], model,
        input_tokens, crd, output_tokens, cached_ratio > 0, cost, priority)


@router.get("/v1/models")
async def list_models_api(api_key_data: dict = Depends(verify_api_key)):
    """Return only models configured by admin, not all Featherless models."""
    from app.database import list_models as list_all_models
    models = await list_all_models(DB_PATH)
    data = [{
        "id": m["model_id"],
        "object": "model",
        "created": int(m["created_at"]),
        "owned_by": "featherless",
    } for m in models]
    return JSONResponse({"object": "list", "data": data})


@router.get("/v1/me")
async def get_me(api_key_data: dict = Depends(verify_api_key)):
    q = queue_mgr.stats() if queue_mgr else {}
    return JSONResponse({
        "api_key_name": api_key_data["name"],
        "user_id": api_key_data.get("user_id"),
        "credits": api_key_data.get("user_credits", 0),
        "is_admin": _is_admin_key(api_key_data),
        "priority": api_key_data.get("priority", 2),
        "queue": {
            "max_connections": q.get("max_connections", 0),
            "used_connections": q.get("used_connections", 0),
            "free_connections": q.get("free_connections", 0),
            "queue_size": q.get("queue_size", 0),
            "promote_after_seconds": q.get("promote_after_seconds", 0),
        }
    })
