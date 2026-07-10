"""Automation Agent — the conductor that executes outreach workflows.

It orchestrates the existing agents' OUTPUTS; it never researches, never writes,
never rewrites. It decides WHAT happens next (schedule, send, wait, check for a
reply, follow up, stop) and drives a persisted state machine so a workflow
survives restarts and crashes.

Layers (each small and testable):
    states    — the explicit workflow/step state machine
    models    — Workflow + Step dataclasses (JSON-safe, artifact-linked)
    store     — transactional SQLite persistence (recovery source of truth)
    scheduler — deterministic next-run / backoff / timezone maths
    redis     — Upstash REST: locks, dedup, rate-limit (ephemeral only)
    providers — Gmail / Outlook / dry-run behind one interface
    metrics   — counters for observability
    engine    — the conductor: create, tick, send, reply-ingest, recover
"""
