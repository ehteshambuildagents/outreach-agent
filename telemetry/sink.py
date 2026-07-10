"""The telemetry sink — durable, asynchronous, idempotent, and crash-safe.

Recording a datapoint is ``emit(table, row)``: in production it enqueues onto a
bounded in-memory queue drained by a single daemon writer thread, so instrumenting
an AI call adds ~microseconds and NEVER blocks the request or the event loop. The
writer batches inserts through the EXISTING :class:`automation.db.Database` (no new
connection pool). Every insert is ``ON CONFLICT(id) DO NOTHING`` so a retry or a
double-emit is a no-op (idempotent), and every write is wrapped so telemetry can
never raise into production.

Modes:
  * default (async)     — enqueue + background flush.
  * ``TELEMETRY_SYNC=1`` — write inline (deterministic; used by the test suite).
  * ``TELEMETRY_DISABLED=1`` — drop everything (a hard kill switch).

Under back-pressure (queue full) datapoints are dropped rather than blocking —
observability must never throttle the product.
"""

import os
import queue
import threading
import time

from automation.db import Database
from telemetry import schema

_MAX_QUEUE = 20000
_TABLE_WHITELIST = {"ai_requests", "agent_runs", "telemetry_events"}


def _flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes")


class _Sink:
    def __init__(self):
        self._q = queue.Queue(maxsize=_MAX_QUEUE)
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._db = Database()            # shared; thread-local connections internally
        self.dropped = 0                 # observability of the observer

    # ── public ─────────────────────────────────────────────────────────
    def emit(self, table: str, row: dict) -> None:
        if _flag("TELEMETRY_DISABLED") or table not in _TABLE_WHITELIST or "id" not in row:
            return
        if _flag("TELEMETRY_SYNC"):
            self._write(table, row)
            return
        try:
            self._q.put_nowait((table, row))
        except queue.Full:
            self.dropped += 1            # never block production
            return
        self._ensure_thread()

    def flush(self, timeout: float = 5.0) -> None:
        """Best-effort drain — used by tests and graceful shutdown."""
        if _flag("TELEMETRY_SYNC"):
            return
        end = time.time() + timeout
        while ((not self._q.empty()) or getattr(self._q, "unfinished_tasks", 0)) \
                and time.time() < end:
            time.sleep(0.01)

    def stop(self, timeout: float = 5.0) -> None:
        self.flush(timeout)
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    # ── internals ──────────────────────────────────────────────────────
    def _ensure_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="telemetry-sink",
                                            daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        schema.ensure(self._db)
        while not self._stop.is_set():
            try:
                table, row = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            self._write(table, row)
            self._q.task_done()

    def _write(self, table: str, row: dict) -> None:
        try:
            with self._write_lock:
                schema.ensure(self._db)
                cols = list(row.keys())
                placeholders = ",".join(["?"] * len(cols))
                sql = (f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
                       "ON CONFLICT(id) DO NOTHING")
                self._db.execute(sql, [row[c] for c in cols])
        except Exception:  # noqa: BLE001 - telemetry must never break production
            pass


_sink = None
_sink_lock = threading.Lock()


def sink() -> "_Sink":
    global _sink
    if _sink is None:
        with _sink_lock:
            if _sink is None:
                _sink = _Sink()
    return _sink


def emit(table: str, row: dict) -> None:
    sink().emit(table, row)


def flush(timeout: float = 5.0) -> None:
    sink().flush(timeout)


def stop(timeout: float = 5.0) -> None:
    if _sink is not None:
        _sink.stop(timeout)
