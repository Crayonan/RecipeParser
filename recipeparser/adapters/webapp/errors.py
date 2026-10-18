"""
recipeparser/adapters/webapp/errors.py — turn any failure into "what happened, why, how to fix it".

Every error the web app records goes through ``explain``: an exception from the
model API (with its HTTP code and gRPC status), a timeout, a parse failure, a
validation failure, a missing book, a crash. The result is the row the Errors
views show and the line the console prints, so the person reading it never has
to open a terminal to find out what a ``429`` or a ``404`` meant for this run.

Severity:
  * ``warning`` — handled automatically (retried, skipped and reported), the run goes on.
  * ``error``   — work was lost: a chunk or recipe failed for good.
  * ``fatal``   — the run cannot continue.
"""

from __future__ import annotations

import json
import re
import traceback
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class Explanation:
    code: str
    category: str
    severity: str
    title: str
    message: str
    cause: str
    fix: str
    status_code: Optional[int] = None
    status: Optional[str] = None
    exception_type: Optional[str] = None
    traceback: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# (code, title, cause, fix, category, retried-automatically)
_HTTP: Dict[int, Tuple[str, str, str, str, bool]] = {
    400: (
        "Bad request",
        "The model API rejected the request as malformed: an invalid parameter, a response schema it "
        "will not accept, a prompt it cannot read, or an API key it does not recognise.",
        "Read the message: 'API key not valid' means fix GOOGLE_API_KEY in Settings → API keys. Otherwise "
        "check the model name (Settings → Provider & models), GEMINI_THINKING_LEVEL, and any edited prompt "
        "(Prompts) for a change that broke the request.",
        "request",
        False,
    ),
    401: (
        "Not authenticated",
        "The API key is missing or was not accepted.",
        "Set a valid GOOGLE_API_KEY in Settings → API keys (get one at aistudio.google.com/app/apikey).",
        "auth",
        False,
    ),
    403: (
        "Permission denied",
        "The key is valid but not allowed to do this: the Generative Language API is not enabled for its "
        "project, the key is restricted, or the model is not available to this account or region.",
        "Enable the Generative Language API for the key's Google Cloud project, lift the key's API "
        "restrictions, or choose a model the account can use (Settings → Provider & models).",
        "auth",
        False,
    ),
    404: (
        "Model or resource not found",
        "The model name does not exist (a typo, a retired model, or a name missing its 'models/' prefix "
        "for embeddings) or the endpoint is wrong.",
        "Pick a model from the list in Settings → Provider & models. Generation: GEMINI_MODEL; embeddings: "
        "GEMINI_EMBEDDING_MODEL. Retired models (e.g. gemini-2.5-flash after 2026-10-16) answer 404.",
        "config",
        False,
    ),
    408: (
        "Request timeout (server side)",
        "The API gave up waiting for the request.",
        "Usually transient. If it repeats, lower concurrency or chunk size (RP_MAX_CHUNK_CHARS).",
        "transient",
        True,
    ),
    409: (
        "Conflict",
        "The API reported a conflicting request.",
        "Retry the run; report it if it repeats.",
        "transient",
        False,
    ),
    413: (
        "Request too large",
        "The prompt (chunk text plus instructions) exceeded what the API accepts in one request.",
        "Lower RP_MAX_CHUNK_CHARS in Settings → Chunking so books are cut into smaller chunks.",
        "request",
        False,
    ),
    429: (
        "Rate limit or quota exhausted",
        "Too many requests per minute for this key and model, or the daily/monthly quota is used up "
        "(RESOURCE_EXHAUSTED). The pipeline retries with exponential back-off (RP_MAX_RETRIES, "
        "RP_BACKOFF_BASE_SECS, RP_BACKOFF_MAX_SECS).",
        "Lower Requests per minute or Concurrency for the run, or raise the key's quota/tier. If every call "
        "fails with 429 the daily quota is spent: wait for the reset or use another key.",
        "rate_limit",
        True,
    ),
    499: (
        "Request cancelled",
        "The request was cancelled before it finished.",
        "Expected after Stop or Kill; otherwise retry.",
        "transient",
        False,
    ),
    500: (
        "Model API internal error",
        "The model API failed on its side (INTERNAL). Retried automatically with back-off.",
        "Nothing to change if retries succeed. If a chunk fails repeatedly, the chunk may provoke it: try "
        "smaller chunks (RP_MAX_CHUNK_CHARS) or another model.",
        "transient",
        True,
    ),
    502: (
        "Bad gateway",
        "A proxy between here and the model API failed. Retried automatically.",
        "Usually transient; check network/proxy if it repeats.",
        "transient",
        True,
    ),
    503: (
        "Model API unavailable / overloaded",
        "The model is overloaded or temporarily unavailable (UNAVAILABLE). Retried automatically.",
        "Wait and retry, lower concurrency, or choose a less loaded model.",
        "transient",
        True,
    ),
    504: (
        "Deadline exceeded (server side)",
        "The API did not finish within its own deadline (DEADLINE_EXCEEDED). Retried automatically.",
        "Lower RP_MAX_CHUNK_CHARS so each request is smaller, or lower GEMINI_THINKING_LEVEL.",
        "transient",
        True,
    ),
}


