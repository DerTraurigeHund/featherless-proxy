"""
Featherless Proxy - Cache layer
Caches prompt chunks for CACHE_TTL seconds.

Chunk-based matching: prompts are split into semantic chunks (paragraphs /
sentences) and each chunk is cached independently per model. This allows
partial cache hits even when messages are reordered or interspersed with new
content — not just leading-prefix matches.

Token estimation is ratio-based: the fraction of matched characters is applied
to the actual input_tokens reported by the upstream API, giving accurate
cached_read billing instead of a rough len//4 guess.
"""
import time
import hashlib
import asyncio
import re
import logging
from collections import defaultdict

logger = logging.getLogger("featherless-proxy.cache")

MAX_CHUNK_SIZE = 1200  # max characters per chunk


def _split_by_sentences(text: str) -> list[str]:
    """Split a (usually long) text by sentence boundaries, falling back to
    fixed-size slices if a single sentence exceeds MAX_CHUNK_SIZE."""
    if len(text) <= MAX_CHUNK_SIZE:
        return [text] if text else []

    chunks = []
    current = ""
    for sent in re.split(r'(?<=[.!?。！？])\s+', text):
        sent = sent.strip()
        if not sent:
            continue
        if len(current) + len(sent) + 1 <= MAX_CHUNK_SIZE:
            current = (current + " " + sent).strip() if current else sent
        else:
            if current:
                chunks.append(current)
            if len(sent) <= MAX_CHUNK_SIZE:
                current = sent
            else:
                # Degenerate sentence: hard slice at the size limit.
                for i in range(0, len(sent), MAX_CHUNK_SIZE):
                    piece = sent[i:i + MAX_CHUNK_SIZE].strip()
                    if piece:
                        chunks.append(piece)
                current = ""
    if current:
        chunks.append(current)
    return chunks


def _chunk_content(content: str) -> list[str]:
    """Split content into semantic chunks.

    Paragraph boundaries and individual lines are preserved so that code is
    not sliced mid-line. Only individual lines longer than MAX_CHUNK_SIZE are
    split, preferring sentence boundaries. This keeps cache fingerprints
    stable when small edits happen inside a prompt.
    """
    if not content:
        return []
    # Standardize line endings and collapse tabs/multiple spaces
    # (keep \n so paragraph/line boundaries stay intact).
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    content = re.sub(r'[^\S\n]+', ' ', content).strip()
    if not content:
        return []

    chunks = []
    for para in content.split("\n\n"):
        para = para.strip()
        if not para:
            continue

        # Short paragraph = single chunk.
        if len(para) <= MAX_CHUNK_SIZE:
            chunks.append(para)
            continue

        # Large paragraph: group lines to keep code boundaries intact.
        current_lines: list[str] = []
        current_len = 0

        def _flush_lines():
            nonlocal current_lines, current_len
            if current_lines:
                chunks.append("\n".join(current_lines))
                current_lines = []
                current_len = 0

        for line in para.split("\n"):
            line = line.strip()
            if not line:
                _flush_lines()
                continue

            if len(line) > MAX_CHUNK_SIZE:
                _flush_lines()
                chunks.extend(_split_by_sentences(line))
                continue

            if current_len + len(line) + 1 <= MAX_CHUNK_SIZE:
                current_lines.append(line)
                current_len += len(line) + 1
            else:
                _flush_lines()
                current_lines = [line]
                current_len = len(line)

        _flush_lines()
    return chunks


def _messages_to_chunks(messages: list) -> list[dict]:
    """Convert messages into a flat list of chunk dicts with fingerprints."""
    chunks = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = str(content)
        for text in _chunk_content(content):
            fp = hashlib.sha256((role + "\x00" + text).encode()).hexdigest()
            chunks.append({
                "role": role,
                "text": text,
                "fingerprint": fp,
                "chars": len(text),
            })
    return chunks


