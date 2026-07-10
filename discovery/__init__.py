"""Prospect Discovery Agent — find companies that match an ICP.

Given a target profile (industry, location, size, stage, keywords), it DISCOVERS
candidate companies using the existing search providers (Tavily + Exa). It does
NOT qualify, research deeply, write, or send — those stay with the other agents.
Discovery is deterministic: the providers do the searching, and ranking / dedupe
/ filtering / pagination are pure Python. An LLM is only ever used upstream (the
chat agent) to turn a free-text request into structured filters.

    query (filters) -> sources (Tavily/Exa) -> engine (dedupe/filter/rank/page)
                    -> prospects [structured JSON] -> store (Postgres/SQLite)
"""
