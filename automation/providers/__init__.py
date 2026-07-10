"""Email providers behind one interface. The engine talks only to ``EmailProvider``
and never to Gmail/Outlook directly, so provider differences live in exactly one
place each.

    get_provider("dryrun")                      -> deterministic, no network (tests/local)
    get_provider("gmail",   credentials=token)  -> real Gmail REST
    get_provider("outlook", credentials=token)  -> real Microsoft Graph
"""

from automation.providers.base import (
    EmailProvider,
    ProviderError,
    ProviderNotConfigured,
    SendResult,
)
from automation.providers.dryrun import DryRunProvider
from automation.providers.gmail import GmailProvider
from automation.providers.outlook import OutlookProvider

_REGISTRY = {
    "dryrun": DryRunProvider,
    "gmail": GmailProvider,
    "outlook": OutlookProvider,
}


def get_provider(name: str, credentials=None) -> EmailProvider:
    cls = _REGISTRY.get((name or "dryrun").lower(), DryRunProvider)
    return cls(credentials=credentials)


__all__ = ["EmailProvider", "ProviderError", "ProviderNotConfigured",
           "SendResult", "DryRunProvider", "GmailProvider", "OutlookProvider",
           "get_provider"]
