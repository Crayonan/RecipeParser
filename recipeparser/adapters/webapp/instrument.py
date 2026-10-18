"""
recipeparser/adapters/webapp/instrument.py — see every model call and every log line of a run.

``InstrumentedClient`` wraps a ``google.genai.Client`` (or the offline replay
client). Every ``generate_content`` and ``embed_content`` the pipeline makes —
first attempts and back-off retries alike, because ``gemini._with_backoff``
calls the client again for each — is timed and recorded with its model, chunk,
stage, token usage, finish reason, HTTP code and status. A failed call also
becomes an explained error row (``errors.explain``), so a 429, a 404 or a
timeout is visible the moment it happens, not only when the chunk gives up.

``StoreLogHandler`` turns the package's own log records into run events, so the
web app's live log shows the same lines the console does, tagged with chunk and
stage.

``ConsoleFormatter`` prints those lines readably: time, level, run, chunk/stage.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

from recipeparser.adapters.webapp.errors import explain
from recipeparser.adapters.webapp.store import Store
from recipeparser.core import run_context

log = logging.getLogger(__name__)


def _usage(response: Any) -> Dict[str, Optional[int]]:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {}
    return {
        "prompt_tokens": getattr(usage, "prompt_token_count", None),
        "output_tokens": getattr(usage, "candidates_token_count", None),
        "thought_tokens": getattr(usage, "thoughts_token_count", None),
        "total_tokens": getattr(usage, "total_token_count", None),
    }


def _finish_reason(response: Any) -> Optional[str]:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    reason = getattr(candidates[0], "finish_reason", None)
    if reason is None:
        return None
    return str(getattr(reason, "name", reason))


def _text_of(contents: Any) -> str:
    if isinstance(contents, str):
        return contents
    if isinstance(contents, (list, tuple)):
        parts = []
        for p in contents:
            if isinstance(p, str):
                parts.append(p)
            else:
                parts.append(f"[{type(p).__name__}]")
        return "\n".join(parts)
    return f"[{type(contents).__name__}]"


def _response_text(response: Any) -> str:
    try:
        text = getattr(response, "text", None)
    except Exception:  # genai raises on .text for some blocked replies
        text = None
    if text:
        return str(text)
    embeddings = getattr(response, "embeddings", None)
    if embeddings:
        values = getattr(embeddings[0], "values", None) or []
        return f"[embedding: {len(values)} values]"
    return ""


class _Models:
    def __init__(self, owner: "InstrumentedClient") -> None:
        self._owner = owner

    def generate_content(self, *, model: str, contents: Any, config: Any = None) -> Any:
        return self._owner._call(
            "generate",
            model,
            contents,
            lambda: self._owner._inner.models.generate_content(model=model, contents=contents, config=config),
        )

    def embed_content(self, *, model: str, contents: Any, config: Any = None) -> Any:
        return self._owner._call(
            "embed",
            model,
            contents,
            lambda: self._owner._inner.models.embed_content(model=model, contents=contents, config=config),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._owner._inner.models, name)


class InstrumentedClient:
    """A genai-compatible client that records every call to the run's store."""

    def __init__(
        self,
        inner: Any,
        store: Store,
        run_id: str,
        *,
        store_text: bool = True,
        on_call: Optional[Callable[[Dict[str, Any]], None]] = None,
        latency_ms: int = 0,
    ) -> None:
        self._inner = inner
        self._store = store
        self._run_id = run_id
        self._store_text = store_text
        self._on_call = on_call
        self._latency = latency_ms / 1000.0
        self.models = _Models(self)
        self.stats_lock = threading.Lock()
        self.in_flight = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _call(self, kind: str, model: str, contents: Any, fn: Callable[[], Any]) -> Any:
        chunk, _label, stage = run_context.current()
        prompt = _text_of(contents)
        with self.stats_lock:
            self.in_flight += 1
        started = time.monotonic()
        try:
            if self._latency:
                time.sleep(self._latency)
            response = fn()
        except BaseException as exc:
            elapsed = (time.monotonic() - started) * 1000
            with self.stats_lock:
                self.in_flight -= 1
            self._record_failure(kind, model, chunk, stage, prompt, elapsed, exc)
            raise
        elapsed = (time.monotonic() - started) * 1000
        with self.stats_lock:
            self.in_flight -= 1
        text = _response_text(response)
        row = {
            "chunk": chunk,
            "stage": stage,
            "kind": kind,
            "model": model,
            "duration_ms": round(elapsed, 1),
            "ok": 1,
            "status_code": 200,
            "prompt_chars": len(prompt),
            "response_chars": len(text),
            "finish_reason": _finish_reason(response),
            "prompt_text": prompt if self._store_text else None,
            "response_text": text if self._store_text else None,
            **_usage(response),
        }
        try:
            call_id = self._store.add_api_call(self._run_id, **row)
            if self._on_call:
                self._on_call({**row, "id": call_id})
        except Exception:  # recording must never fail the call
            log.exception("Could not record an API call.")
        return response

    def _record_failure(
        self,
        kind: str,
        model: str,
        chunk: Optional[int],
        stage: Optional[str],
        prompt: str,
        elapsed: float,
        exc: BaseException,
    ) -> None:
        try:
            e = explain(exc)
            call_id = self._store.add_api_call(
                self._run_id,
                chunk=chunk,
                stage=stage,
                kind=kind,
                model=model,
                duration_ms=round(elapsed, 1),
                ok=0,
                status_code=e.status_code,
                status=e.status or e.code,
                error=e.message[:4000],
                prompt_chars=len(prompt),
                prompt_text=prompt if self._store_text else None,
            )
            self._store.add_error(
                self._run_id,
                severity=e.severity,
                code=e.code,
                category=e.category,
                title=e.title,
                message=e.message,
                cause=e.cause,
                fix=e.fix,
                stage=stage,
                chunk=chunk,
                exception_type=e.exception_type,
                traceback=e.traceback,
                api_call_id=call_id,
            )
            where = f"chunk {chunk}" if chunk is not None else "run"
            level = logging.WARNING if e.severity == "warning" else logging.ERROR
            logging.getLogger("recipeparser.adapters.webapp.api").log(
                level,
                "%s %s call failed (%s, %s, %.0f ms): %s — %s",
                e.code,
                kind,
                where,
                stage or "-",
                elapsed,
                e.title,
                e.message[:300],
            )
            if self._on_call:
                self._on_call(
                    {
                        "id": call_id,
                        "ok": 0,
                        "chunk": chunk,
                        "stage": stage,
                        "kind": kind,
                        "status_code": e.status_code,
                        "code": e.code,
                    }
                )
        except Exception:
            log.exception("Could not record a failed API call.")


