"""The background worker — a real scheduler that drives the engine over time.

One worker process per deployment. It is NOT a busy ``while True`` spin: a single
scheduler thread blocks on a ``threading.Event`` between ticks, so it wakes on a
fixed cadence and returns *instantly* on shutdown. Responsibilities:

    on start      -> recover() any workflow caught mid-send by a previous crash
    every tick    -> engine.tick() advances all due workflows (locked, idempotent)
    every maint   -> refresh access tokens nearing expiry; renew Gmail/Graph watches
    on stop       -> set the event, join the thread (graceful, bounded)

Because SQLite is the source of truth and every send is idempotent, a restart is
safe at any moment: unfinished work is simply picked up again. Nothing here
generates content or calls an LLM — it only schedules and sends.
"""

import logging
import threading
import time

from automation import engine, metrics, tokens
from automation.store import WorkflowStore
from automation.tokens import TokenStore
from config.settings import (
    AUTOMATION_WATCH_RENEW_BEFORE,
    AUTOMATION_WORKER_MAINT_SECONDS,
    AUTOMATION_WORKER_TICK_SECONDS,
)

log = logging.getLogger("automation.worker")


class Worker:
    def __init__(self, store: WorkflowStore = None, token_store: TokenStore = None, *,
                 tick_interval: float = AUTOMATION_WORKER_TICK_SECONDS,
                 maint_interval: float = AUTOMATION_WORKER_MAINT_SECONDS,
                 credentials_provider=tokens.credentials_provider):
        self.store = store or WorkflowStore()
        self.tokens = token_store or tokens.default_store()
        self.tick_interval = tick_interval
        self.maint_interval = maint_interval
        self.credentials_provider = credentials_provider
        self._stop = threading.Event()
        self._thread = None
        self._last_maint = 0.0
        self.last_tick_at = 0.0            # heartbeat for health checks

    # ── lifecycle ──────────────────────────────────────────────────────
    def start(self) -> "Worker":
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        repaired = engine.recover(self.store)      # crash recovery on startup
        log.info('automation worker starting (recovered %d workflows)', repaired)
        self._thread = threading.Thread(target=self._loop, name="automation-worker",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        log.info("automation worker stopped")

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── the scheduled loop (Event.wait, never a busy spin) ─────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:            # noqa: BLE001 - a tick must never kill the loop
                log.error("worker tick failed: %s", exc, exc_info=True)
            self._stop.wait(self.tick_interval)  # blocks; wakes early on stop()

    def run_once(self, *, now=None) -> int:
        """One scheduler beat: advance due workflows, and run maintenance when due.
        Exposed for tests so the whole worker body runs without a thread."""
        now = time.time() if now is None else now
        processed = engine.tick(self.store, now=now,
                                credentials_provider=self.credentials_provider)
        self.last_tick_at = now
        if now - self._last_maint >= self.maint_interval:
            self._maintenance(now=now)
            self._last_maint = now
        if processed:
            log.debug("worker advanced %d workflows", processed)
        return processed

    # ── maintenance: token refresh + watch renewal ─────────────────────
    def _maintenance(self, *, now=None) -> None:
        now = time.time() if now is None else now
        self._refresh_expiring_tokens(now)
        self._renew_watches(now)

    def _refresh_expiring_tokens(self, now) -> None:
        for acct in self.tokens.due_for_refresh(now=now):
            try:
                # valid_access_token refreshes + persists (or flags reconnect).
                self.tokens.valid_access_token(
                    acct["user_id"], acct["provider"], acct["account_email"], now=now)
            except Exception as exc:            # noqa: BLE001
                metrics.incr("oauth_failures")
                log.warning("token refresh failed for %s/%s: %s",
                            acct["provider"], acct["account_email"], type(exc).__name__)

    def _renew_watches(self, now) -> None:
        from automation.providers import get_provider
        for provider in ("gmail", "outlook"):
            for rec in self.tokens.with_watch(provider):
                exp = (rec.get("watch_state") or {}).get("expiration")
                if exp and float(exp) - now > AUTOMATION_WATCH_RENEW_BEFORE:
                    continue                    # still fresh
                token = self.tokens.valid_access_token(
                    rec["user_id"], provider, rec["account_email"], now=now)
                if not token:
                    continue                    # reconnect needed; skip quietly
                try:
                    state = get_provider(provider, credentials=token).watch(
                        user_id=rec["user_id"])
                    self.tokens.set_watch_state(rec["user_id"], provider,
                                                rec["account_email"], state or {})
                except Exception as exc:        # noqa: BLE001
                    metrics.incr("provider_failures")
                    log.warning("watch renewal failed for %s/%s: %s",
                                provider, rec["account_email"], type(exc).__name__)


# Module-level singleton so the API and a standalone entrypoint share one worker.
_worker = None


def get_worker() -> Worker:
    global _worker
    if _worker is None:
        _worker = Worker()
    return _worker


def start() -> Worker:
    return get_worker().start()


def stop() -> None:
    if _worker is not None:
        _worker.stop()


if __name__ == "__main__":     # pragma: no cover - standalone entrypoint
    import signal

    from config.env import load_env
    load_env()                 # same .env.local + .env as the API/migrate/verifier

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    w = start()

    def _graceful(*_a):
        log.info("signal received — shutting down worker")
        stop()

    signal.signal(signal.SIGINT, _graceful)
    signal.signal(signal.SIGTERM, _graceful)
    while w.running:               # main thread parks; loop lives in the worker thread
        time.sleep(1)
