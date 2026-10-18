"""
recipeparser/core/run_context.py — which chunk and stage the current thread is working on.

RecipePipeline runs each chunk on a pool thread and calls the model from deep
inside the stages, which know nothing of the chunk they serve. Anything that
watches those calls from outside (the web app's instrumented client, a log
handler) reads this thread-local to say "chunk 7, REFINING" next to a request,
a retry or an error, instead of an anonymous call.

Setting it costs a thread-local write; nothing in core reads it back, so a
caller that never looks at it is unaffected.

TID rule: core module — MUST NOT import from io/ or adapters/.
"""

from __future__ import annotations

import threading
from typing import Optional, Tuple

_local = threading.local()


def set_chunk(index: Optional[int], label: Optional[str] = None) -> None:
    """Record the chunk (zero-based submission position) this thread now works on."""
    _local.chunk = index
    _local.label = label
    _local.stage = None


def set_stage(stage: Optional[str]) -> None:
    """Record the stage (EXTRACTING, REFINING, EMBEDDING, ASSEMBLING, TAGGING …) this thread is in."""
    _local.stage = stage


def current() -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """``(chunk_index, chunk_label, stage)`` for the calling thread; Nones when unset."""
    return (
        getattr(_local, "chunk", None),
        getattr(_local, "label", None),
        getattr(_local, "stage", None),
    )


def clear() -> None:
    """Forget the chunk and stage (a worker that has finished its chunk)."""
    _local.chunk = None
    _local.label = None
    _local.stage = None
