"""
recipeparser/providers.py — which model providers and models a run may use.

Gemini is the only provider the pipeline speaks today (every stage calls
``google.genai``). The table is shaped so another provider is one more entry
plus a client factory, and so the web app can list the choices rather than
hard-code them.

The embedding check is here, at configuration level, and not left to the EMBED
stage: a model that cannot return ``EMBEDDING_DIMENSIONS`` values would fail
every recipe of a run at its last step, and the vectors it did return could not
be restored from a Cayenne archive or stored in Supabase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from recipeparser.config import EMBEDDING_DIMENSIONS

__all__ = [
    "EMBEDDING_DIMENSIONS", "DEFAULT_PROVIDER", "PROVIDERS", "EmbeddingModel", "GenerationModel", "Provider",
    "check_embedding_model", "find_embedding_model", "get_provider",
]


@dataclass(frozen=True)
class EmbeddingModel:
    id: str
    label: str
    #: The sizes the model can return, as (min, max) when it truncates to a requested
    #: size (output_dimensionality), or a fixed size as (n, n).
    dims: Tuple[int, int]
    note: str = ""

    def supports(self, n: int) -> bool:
        return self.dims[0] <= n <= self.dims[1]


@dataclass(frozen=True)
class GenerationModel:
    id: str
    label: str
    note: str = ""


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    api_key_env: str
    generation_env: str
    embedding_env: str
    generation_models: List[GenerationModel] = field(default_factory=list)
    embedding_models: List[EmbeddingModel] = field(default_factory=list)
    #: True when the provider makes no network call (recorded replies).
    offline: bool = False
    note: str = ""


GEMINI = Provider(
    id="gemini",
    label="Google Gemini",
    api_key_env="GOOGLE_API_KEY",
    generation_env="GEMINI_MODEL",
    embedding_env="GEMINI_EMBEDDING_MODEL",
    generation_models=[
        GenerationModel(
            "gemini-3.1-flash-lite",
            "Gemini 3.1 Flash-Lite",
            "Current default. 147/160 on the tagging golden set (config.py).",
        ),
        GenerationModel("gemini-3.5-flash", "Gemini 3.5 Flash", "Higher tagging accuracy, higher cost."),
        GenerationModel("gemini-3.8-flash", "Gemini 3.8 Flash", "96/96 on the tagging golden set."),
        GenerationModel("gemini-2.5-flash", "Gemini 2.5 Flash", "Retires 2026-10-16."),
        GenerationModel(
            "gemini-3.5-flash-lite",
            "Gemini 3.5 Flash-Lite",
            "Not recommended: no usable refinement in 42 of 42 runs (config.py).",
        ),
    ],
    embedding_models=[
        EmbeddingModel(
            "models/gemini-embedding-001",
            "Gemini Embedding 001",
            (128, 3072),
            "Default. Returns 1536 values via output_dimensionality.",
        ),
        EmbeddingModel("gemini-embedding-001", "Gemini Embedding 001 (bare name)", (128, 3072)),
        EmbeddingModel(
            "models/text-embedding-004", "Text Embedding 004", (768, 768), "Returns 768 values only: not usable."
        ),
        EmbeddingModel(
            "models/embedding-001", "Embedding 001 (legacy)", (768, 768), "Returns 768 values only: not usable."
        ),
    ],
)

REPLAY = Provider(
    id="replay",
    label="Offline replay (recorded Gemini replies)",
    api_key_env="",
    generation_env="GEMINI_MODEL",
    embedding_env="GEMINI_EMBEDDING_MODEL",
    generation_models=GEMINI.generation_models,
    embedding_models=GEMINI.embedding_models,
    offline=True,
    note=(
        "Replays the Gemini replies recorded for the golden test corpus (tests/goldens). "
        "No API key, no cost; only the corpus books have recordings, and embeddings are "
        "deterministic stand-ins. For trying the app and its controls, not for real imports."
    ),
)

PROVIDERS: Dict[str, Provider] = {p.id: p for p in (GEMINI, REPLAY)}
DEFAULT_PROVIDER = GEMINI.id


def get_provider(provider_id: str) -> Provider:
    try:
        return PROVIDERS[provider_id]
    except KeyError:
        raise ValueError(f"Unknown provider {provider_id!r}. Available: {', '.join(PROVIDERS)}.") from None


def find_embedding_model(provider_id: str, model: str) -> Optional[EmbeddingModel]:
    target = model.strip()
    bare = target[7:] if target.startswith("models/") else target
    for m in get_provider(provider_id).embedding_models:
        m_bare = m.id[7:] if m.id.startswith("models/") else m.id
        if m.id == target or m_bare == bare:
            return m
    return None


def check_embedding_model(provider_id: str, model: str) -> Tuple[str, str]:
    """``(verdict, message)`` for using *model* as the embedding model.

    verdict is ``"ok"`` (known to return EMBEDDING_DIMENSIONS values), ``"rejected"``
    (known not to), or ``"unverified"`` (not in the table: the run's preflight
    probe must see EMBEDDING_DIMENSIONS values before any chunk is processed).
    """
    if not model.strip():
        return "rejected", "No embedding model is set."
    known = find_embedding_model(provider_id, model)
    if known is None:
        return "unverified", (
            f"{model!r} is not a known {get_provider(provider_id).label} embedding model. It can be used only "
            f"if it returns exactly {EMBEDDING_DIMENSIONS} values; every run checks that with one probe call "
            "before processing anything."
        )
    if known.supports(EMBEDDING_DIMENSIONS):
        return "ok", f"{known.label} returns {EMBEDDING_DIMENSIONS} values."
    lo, hi = known.dims
    size = str(lo) if lo == hi else f"{lo}-{hi}"
    return "rejected", (
        f"{known.label} returns {size} values, but recipes are stored as {EMBEDDING_DIMENSIONS}-value vectors "
        "(the EMBED stage, the Cayenne archive restore path and Supabase all require that size)."
    )
