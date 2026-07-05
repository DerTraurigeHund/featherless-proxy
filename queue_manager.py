"""
Featherless Proxy - Queue manager (v3)

Event-driven priority admission control with concurrent connection-slot
tracking. Each request reserves `cost` connection slots; the proxy never
exceeds `max_connections` slots in use.

Key properties
--------------
- **Event-driven**: waiters block on an ``asyncio.Condition`` and are woken the
  instant slots free up — no polling / busy-waiting.
- **Priority**: lower priority number = higher importance (P1 before P2).
- **Backfill**: if the highest-priority waiter does not currently fit, smaller
  waiters behind it may proceed, keeping connection slots busy.
- **Aging / anti-starvation**: once the head-of-line waiter has waited longer
  than ``aging_seconds`` it reserves the queue — backfilling stops so the large
  request is guaranteed to run as soon as enough slots are free.
- **Unified API**: streaming and non-streaming requests both use
  ``acquire()`` / ``Reservation.release()``. No special-casing.

Usage
-----
    res = await queue.acquire(priority=2, cost=4, name="key", model="m")
    try:
        ...  # do upstream work
    finally:
        await res.release()
"""
import asyncio
import time
import itertools
from dataclasses import dataclass, field


@dataclass
class _Waiter:
    priority: int
    cost: int
    seq: int
    enqueued: float
    name: str = ""
    model: str = ""


class Reservation:
    """Handle for reserved connection slots. Release exactly once when done."""

    __slots__ = ("_mgr", "cost", "_released", "acquired_at")

    def __init__(self, mgr: "QueueManager", cost: int):
        self._mgr = mgr
        self.cost = cost
        self._released = False
        self.acquired_at = time.time()

    @property
    def released(self) -> bool:
        return self._released

    async def release(self, aborted: bool = False):
        if self._released:
            return
        self._released = True
        await self._mgr._release(self.cost, aborted=aborted)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.release(aborted=exc_type is not None)


class QueueTimeout(TimeoutError):
    """Raised when a request waits longer than its timeout for slots."""


class QueueManager:
    def __init__(self, max_connections: int, aging_seconds: float = 8.0):
        self.max_connections = max(1, int(max_connections))
        self.aging_seconds = aging_seconds
        self.used_connections = 0
        self._waiters: list[_Waiter] = []
        self._cond = asyncio.Condition()
        self._seq = itertools.count()
        self._running = True
        self._stats = {
            "total_queued": 0,
            "total_processed": 0,
            "total_timeout": 0,
            "total_aborted": 0,
            "total_cancelled": 0,
            "queue_wait_total_ms": 0.0,
            "peak_used": 0,
            "peak_queue": 0,
        }

    # ---- lifecycle (kept for API compatibility with main.py) ----

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False
        async with self._cond:
            self._cond.notify_all()

    # ---- internal scheduling ----

    @property
    def free_connections(self) -> int:
        return self.max_connections - self.used_connections

    def _ordered(self) -> list[_Waiter]:
        return sorted(self._waiters, key=lambda w: (w.priority, w.seq))

    def _chosen(self) -> _Waiter | None:
        """Pick the waiter allowed to proceed right now (or None).

        Highest priority first; if the head does not fit, allow a smaller
        waiter to backfill — unless the head has aged past ``aging_seconds``,
        in which case the head reserves the queue to avoid starvation.
        """
        ordered = self._ordered()
        if not ordered:
            return None
        free = self.free_connections
        head = ordered[0]
        if head.cost <= free:
            return head
        # Head does not fit. Reserve the queue for it once it has aged.
        if (time.time() - head.enqueued) >= self.aging_seconds:
            return None
        for w in ordered[1:]:
            if w.cost <= free:
                return w
        return None

    # ---- public API ----

    async def acquire(self, priority: int, cost: int, name: str = "",
                      model: str = "", timeout: float = 300.0) -> Reservation:
        """Wait in the priority queue until ``cost`` slots are reserved.

        Returns a :class:`Reservation`; the caller must ``release()`` it.
        Raises :class:`QueueTimeout` if not granted within ``timeout`` seconds.
        """
        cost = max(1, min(int(cost), self.max_connections))
        waiter = _Waiter(priority=priority, cost=cost, seq=next(self._seq),
                         enqueued=time.time(), name=name, model=model)
        deadline = time.time() + timeout

        async with self._cond:
            self._waiters.append(waiter)
            self._stats["total_queued"] += 1
            self._stats["peak_queue"] = max(self._stats["peak_queue"], len(self._waiters))
            self._cond.notify_all()
            try:
                while True:
                    if not self._running:
                        raise QueueTimeout("Queue is shutting down")
                    if self._chosen() is waiter:
                        self._waiters.remove(waiter)
                        self.used_connections += cost
                        self._stats["total_processed"] += 1
                        self._stats["peak_used"] = max(
                            self._stats["peak_used"], self.used_connections)
                        self._stats["queue_wait_total_ms"] += (
                            time.time() - waiter.enqueued) * 1000
                        # Let other fitting waiters backfill immediately.
                        self._cond.notify_all()
                        return Reservation(self, cost)

                    remaining = deadline - time.time()
                    if remaining <= 0:
                        raise QueueTimeout(
                            f"Request timed out after {timeout:.0f}s in queue")
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        raise QueueTimeout(
                            f"Request timed out after {timeout:.0f}s in queue")
            except QueueTimeout:
                self._stats["total_timeout"] += 1
                raise
            except asyncio.CancelledError:
                self._stats["total_cancelled"] += 1
                raise
            finally:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                self._cond.notify_all()

    async def _release(self, cost: int, aborted: bool = False):
        async with self._cond:
            self.used_connections = max(0, self.used_connections - cost)
            if aborted:
                self._stats["total_aborted"] += 1
            self._cond.notify_all()

    # ---- introspection ----

    def stats(self) -> dict:
        ordered = self._ordered()
        now = time.time()
        processed = self._stats["total_processed"] or 1
        prio_counts: dict[int, int] = {}
        for w in self._waiters:
            prio_counts[w.priority] = prio_counts.get(w.priority, 0) + 1
        oldest_wait = max((now - w.enqueued for w in ordered), default=0.0)
        return {
            **self._stats,
            "max_connections": self.max_connections,
            "used_connections": self.used_connections,
            "free_connections": self.free_connections,
            "utilization": round(self.used_connections / self.max_connections, 4),
            "queue_size": len(self._waiters),
            "queue_by_priority": prio_counts,
            "oldest_wait_seconds": round(oldest_wait, 2),
            "avg_wait_ms": round(self._stats["queue_wait_total_ms"] / processed, 1),
            "waiting": [
                {
                    "priority": w.priority,
                    "cost": w.cost,
                    "model": w.model,
                    "name": w.name,
                    "wait_seconds": round(now - w.enqueued, 2),
                }
                for w in ordered[:25]
            ],
        }
