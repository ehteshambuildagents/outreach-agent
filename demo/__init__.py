"""Public live-demo pipeline.

A no-login visitor gives an ICP (or their own website); this runs the SAME real
agents the product uses — discovery → research → qualification → writer — over
several candidates and streams the work as it happens. It deliberately stops
BEFORE anything that needs Gmail (no send, no reply detection).

Transport (SSE) + abuse controls live in ``server/demo_api.py``; the pipeline
orchestration lives in ``demo/runner.py``. This package adds no new agent logic —
it is a new, bounded ENTRY POINT into the existing ones.
"""

from demo.runner import run_demo, GMAIL_PENDING_MESSAGE

__all__ = ["run_demo", "GMAIL_PENDING_MESSAGE"]