class StoreLogHandler(logging.Handler):
    """Writes package log records to the run's event table."""

    def __init__(self, store: Store, run_id: str, level: int = logging.INFO) -> None:
        super().__init__(level)
        self._store = store
        self._run_id = run_id
        self._guard = threading.local()
        #: Records that could not be written to the database. Reported on stderr as they happen and
        #: by the runner at the end, so a lost log line is never silent.
        self.dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._guard, "busy", False):
            return
        if not record.name.startswith("recipeparser") and record.levelno < logging.WARNING:
            return
        if record.name == "recipeparser.adapters.webapp.event":
            return  # already an event row: the runner echoes it to the console only
        self._guard.busy = True
        try:
            chunk, _label, stage = run_context.current()
            msg = record.getMessage()
            data: Dict[str, Any] = {"logger": record.name}
            if record.exc_info and record.exc_info[1] is not None:
                data["exception"] = logging.Formatter().formatException(record.exc_info)
            self._store.add_event(
                self._run_id,
                "log",
                msg[:8000],
                level=record.levelname,
                stage=stage,
                chunk=chunk,
                data=data,
                ts=record.created,
            )
        except Exception as exc:
            self.dropped += 1
            if self.dropped <= 5 or self.dropped % 100 == 0:
                sys.stderr.write(
                    f"[run {self._run_id}] could not record a log line in the database "
                    f"({type(exc).__name__}: {exc}); {self.dropped} dropped so far. The run log file still has it.\n"
                )
        finally:
            self._guard.busy = False


_COLORS = {
    "DEBUG": "\033[90m",
    "INFO": "\033[36m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[41;97m",
}
_RESET = "\033[0m"


class ConsoleFormatter(logging.Formatter):
    """``12:01:07 INFO  [run 251012-…] c7 REFINING  message`` — coloured on a terminal."""

    def __init__(self, run_id: Optional[str] = None, color: Optional[bool] = None) -> None:
        super().__init__()
        self._run = run_id
        self._color = sys.stderr.isatty() if color is None else color

    def format(self, record: logging.LogRecord) -> str:
        chunk, _label, stage = run_context.current()
        when = time.strftime("%H:%M:%S", time.localtime(record.created))
        level = record.levelname
        lvl = f"{level:<7}"
        if self._color:
            lvl = _COLORS.get(level, "") + lvl + _RESET
        where = []
        if self._run:
            where.append(f"[run {self._run}]")
        if chunk is not None:
            where.append(f"c{chunk}")
        if stage:
            where.append(stage)
        text = f"{when} {lvl} {' '.join(where)} {record.name.replace('recipeparser.', '')}: {record.getMessage()}"
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text
