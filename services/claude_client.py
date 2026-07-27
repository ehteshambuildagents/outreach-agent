"""Low-level Claude API utilities.

Two responsibilities only:
  * `_call_model` — make ONE structured-output call and return parsed JSON,
    translating SDK/network errors into a single safe exception type.
  * `is_company_member` — the team-strictness predicate (the company's OWN
    people only), reused by the extractor and verifier.

The key is read from config.settings (never hard-coded, never logged). Prompts
and schemas live in the research package (research/extractor.py).
"""

import json
import logging
import random
import re
import time

import anthropic

from telemetry.instrument import llm_span   # records tokens/cost/latency; never raises
from config.settings import (
    API_BACKOFF_BASE_SECONDS,
    API_BACKOFF_MAX_SECONDS,
    API_MAX_RETRIES,
    EMPTY_RESPONSE_RETRIES,
    CHAT_MAX_TOKENS,
    FAST_MODEL,
    QUALITY_MODEL,
    REQUEST_MAX_TOKENS,
    get_api_key,
)


log = logging.getLogger("saqua.claude_client")


class ClaudeClientError(Exception):
    """Raised for any failure talking to Claude. Messages are user-safe."""


# ── Per-user usage cap (public-signup safety) ──────────────────────────
# Metered per user via the ambient telemetry context, so no signature changes.
# A system call (no user in context) is never capped. Best-effort: any internal
# error fails OPEN so metering can't take down the model call.
def _cap_user():
    try:
        from telemetry import context
        return context.get("user_id")
    except Exception:  # noqa: BLE001
        return None


def _enforce_anthropic_cap() -> None:
    """Raise a user-safe ClaudeClientError if this user is over their limit."""
    user_id = _cap_user()
    if not user_id:
        return
    try:
        import limits
        decision = limits.allow("anthropic", user_id)
    except Exception:  # noqa: BLE001
        return
    if decision is not None and not decision.allowed:
        raise ClaudeClientError(decision.reason or "Usage limit reached. Please try later.")


def _record_anthropic() -> None:
    user_id = _cap_user()
    if not user_id:
        return
    try:
        import limits
        limits.record("anthropic", user_id)
    except Exception:  # noqa: BLE001
        pass


# Errors worth retrying: transient transport/rate-limit/server faults. Client
# errors (auth, 400/422 schema, not-found) are NOT retryable and fail fast.
_RETRYABLE_ERRORS = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,   # includes APITimeoutError
)

_QUALITY_STAGES = {
    "writer",
    "writer_refine",
    "channel_writer",
    "email_writer",
    "subject_writer",
    "sequence_writer",
    "followup_writer",
}


def _is_retryable(exc) -> bool:
    if isinstance(exc, _RETRYABLE_ERRORS):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return getattr(exc, "status_code", 0) >= 500  # 5xx only; never 4xx
    return False


def _retry_after_seconds(exc):
    """Honour a server Retry-After header when present (respect rate limits)."""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