def _short_message(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text if len(text) <= 2000 else text[:2000] + " …"


def _http_explanation(code: int, status: Optional[str], message: str, exc_type: str) -> Explanation:
    if code in _HTTP:
        title, cause, fix, category, retried = _HTTP[code]
    elif 400 <= code < 500:
        title, cause, fix, category, retried = (
            "Client error",
            "The model API refused the request.",
            "Read the message below; check the API key, the model name and any edited prompt.",
            "request",
            False,
        )
    elif 500 <= code < 600:
        title, cause, fix, category, retried = (
            "Server error",
            "The model API failed on its side.",
            "Usually transient; retry the run or its failed chunks.",
            "transient",
            True,
        )
    else:
        title, cause, fix, category, retried = (
            "Unexpected API status",
            "The model API answered with a status this app does not know.",
            "Read the message below.",
            "unknown",
            False,
        )
    if code == 400 and "api key" in message.lower():
        title, category = "API key not valid", "auth"
        cause = "The model API did not accept GOOGLE_API_KEY."
        fix = "Paste a valid key in Settings → API keys and start the run again."
    return Explanation(
        code=f"HTTP {code}",
        category=category,
        severity="warning" if retried else "error",
        title=title + (f" ({status})" if status else ""),
        message=message,
        cause=cause,
        fix=fix,
        status_code=code,
        status=status,
        exception_type=exc_type,
    )


def explain(exc: BaseException, *, with_traceback: bool = True) -> Explanation:
    """Explain one exception. Never raises."""
    try:
        return _explain(exc, with_traceback)
    except Exception as inner:  # pragma: no cover - the explainer must not become the failure
        return Explanation(
            code="UNEXPECTED",
            category="unknown",
            severity="error",
            title=type(exc).__name__,
            message=_short_message(exc),
            cause=f"(The error explainer itself failed: {inner!r})",
            fix="See the traceback.",
            exception_type=type(exc).__name__,
        )


def _explain(exc: BaseException, with_traceback: bool) -> Explanation:
    exc_type = type(exc).__name__
    message = _short_message(exc)
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if with_traceback else None

    def done(e: Explanation) -> Explanation:
        e.exception_type = e.exception_type or exc_type
        e.traceback = tb
        return e

    # google-genai APIError carries .code (HTTP) and .status (gRPC name).
    code = getattr(exc, "code", None)
    if isinstance(code, int) and 100 <= code < 600 and _is_api_error(exc):
        status = getattr(exc, "status", None)
        api_message = getattr(exc, "message", None) or message
        return done(_http_explanation(code, status if isinstance(status, str) else None, str(api_message), exc_type))

    mod = type(exc).__module__ or ""
    if "Timeout" in exc_type or isinstance(exc, TimeoutError):
        return done(
            Explanation(
                code="TIMEOUT",
                category="timeout",
                severity="error",
                title="Request timed out",
                message=message,
                cause="A model call did not answer within RP_HTTP_TIMEOUT_SECS. Local timeouts are not retried: "
                "the call already waited the full bound.",
                fix="Raise RP_HTTP_TIMEOUT_SECS, lower RP_MAX_CHUNK_CHARS so requests are smaller, or lower "
                "GEMINI_THINKING_LEVEL. Then retry the failed chunks.",
            )
        )
    if (
        mod.startswith("httpx")
        or exc_type in ("ConnectError", "ConnectionError", "RemoteProtocolError")
        or isinstance(exc, ConnectionError)
    ):
        return done(
            Explanation(
                code="NETWORK",
                category="network",
                severity="error",
                title="Network error",
                message=message,
                cause="The model API could not be reached (no connection, DNS, proxy or TLS failure).",
                fix="Check the internet connection and any proxy, then retry the failed chunks.",
            )
        )
    if exc_type == "ExtractionParseError":
        return done(
            Explanation(
                code="PARSE",
                category="parse",
                severity="error",
                title="Model reply could not be parsed",
                message=message,
                cause="Every attempt returned truncated or malformed JSON (RP_MAX_PARSE_RETRIES retries were made). "
                "A finish_reason of MAX_TOKENS means the reply was cut off.",
                fix="Lower RP_MAX_CHUNK_CHARS so each reply is shorter, raise RP_MAX_PARSE_RETRIES, or try another "
                "model. If you edited the extraction prompt, check it still asks for the JSON shape.",
            )
        )
    if exc_type in ("JSONDecodeError",):
        return done(
            Explanation(
                code="PARSE",
                category="parse",
                severity="error",
                title="Model reply was not valid JSON",
                message=message,
                cause="The model returned text that is not JSON.",
                fix="Retry; if it repeats, check an edited prompt still asks for JSON, or try another model.",
            )
        )
    if exc_type == "ValidationError":
        return done(
            Explanation(
                code="SCHEMA",
                category="validation",
                severity="error",
                title="Model reply did not match the expected shape",
                message=message,
                cause="The JSON parsed but lacks required fields or has wrong types.",
                fix="Check edited prompts (Prompts) still ask for every field; try another model.",
            )
        )
    if exc_type == "MissingRecordingError":
        return done(
            Explanation(
                code="REPLAY_MISSING",
                category="config",
                severity="error",
                title="No recorded reply for this request (offline replay)",
                message=message,
                cause="Offline replay only has replies recorded for the golden corpus books with the prompts they "
                "were recorded against. This request (another book, chunk size or edited prompt) has none.",
                fix="Use a golden corpus book with default settings, or switch the provider to Gemini.",
            )
        )
    if exc_type == "FileNotFoundError":
        return done(
            Explanation(
                code="NOT_FOUND",
                category="input",
                severity="fatal",
                title="File not found",
                message=message,
                cause="A file the run needs does not exist (usually the book path).",
                fix="Choose the book again on the New run page (upload it or give an existing path).",
            )
        )
    if exc_type == "MemoryError":
        return done(
            Explanation(
                code="OOM",
                category="crash",
                severity="fatal",
                title="Out of memory",
                message=message,
                cause="The run used all available memory.",
                fix="Lower concurrency and RP_MAX_CHUNK_CHARS, or process the book in parts (% of book).",
            )
        )
    low = message.lower()
    if "expected" in low and "-dim" in low:
        return done(
            Explanation(
                code="EMBED_DIMS",
                category="config",
                severity="fatal",
                title="Embedding has the wrong size",
                message=message,
                cause="The embedding model returned a vector that is not 1536 values long.",
                fix="Choose an embedding model that can return 1536 values (Settings → Provider & models).",
            )
        )
    if "fat token" in low or "refine():" in low or "cayenne refinement" in low:
        return done(
            Explanation(
                code="REFINE",
                category="validation",
                severity="error",
                title="Refinement failed",
                message=message,
                cause="REFINE's reply was empty or failed its checks (Fat Tokens that do not give back the "
                "direction text, unknown ingredient ids). The recipe was dropped.",
                fix="See the API call for this chunk (API calls tab). Retry the failed chunk; if it repeats, "
                "check an edited refine prompt or try another model.",
            )
        )
    if "embed():" in low:
        return done(
            Explanation(
                code="EMBED",
                category="request",
                severity="error",
                title="Embedding failed",
                message=message,
                cause="The embedding call failed after retries.",
                fix="Check GEMINI_EMBEDDING_MODEL and the API key; see the failed API call for its HTTP code.",
            )
        )
    return done(
        Explanation(
            code=exc_type.upper() if exc_type.isidentifier() else "UNEXPECTED",
            category="unexpected",
            severity="error",
            title=f"Unexpected {exc_type}",
            message=message,
            cause="An error this app has no specific explanation for.",
            fix="Read the message and traceback. Retry the failed chunks; if it repeats it is a bug worth reporting "
            "with this error's details.",
        )
    )


def _is_api_error(exc: BaseException) -> bool:
    try:
        from google.genai import errors as genai_errors
    except Exception:  # pragma: no cover
        return hasattr(exc, "status")
    return isinstance(exc, genai_errors.APIError) or (hasattr(exc, "status") and hasattr(exc, "code"))


_REASON = re.compile(r"^(?P<type>[A-Za-z_][A-Za-z0-9_.]*): (?P<msg>.*)$", re.S)


def explain_reason(reason: str) -> Optional[Explanation]:
    """Explain a pipeline skip reason (``"TypeName: message"``); None for an ordinary, non-error skip."""
    m = _REASON.match(reason.strip())
    if not m or not (m.group("type")[0].isupper()):
        return None
    type_name = m.group("type").rsplit(".", 1)[-1]
    fake = type(type_name, (Exception,), {})(m.group("msg"))
    status = re.search(r"\b([1-5]\d\d)\b\s+([A-Z_]{4,})", m.group("msg"))
    if status:
        e = _http_explanation(int(status.group(1)), status.group(2), m.group("msg"), type_name)
        e.severity = "error"
        return e
    e = explain(fake, with_traceback=False)
    e.exception_type = type_name
    if e.severity == "warning":
        e.severity = "error"
    return e


def to_json(e: Explanation) -> str:
    return json.dumps(e.as_dict(), ensure_ascii=False)
