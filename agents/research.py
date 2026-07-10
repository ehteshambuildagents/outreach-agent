"""Back-compat shim.

The research agent has been re-architected into the evidence-first `research`
package (research/pipeline.py, fetcher.py, crawler.py, cleaner.py, classifier.py,
extractor.py, verifier.py, hooks.py). This module simply re-exports the public
entry point so existing imports (`from agents.research import research_company`)
keep working.
"""

from research.pipeline import research_company

__all__ = ["research_company"]
