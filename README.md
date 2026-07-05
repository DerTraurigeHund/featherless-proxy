# Featherless Proxy

A priority-queue proxy for Featherless.ai with response caching and cost tracking.

## Features

- **OpenAI-compatible API** — drop-in replacement for Featherless/OpenAI endpoints
- **Event-driven priority queue** — API key-based priorities (P1 = immediate, P2 = queue) with connection-slot admission control, backfill and anti-starvation aging
- **Concurrent Connection Tracking** — prevents "too many connections" errors
- **Response Cache** — chunk-based cache with TTL and ratio-based cached-read billing
- **Cost Calculator** — tracks input/cached-read/output token costs per model
- **Users & Credits** — per-user accounts, credit balances and audit log
- **WebUI** — admin dashboard with live system monitor, charts, user/key/model management, plus a user dashboard

## Setup

```bash
cd featherless-proxy
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your Featherless API key and plan limits
python main.py
```

## Configuration (.env)

| Variable | Description | Default |
|----------|-------------|---------|
| `FEATHERLESS_API_BASE` | Featherless API base URL | `https://api.featherless.ai/v1` |
| `FEATHERLESS_API_KEY` | Your Featherless API key | (required) |
| `MAX_CONCURRENT_CONNECTIONS` | Total concurrent connection slots on your plan | `16` |
| `CACHE_TTL` | Response cache TTL in seconds | `60` |
| `HOST` | Bind address | `0.0.0.0` |
| `PORT` | Listen port | `8080` |
| `DATABASE_PATH` | SQLite database path | `proxy.db` |

## Usage

### 1. Configure Models

In the WebUI, add each model with:
- Model ID (e.g. `featherless/zai-org/GLM-5.2`)
- Concurrent cost (e.g. `4` for GLM-5.2)
- Input price per 1M tokens
- Cached read price per 1M tokens
- Output price per 1M tokens

### 2. Create API Keys

Create API keys with priority:
- **Priority 1 (Immediate)** — bypasses the queue, processed as soon as slots are free
- **Priority 2 (Queue)** — waits in queue behind P1 requests

### 3. Use the Proxy

Point your OpenAI-compatible client to the proxy:

```python
import openai

client = openai.OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="fp_your_api_key_here"
)

response = client.chat.completions.create(
    model="featherless/zai-org/GLM-5.2",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

## How It Works

### Queue System

The queue is an **event-driven admission controller** (no polling): requests
block on an async condition and are woken the instant connection slots free up.

1. Request comes in with an API key
2. API key's priority determines queue position (P1 jumps ahead of P2)
3. The model's `concurrent_cost` is checked against available slots
4. The highest-priority request that fits in the free slots is admitted
5. **Backfill** — if the head request needs more slots than are free, a smaller
   request behind it may run, keeping connections busy
6. **Aging** — once the head request has waited longer than the aging window it
   reserves the queue so large requests can't be starved by a stream of small ones
7. Both streaming and non-streaming requests use the same `acquire` / `release`
   slot lifecycle; slots are always released, even on client disconnect or error

### Cache System

- Prompts are split into **semantic chunks** (paragraphs → sentences); each chunk
  is cached per-model for `CACHE_TTL` seconds
- On lookup, the fraction of matched chunks yields a **cached-read ratio**, which
  is applied to the upstream `input_tokens` for accurate cached-read billing —
  this catches partial reuse even when messages are reordered, not just leading prefixes
- Cache hits are logged with `cache_hit=1` and the cached-read tokens are tracked

### Cost Calculation

Cost = (input_tokens / 1M × input_price) + (cached_read_tokens / 1M × cached_read_price) + (output_tokens / 1M × output_price)

All prices are per 1M tokens, set per-model in the WebUI.

## WebUI

Sign in at `http://localhost:8080/`:

- **Admin dashboard** (`/admin`) — live system monitor (connection gauge, queue,
  cache), cost/token charts, per-model and per-key stats, plus user, API key and
  model management
- **User dashboard** (`/dashboard`) — credit balance, personal usage charts and
  logs, API key management, available models and a copy-paste usage snippet

Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes; legacy SHA-256 hashes
are verified and transparently upgraded on the next login. The default admin
account is `admin` / `admin` — change it immediately after first login.

## API Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/v1/chat/completions` | POST | API key | OpenAI-compatible chat completions (streaming + non-streaming) |
| `/v1/models` | GET | API key | List admin-configured models |
| `/v1/me` | GET | API key | Info about the calling key/account |
| `/api/login` · `/api/logout` · `/api/session` | POST/GET | session | Auth |
| `/admin/api/system` | GET | admin | Live queue + cache stats |
| `/admin/api/stats` · `/admin/api/timeseries` · `/admin/api/logs` | GET | admin | Usage analytics |
| `/admin/api/users` · `/keys` · `/models` | GET/POST/DELETE | admin | Management |
| `/user/api/me` · `/stats` · `/keys` · `/models` · `/password` | GET/POST/DELETE | session | User self-service |
| `/` · `/login` · `/admin` · `/dashboard` | GET | — | WebUI pages |