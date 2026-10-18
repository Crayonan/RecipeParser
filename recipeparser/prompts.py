"""
recipeparser/prompts.py — editable prompts.

Every prompt the extraction pipeline sends is built by a template function in
``gemini.py`` registered here under a short name ("extract", "refine", …).
A template function takes its variable parts as strings and returns the prompt.
With no override in force, ``render`` calls it with the real values, so the
prompt is byte-for-byte what it always was.

An override replaces a prompt's whole text. It is read from the JSON file named
by ``RP_PROMPT_OVERRIDES_FILE`` (``{"refine": "…template…"}``), which the web
app writes for each run; the file is re-read whenever it changes on disk. In an
override template a variable part is written ``{{name}}`` — double braces,
because the prompts themselves are full of single braces (JSON examples, Fat
Token syntax) that must pass through untouched.

Neutral ground: imports nothing from the package, so ``gemini.py`` and the web
app can both use it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

PROMPT_OVERRIDES_ENV = "RP_PROMPT_OVERRIDES_FILE"

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


@dataclass(frozen=True)
class PromptSpec:
    """A registered prompt: what it is for and which variable parts it takes."""

    name: str
    title: str
    description: str
    stage: str
    placeholders: Tuple[str, ...]
    template: Callable[..., str] = field(compare=False)
    #: Placeholders an override must keep; dropping one sends the model no recipe text.
    required: Tuple[str, ...] = ()

    def default_template(self) -> str:
        """The built-in prompt with each variable part shown as ``{{name}}``."""
        return self.template(**{p: "{{" + p + "}}" for p in self.placeholders})


_REGISTRY: Dict[str, PromptSpec] = {}
_lock = threading.Lock()
_cache: Dict[str, Any] = {"path": None, "mtime": None, "data": {}}


def register(
    name: str,
    *,
    title: str,
    description: str,
    stage: str,
    placeholders: Tuple[str, ...],
    required: Optional[Tuple[str, ...]] = None,
) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Decorator: register a template function under *name*."""

    def deco(fn: Callable[..., str]) -> Callable[..., str]:
        _REGISTRY[name] = PromptSpec(
            name=name,
            title=title,
            description=description,
            stage=stage,
            placeholders=placeholders,
            template=fn,
            required=placeholders if required is None else required,
        )
        return fn

    return deco


def specs() -> List[PromptSpec]:
    """Every registered prompt, in registration order."""
    return list(_REGISTRY.values())


def spec(name: str) -> PromptSpec:
    return _REGISTRY[name]


def _load_overrides() -> Dict[str, str]:
    path = os.environ.get(PROMPT_OVERRIDES_ENV, "").strip()
    if not path:
        return {}
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        log.error(
            "Prompt overrides file %s (from %s) does not exist — using built-in prompts.", path, PROMPT_OVERRIDES_ENV
        )
        return {}
    with _lock:
        if _cache["path"] == path and _cache["mtime"] == mtime:
            cached: Dict[str, str] = dict(_cache["data"])
            return cached
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError) as exc:
            log.error("Prompt overrides file %s could not be read (%s) — using built-in prompts.", path, exc)
            raw = {}
        data = {k: v for k, v in raw.items() if isinstance(k, str) and isinstance(v, str) and v.strip()}
        unknown = sorted(set(data) - set(_REGISTRY))
        if unknown:
            log.warning("Prompt overrides for unknown prompt(s) ignored: %s", ", ".join(unknown))
        _cache.update(path=path, mtime=mtime, data=data)
        if data:
            log.info("Prompt overrides active: %s", ", ".join(sorted(k for k in data if k in _REGISTRY)))
        return dict(data)


def active_overrides() -> Dict[str, str]:
    """The overrides in force for this process (name -> template)."""
    return {k: v for k, v in _load_overrides().items() if k in _REGISTRY}


def substitute(template: str, values: Dict[str, str]) -> str:
    """Replace each ``{{name}}`` in *template* with ``values[name]``; unknown names stay as written."""
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def validate(name: str, template: str) -> List[str]:
    """Problems with an override template for *name*; empty when it can be used."""
    if name not in _REGISTRY:
        return [f"There is no prompt called {name!r}."]
    s = _REGISTRY[name]
    problems: List[str] = []
    if not template.strip():
        problems.append("The prompt is empty.")
    used = set(_PLACEHOLDER.findall(template))
    for p in s.required:
        if p not in used:
            problems.append(f"The placeholder {{{{{p}}}}} is missing, so the model would not receive the {p}.")
    for p in sorted(used - set(s.placeholders)):
        problems.append(
            f"{{{{{p}}}}} is not a placeholder of this prompt (it has: "
            + ", ".join("{{" + q + "}}" for q in s.placeholders)
            + ")."
        )
    return problems


def render(name: str, **values: str) -> str:
    """The prompt *name* for *values*: the override when one is in force, else the built-in template."""
    s = _REGISTRY[name]
    override = _load_overrides().get(name)
    if override is not None:
        return substitute(override, values)
    return s.template(**values)