class ResponseCache:
    """
    In-memory chunk cache with TTL.

    Prompts are split into semantic chunks; each chunk is cached independently
    per model. On lookup, the fraction of the new prompt's chunks that are
    already known is returned as a ratio, used for cached_read billing.
    """

    def __init__(self, ttl: int = 60, max_chunks: int | None = None):
        self.ttl = ttl
        self.max_chunks = max_chunks  # None = unlimited
        # model -> fingerprint -> {chars, timestamp, hit_count}
        self._chunks: dict[str, dict[str, dict]] = defaultdict(dict)
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None
        self._stats = {"total_sets": 0, "total_lookups": 0, "total_chunk_hits": 0}

    async def get(self, model: str, messages: list, **kwargs) -> dict | None:
        """
        Check how many of the prompt's chunks are already cached.

        Returns:
        - None: no chunks matched
        - dict with _cached_ratio (0.0-1.0), _matched_chunks, _total_chunks,
          _matched_chars, _total_chars, _cache_type
        """
        chunks = _messages_to_chunks(messages)
        if not chunks:
            return None

        total_chars = sum(c["chars"] for c in chunks)
        if total_chars == 0:
            return None

        now = time.time()
        matched_chars = 0
        matched_chunks = 0

        async with self._lock:
            self._stats["total_lookups"] += 1
            model_chunks = self._chunks.get(model)
            if not model_chunks:
                logger.info(f"Cache miss model={model}: no chunks stored for this model yet")
                return None

            for c in chunks:
                entry = model_chunks.get(c["fingerprint"])
                if entry and now - entry["timestamp"] < self.ttl:
                    matched_chars += c["chars"]
                    matched_chunks += 1
                    entry["hit_count"] += 1
                    entry["timestamp"] = now  # touch: keep frequently used chunks alive
                    self._stats["total_chunk_hits"] += 1

        if matched_chunks == 0:
            logger.info(
                f"Cache miss model={model}: none of {len(chunks)} chunks matched; "
                f"{len(model_chunks)} chunks stored for this model")
            return None

        ratio = matched_chars / total_chars
        if ratio >= 0.999:
            cache_type = "full"
        else:
            cache_type = "partial"

        logger.debug(
            f"Cache {cache_type} hit model={model}: "
            f"{matched_chunks}/{len(chunks)} chunks matched, ratio={ratio:.2%}")
        return {
            "_cache_type": cache_type,
            "_cached_ratio": ratio,
            "_matched_chunks": matched_chunks,
            "_total_chunks": len(chunks),
            "_matched_chars": matched_chars,
            "_total_chars": total_chars,
        }

    async def set(self, model: str, messages: list, response: dict, **kwargs):
        """Store all chunks of the prompt in the cache."""
        chunks = _messages_to_chunks(messages)
        if not chunks:
            return
        now = time.time()

        async with self._lock:
            self._stats["total_sets"] += 1
            model_chunks = self._chunks[model]
            for c in chunks:
                model_chunks[c["fingerprint"]] = {
                    "chars": c["chars"],
                    "timestamp": now,
                    "hit_count": 0,
                }
            logger.info(
                f"Cache set model={model}: stored {len(chunks)} chunks, "
                f"model now has {len(model_chunks)} chunks")
            self._enforce_capacity()

    def _enforce_capacity(self):
        """Evict expired chunks globally; if still over limit, drop oldest."""
        # With unlimited capacity the periodic cleanup loop is enough; skipping
        # the scan here keeps set() fast even when the cache grows large.
        if self.max_chunks is None:
            return
        now = time.time()
        for model in list(self._chunks.keys()):
            model_chunks = self._chunks[model]
            expired = [fp for fp, e in model_chunks.items() if now - e["timestamp"] >= self.ttl]
            for fp in expired:
                del model_chunks[fp]
            if not model_chunks:
                del self._chunks[model]

        if self.max_chunks is None:
            return
        total = sum(len(m) for m in self._chunks.values())
        while total > self.max_chunks:
            oldest_model = None
            oldest_fp = None
            oldest_ts = None
            for m, chunks in self._chunks.items():
                for fp, e in chunks.items():
                    if oldest_ts is None or e["timestamp"] < oldest_ts:
                        oldest_ts = e["timestamp"]
                        oldest_model = m
                        oldest_fp = fp
            if oldest_model is None:
                break
            del self._chunks[oldest_model][oldest_fp]
            if not self._chunks[oldest_model]:
                del self._chunks[oldest_model]
            total -= 1

    async def _cleanup_loop(self):
        """Background task that periodically removes expired chunks."""
        interval = max(self.ttl / 4, 5)  # run at least every 5s
        while True:
            await asyncio.sleep(interval)
            try:
                now = time.time()
                async with self._lock:
                    removed = 0
                    for model in list(self._chunks.keys()):
                        model_chunks = self._chunks[model]
                        expired = [fp for fp, e in model_chunks.items() if now - e["timestamp"] >= self.ttl]
                        for fp in expired:
                            del model_chunks[fp]
                            removed += 1
                        if not model_chunks:
                            del self._chunks[model]
                    if removed:
                        logger.debug(f"Cache cleanup: removed {removed} expired chunks")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Cache cleanup error: {e}")

    async def start_cleanup(self):
        """Start the background cleanup task. Call once during app startup."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info(f"Cache cleanup task started (interval={max(self.ttl / 4, 5):.0f}s)")

    async def stop_cleanup(self):
        """Stop the background cleanup task. Call once during app shutdown."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    async def stats(self) -> dict:
        async with self._lock:
            now = time.time()
            active = sum(
                1
                for m in self._chunks.values()
                for e in m.values()
                if now - e["timestamp"] < self.ttl
            )
            total = sum(len(m) for m in self._chunks.values())
            return {
                "active_chunks": active,
                "total_chunks": total,
                "models_cached": len(self._chunks),
                "total_sets": self._stats["total_sets"],
                "total_lookups": self._stats["total_lookups"],
                "total_chunk_hits": self._stats["total_chunk_hits"],
                "ttl_seconds": self.ttl,
                "max_chunks": self.max_chunks,
            }

    async def clear(self):
        async with self._lock:
            self._chunks.clear()
            self._stats = {"total_sets": 0, "total_lookups": 0, "total_chunk_hits": 0}