def _backoff_delay(attempt: int, exc) -> float:
    """Exponential backoff with jitter; never below a server Retry-After."""
    delay = min(API_BACKOFF_MAX_SECONDS,
                API_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None and retry_after > 0:
        delay = max(delay, min(retry_after, 60.0))  # respect, but bound
    return delay + random.uniform(0, delay * 0.25)  # jitter


def _with_retry(call, *, max_retries: int = API_MAX_RETRIES, sleep=time.sleep):
    """Call `call()`, retrying only retryable failures with bounded backoff."""
    attempt = 0
    while True:
        try:
            return call()
        except Exception as exc:                 # noqa: BLE001 - re-raised below
            attempt += 1
            if attempt > max_retries or not _is_retryable(exc):
                raise
            sleep(_backoff_delay(attempt, exc))


# Role markers that mark a person as NOT the company's own staff. A defensive
# net in addition to the extraction prompt.
_EXTERNAL_ROLE_MARKERS = (
    "investor", "backer", "angel", "advisor", "board member", "board of",
    "(customer)", "(client)", "(partner)", "partner at", "testimonial",
    "mascot", "fictional", "ai character", "ai persona",
    # Uncertainty markers => the name was inferred, not stated:
    "likely", "possibly", "presumably", "appears to be", "guess",
)


def is_company_member(member) -> bool:
    """True if this looks like the company's OWN person (not investor/customer/etc.).

    Rejects roles that mark someone as external (investor/backer/advisor/
    customer/partner), uncertain/inferred, or a mascot/fictional character.
    """
    if not isinstance(member, dict):
        return False
    name = member.get("name")
    if not (isinstance(name, str) and name.strip()):
        return False
    role = (member.get("role") or "").lower()
    return not any(marker in role for marker in _EXTERNAL_ROLE_MARKERS)


def _select_model(stage: str = "model") -> str:
    """Route only final/reply-critical writing work to the quality model."""
    return QUALITY_MODEL if _stage_label(stage) in _QUALITY_STAGES else FAST_MODEL


def _call_model(system_prompt: str, schema: dict, user_content: str,
                max_tokens: int = REQUEST_MAX_TOKENS,
                stage: str = "model") -> dict:
    """One structured-output call. Returns parsed JSON dict or raises ClaudeClientError.

    An EMPTY completion is retried here rather than raised. _with_retry wraps
    only the SDK create(), so a response that arrived fine but carried no text
    was the single transient fault that never got a second attempt: one flaky
    empty answer permanently failed a campaign prospect we had already paid to
    research and qualify (observed live on openai.com, where the identical
    input then succeeded three times out of three).
    """
    last_empty = None
    for _ in range(1 + EMPTY_RESPONSE_RETRIES):
        text = _one_structured_call(system_prompt, schema, user_content,
                                    max_tokens, stage)
        if text.strip():
            return _parse_json(text)
        last_empty = text
        log.info("empty completion at stage=%s; retrying", stage)
    return _parse_json(last_empty or "")     # raises the empty-response error


def _one_structured_call(system_prompt: str, schema: dict, user_content: str,
                         max_tokens: int, stage: str) -> str:
    """One metered attempt. Returns the response text (possibly empty)."""
    selected_model = _select_model(stage)
    _enforce_anthropic_cap()                        # per-user usage cap (never breaks a system call)
    token_estimate = _estimate_tokens(system_prompt, user_content, json.dumps(schema, ensure_ascii=False))
    span = llm_span("anthropic", selected_model)   # telemetry: tokens/cost/latency
    started = time.perf_counter()
    try:
        response = _create_structured(
            span, system_prompt, schema, user_content, max_tokens, stage,
            selected_model, token_estimate,
        )
    except ClaudeClientError as exc:
        span.failed(exc)
        _log_request_metrics(
            stage=stage,
            model=selected_model,
            input_token_estimate=token_estimate,
            output_tokens=None,
            latency_ms=_elapsed_ms(started),
            success=False,
        )
        raise
    span.done(response)
    _record_anthropic()                             # meter the real, paid model call
    _log_request_metrics(
        stage=stage,
        model=selected_model,
        input_token_estimate=token_estimate,
        output_tokens=_output_tokens(response),
        latency_ms=_elapsed_ms(started),
        success=True,
    )
    return _first_text(response)


def _create_structured(span, system_prompt, schema, user_content, max_tokens,
                       stage="model", selected_model: str | None = None,
                       token_estimate: int | None = None):
    """Make the structured-output create() with retry + uniform error translation.
    Returns the raw SDK response (telemetry reads usage off it in the caller)."""
    # max_retries=0: our own _with_retry is the single source of retries, so the
    # SDK's built-in retry doesn't compound on top of it.
    selected_model = selected_model or _select_model(stage)
    client = anthropic.Anthropic(api_key=get_api_key(), max_retries=0)
    try:
        try:
            return _with_retry(span.counted(lambda: client.messages.create(
                model=selected_model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )))
        except TypeError:
            # Older SDK without output_config -> rely on the prompt for JSON.
            return _with_retry(span.counted(lambda: client.messages.create(
                model=selected_model,
                max_tokens=max_tokens,
                system=system_prompt
                + "\n\nRespond with ONLY the JSON object — no prose, no fences.",
                messages=[{"role": "user", "content": user_content}],
            )))
    except anthropic.AuthenticationError as exc:
        raise ClaudeClientError(
            "Authentication failed. Check that ANTHROPIC_API_KEY in your .env "
            "file is a valid key."
        ) from exc
    except anthropic.RateLimitError as exc:
        raise ClaudeClientError(
            "Rate limited by the Anthropic API. Please wait and try again."
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise ClaudeClientError(
            "Could not reach the Anthropic API. Check your network connection."
        ) from exc
    except anthropic.APIStatusError as exc:
        _log_status_error(exc, stage=stage, model=selected_model, token_estimate=token_estimate)
        raise ClaudeClientError(_user_status_error(exc, stage=stage)) from exc
    except anthropic.APIError as exc:
        raise ClaudeClientError("Unexpected Anthropic API error.") from exc


def _translate_api_errors(make_call, *, stage: str = "model",
                          model: str | None = None,
                          token_estimate: int | None = None):
    """Run one SDK create() through retry + uniform, user-safe error translation.
    Shared so every call type (structured output, tool-use) fails identically."""
    try:
        return _with_retry(make_call)
    except anthropic.AuthenticationError as exc:
        raise ClaudeClientError(
            "Authentication failed. Check that ANTHROPIC_API_KEY in your .env "
            "file is a valid key."
        ) from exc
    except anthropic.RateLimitError as exc:
        raise ClaudeClientError(
            "Rate limited by the Anthropic API. Please wait and try again."
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise ClaudeClientError(
            "Could not reach the Anthropic API. Check your network connection."
        ) from exc
    except anthropic.APIStatusError as exc:
        model = model or _select_model(stage)
        _log_status_error(exc, stage=stage, model=model, token_estimate=token_estimate)
        raise ClaudeClientError(_user_status_error(exc, stage=stage)) from exc
    except anthropic.APIError as exc:
        raise ClaudeClientError("Unexpected Anthropic API error.") from exc


def call_with_tools(system_prompt: str, messages: list, tools: list,
                    max_tokens: int = CHAT_MAX_TOKENS) -> dict:
    """One tool-use turn for the chat agent. SDK-agnostic return so callers (and
    tests) never touch anthropic objects:

        {"stop_reason": str, "text": str,
         "tool_uses": [{"id","name","input"}],
         "assistant_content": [<content-block dicts to replay as the assistant turn>]}

    Raises ClaudeClientError on any API failure (message is already user-safe).
    """
    stage = "chat"
    selected_model = _select_model(stage)
    _enforce_anthropic_cap()                        # per-user usage cap (never breaks a system call)
    span = llm_span("anthropic", selected_model)   # telemetry: tokens/cost/latency
    client = anthropic.Anthropic(api_key=get_api_key(), max_retries=0)
    token_estimate = _estimate_tokens(system_prompt, json.dumps(messages, ensure_ascii=False),
                                      json.dumps(tools, ensure_ascii=False))
    started = time.perf_counter()
    try:
        response = _translate_api_errors(span.counted(lambda: client.messages.create(
            model=selected_model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
            tools=tools,
        )), stage=stage, model=selected_model, token_estimate=token_estimate)
    except ClaudeClientError as exc:
        span.failed(exc)
        _log_request_metrics(
            stage=stage,
            model=selected_model,
            input_token_estimate=token_estimate,
            output_tokens=None,
            latency_ms=_elapsed_ms(started),
            success=False,
        )
        raise
    span.done(response)
    _record_anthropic()                             # meter the real, paid model call
    _log_request_metrics(
        stage=stage,
        model=selected_model,
        input_token_estimate=token_estimate,
        output_tokens=_output_tokens(response),
        latency_ms=_elapsed_ms(started),
        success=True,
    )
    return _normalize_tool_response(response)


def _estimate_tokens(*parts: str) -> int:
    chars = sum(len(p or "") for p in parts)
    return max(1, round(chars / 4))


def _output_tokens(response) -> int | None:
    usage = getattr(response, "usage", None)
    return getattr(usage, "output_tokens", None) if usage is not None else None


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _response_body(exc) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return ""
    for attr in ("text", "content"):
        value = getattr(response, attr, None)
        if callable(value):
            try:
                value = value()
            except TypeError:
                value = None
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        if isinstance(value, str) and value.strip():
            return _sanitize_body(value)
    try:
        body = getattr(exc, "body", None)
        if body:
            return _sanitize_body(json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body)
    except Exception:  # noqa: BLE001 - diagnostics must not mask the original error
        pass
    return ""


def _sanitize_body(body: str) -> str:
    body = (body or "").replace("\x00", "").strip()
    body = re.sub(r"sk-ant-[A-Za-z0-9_-]+", "sk-ant-[redacted]", body)
    body = re.sub(r"(?i)(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+", r"\1[redacted]", body)
    return body[:2000]


def _safe_reason_from_body(body: str) -> str:
    if not body:
        return ""
    try:
        parsed = json.loads(body)
        err = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(err, dict):
            msg = err.get("message") or err.get("type")
        elif isinstance(err, str):
            msg = err
        else:
            msg = parsed.get("message") if isinstance(parsed, dict) else ""
        if msg:
            return _sanitize_user_reason(str(msg))
    except Exception:  # noqa: BLE001
        pass
    return _sanitize_user_reason(body)


def _sanitize_user_reason(reason: str) -> str:
    reason = _sanitize_body(reason)
    reason = " ".join(reason.split())
    return reason[:300] or "invalid request"


def _stage_label(stage: str) -> str:
    stage = (stage or "model").strip().lower()
    stage = re.sub(r"[^a-z0-9_-]+", "_", stage).strip("_")
    return stage or "model"


def _stage_user_label(stage: str) -> str:
    return _stage_label(stage).replace("_", " ")


def _log_request_metrics(*, stage: str, model: str, input_token_estimate: int,
                         output_tokens: int | None, latency_ms: float,
                         success: bool) -> None:
    log.info(
        "anthropic_request stage=%s selected_model=%s input_tokens_estimate=%s "
        "output_tokens=%s latency_ms=%.1f success=%s",
        _stage_label(stage), model, input_token_estimate, output_tokens, latency_ms, success,
    )


def _log_status_error(exc, *, stage: str, model: str, token_estimate: int | None) -> None:
    body = _response_body(exc)
    log.error(
        "anthropic_api_status_error stage=%s model=%s status_code=%s token_estimate=%s response_body=%r",
        _stage_label(stage), model, getattr(exc, "status_code", None), token_estimate, body,
    )


def _user_status_error(exc, *, stage: str) -> str:
    status = getattr(exc, "status_code", None)
    body = _response_body(exc)
    reason = _safe_reason_from_body(body)
    label = _stage_user_label(stage)
    if status == 400:
        return f"Anthropic rejected the {label} request: {reason or 'invalid request'}."
    return f"The Anthropic API returned an error (HTTP {status})."


def _normalize_tool_response(response) -> dict:
    text_parts, tool_uses, assistant_content = [], [], []
    for block in getattr(response, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            text = getattr(block, "text", "") or ""
            text_parts.append(text)
            assistant_content.append({"type": "text", "text": text})
        elif btype == "tool_use":
            payload = getattr(block, "input", None) or {}
            block_id = getattr(block, "id", None)
            name = getattr(block, "name", None)
            tool_uses.append({"id": block_id, "name": name, "input": payload})
            assistant_content.append({"type": "tool_use", "id": block_id,
                                      "name": name, "input": payload})
    return {
        "stop_reason": getattr(response, "stop_reason", None),
        "text": "\n".join(p for p in text_parts if p).strip(),
        "tool_uses": tool_uses,
        "assistant_content": assistant_content,
    }


def _first_text(response) -> str:
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            return block.text or ""
    return ""


def _parse_json(text: str) -> dict:
    if not text or not text.strip():
        raise ClaudeClientError("The model returned an empty response.")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise ClaudeClientError("The model did not return valid JSON.")
