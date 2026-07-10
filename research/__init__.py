"""Evidence-first company research engine.

Pipeline stages (each in its own module, single responsibility):

    crawler  -> fetcher -> cleaner -> classifier
                                   -> extractor (LLM, evidence-bearing)
                                   -> verifier  (quote-grounding + corroboration)
                                   -> graph/hooks (ranked, scored)
                                   -> pipeline   (orchestration + output)

Public entry point: ``research.pipeline.research_company``.
"""

from research.pipeline import research_company

__all__ = ["research_company"]
