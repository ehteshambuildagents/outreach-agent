"""Provider pricing — cost is COMPUTED from provider-returned token counts.

We never fabricate tokens: the counts come straight from the API response
(Anthropic returns ``usage.input_tokens`` / ``output_tokens``). The only estimate
is the published $/token rate, applied to those real counts — so ``estimated_cost``
is an accurate dollar figure with ``cost_basis="provider_tokens"``. When a provider
returns no usage, cost is recorded as ``None`` / ``cost_basis="unavailable"`` rather
than guessed.

Rates are USD per 1M tokens (input, output), matched by substring so model version
suffixes still resolve. Update here when provider pricing changes — one place.
"""

# (input_per_mtok, output_per_mtok)
_PRICES = {
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku": (0.80, 4.0),
    "claude-fable": (3.0, 15.0),      # priced like sonnet until published
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.5, 10.0),
    "gpt-4": (30.0, 60.0),
}
_DEFAULT = (3.0, 15.0)                 # unknown model -> mid-range (sonnet-like)


def rate(model: str):
    m = (model or "").lower()
    for key, price in _PRICES.items():
        if key in m:
            return price
    return _DEFAULT


def cost(model: str, prompt_tokens, completion_tokens):
    """USD cost from REAL token counts, or None if counts are unavailable."""
    if prompt_tokens is None and completion_tokens is None:
        return None
    p_in, p_out = rate(model)
    return round((int(prompt_tokens or 0) * p_in
                  + int(completion_tokens or 0) * p_out) / 1_000_000, 6)
