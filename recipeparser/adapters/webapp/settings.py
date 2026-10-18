"""
recipeparser/adapters/webapp/settings.py — every environment variable, one table.

The registry is built from two sources so it cannot fall behind the code:

* ``recipeparser/config.py`` is parsed, not imported: each
  ``NAME: type = _env_kind("ENV", default)`` line becomes a setting, with the
  comment block above it as its description and the section banner it sits
  under as its group.
* Every other variable the package reads (the API key, Supabase, the API
  service's auth switches, logging, paths) is listed in ``_EXTRA`` below.
  ``tests/webapp/test_settings_registry.py`` scans the source tree for env
  reads and fails when one is missing here.

Values resolve, lowest priority first: built-in default → the server's own
environment → the app's ``.env`` file (where secrets are saved) → values saved
in the web app → the run's own overrides. Each run's child process gets the
resolved values as its environment, which is how they reach the module-level
constants in ``config.py``.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from recipeparser import providers

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.py"

SCOPE_RUN = "run"
SCOPE_SERVICE = "service"
SCOPE_SYSTEM = "system"
SCOPE_WEBAPP = "webapp"


@dataclass
class Setting:
    key: str
    group: str
    label: str
    type: str  # str | int | float | bool | choice | secret | optional_int
    default: Optional[str]
    description: str
    scope: str = SCOPE_RUN
    choices: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    min: Optional[float] = None
    max: Optional[float] = None
    editable: bool = True
    config_name: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


_LINE = re.compile(
    r"^(?P<name>[A-Z][A-Z0-9_]*): (?P<type>int|float|str|Optional\[int\]) = "
    r"_env_(?P<kind>str|int|float|opt_int)\(\s*\"(?P<env>[A-Z][A-Z0-9_]*)\",\s*(?P<default>.+?)\)\s*(?:#\s*(?P<trail>.*))?$"
)
_BANNER = re.compile(r"^# -{10,}$")

_TYPE = {"str": "str", "int": "int", "float": "float", "opt_int": "optional_int"}

_GROUP_RENAME = {
    "EPUB / chunking": "Chunking & photos",
    "Gemini API": "Model calls & retries",
    "Pipeline concurrency": "Concurrency",
    "TOC extraction and chunking (Phase 2)": "Table of contents",
    "PDF pre-flight (Phase 1)": "PDF",
    "Phase 3 — Pipeline control and rate-limit auto-pause": "Pipeline control",
}

_LABEL_WORDS = {
    "secs": "(s)",
    "px": "(px)",
    "pdf": "PDF",
    "toc": "TOC",
    "ocr": "OCR",
    "rpm": "RPM",
    "api": "API",
    "http": "HTTP",
}


def _label(name: str) -> str:
    words = name.lower().split("_")
    out = [_LABEL_WORDS.get(w, w) for w in words]
    text = " ".join(out)
    return text[:1].upper() + text[1:]


def parse_config(path: Path = CONFIG_PATH) -> List[Setting]:
    """Every env-overridable constant in config.py, in file order."""
    lines = path.read_text(encoding="utf-8").splitlines()
    group = "General"
    comment: List[str] = []
    after_const = False
    out: List[Setting] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _BANNER.match(line) and i + 2 < len(lines) and _BANNER.match(lines[i + 2]):
            title = lines[i + 1].lstrip("# ").strip()
            group = _GROUP_RENAME.get(title, title)
            comment = []
            i += 3
            continue
        if line.startswith("#"):
            if after_const:
                comment, after_const = [], False
            comment.append(line.lstrip("#").strip())
            i += 1
            continue
        after_const = False
        if not line.strip():
            comment = []
        else:
            m = _LINE.match(line)
            if m:
                default_src = m.group("default").strip()
                try:
                    default_val = ast.literal_eval(default_src)
                except (ValueError, SyntaxError):
                    default_val = default_src
                desc = " ".join(c for c in comment if c).strip()
                if m.group("trail"):
                    desc = (desc + " " + m.group("trail")).strip()
                out.append(
                    Setting(
                        key=m.group("env"),
                        group=group,
                        label=_label(m.group("name")),
                        type=_TYPE[m.group("kind")],
                        default=None if default_val is None else str(default_val),
                        description=desc or f"config.{m.group('name')}",
                        scope=SCOPE_SERVICE if m.group("name") == "MAX_UPLOAD_BYTES" else SCOPE_RUN,
                        min=0,
                        config_name=m.group("name"),
                    )
                )
                after_const = True
            # A constant that directly follows another shares its comment (MAX_PARSE_RETRIES and
            # PARSE_RETRY_DELAY_SECS); any other code line ends the block.
            elif not line.startswith(" "):
                comment = []
        i += 1
    return out


_GEN_SUGGESTIONS = [m.id for m in providers.GEMINI.generation_models]
_EMB_SUGGESTIONS = [m.id for m in providers.GEMINI.embedding_models]

_EXTRA: List[Setting] = [
    # Provider & models
    Setting(
        "RP_PROVIDER",
        "Provider & models",
        "Provider",
        "choice",
        providers.DEFAULT_PROVIDER,
        "Which model provider runs use by default. Gemini is the only live provider today; "
        "'replay' answers from the replies recorded for the golden test corpus (offline, no key, no cost).",
        scope=SCOPE_WEBAPP,
        choices=list(providers.PROVIDERS),
    ),
    Setting(
        "GOOGLE_API_KEY",
        "API keys",
        "Google API key",
        "secret",
        None,
        "The Gemini API key every model call uses. Saved to the app's .env file "
        "(the same file the CLI and desktop GUI read), never to the database or a run's snapshot.",
    ),
    Setting(
        "RP_REPLAY_LATENCY_MS",
        "Provider & models",
        "Replay latency (ms)",
        "int",
        "250",
        "Offline replay only: how long each recorded reply takes to 'arrive', so pause, stop and kill can be "
        "tried on a realistic run.",
        min=0,
        max=60000,
    ),
    Setting(
        "RP_WEBAPP_DATA_DIR",
        "Managed by the web app",
        "Web app data directory",
        "str",
        None,
        "Where the web app keeps its database, runs and uploads. Set before starting the server "
        "(or pass --data-dir); default: the app data folder's 'webapp' directory.",
        scope=SCOPE_WEBAPP,
        editable=False,
    ),
    Setting(
        "RP_WEBAPP_PORT",
        "Managed by the web app",
        "Web app port",
        "int",
        "8765",
        "The port the web app listens on. Set before starting the server (or pass --port).",
        scope=SCOPE_WEBAPP,
        editable=False,
    ),
    # Logging
    Setting(
        "LOG_LEVEL",
        "Logging",
        "Log level",
        "choice",
        "INFO",
        "The lowest level written to the console and the run log, and recorded as events. DEBUG adds every "
        "state transition and per-call detail.",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    ),
    Setting(
        "RP_STORE_PROMPTS",
        "Logging",
        "Store prompts and replies",
        "bool",
        "true",
        "Keep the full text of every prompt and model reply with its API call record, so a failed or odd "
        "result can be inspected afterwards. Turn off to save disk space.",
    ),
    # Analytics
    Setting(
        "RP_PRICE_INPUT_PER_M",
        "Analytics",
        "Input price per 1M tokens (USD)",
        "float",
        "0",
        "Used only to estimate run cost in Analytics. 0 hides cost.",
        scope=SCOPE_WEBAPP,
        min=0,
    ),
    Setting(
        "RP_PRICE_OUTPUT_PER_M",
        "Analytics",
        "Output price per 1M tokens (USD)",
        "float",
        "0",
        "Used only to estimate run cost in Analytics (thinking tokens bill at this rate).",
        scope=SCOPE_WEBAPP,
        min=0,
    ),
    # Managed by the web app
    Setting(
        "RP_PROMPT_OVERRIDES_FILE",
        "Managed by the web app",
        "Prompt overrides file",
        "str",
        None,
        "Path of the JSON file holding edited prompts. The web app writes one per run from the Prompts page; "
        "edit prompts there instead.",
        editable=False,
    ),
    # Cayenne API service (recipeparser.adapters.api)
    Setting(
        "SUPABASE_URL",
        "Cayenne API service",
        "Supabase URL",
        "str",
        None,
        "The Supabase project the Cayenne API service writes to. Web-app runs are local only and never use it.",
        scope=SCOPE_SERVICE,
    ),
    Setting(
        "SUPABASE_SERVICE_ROLE_KEY",
        "Cayenne API service",
        "Supabase service-role key",
        "secret",
        None,
        "The Cayenne API service's Supabase key. Not used by web-app runs.",
        scope=SCOPE_SERVICE,
    ),
    Setting(
        "SUPABASE_SERVICE_KEY",
        "Cayenne API service",
        "Supabase service key (legacy name)",
        "secret",
        None,
        "Legacy name for SUPABASE_SERVICE_ROLE_KEY; the API refuses to start when only this one is set.",
        scope=SCOPE_SERVICE,
    ),
    Setting(
        "CORS_ORIGINS",
        "Cayenne API service",
        "CORS origins",
        "str",
        None,
        "Comma-separated origins the Cayenne API service accepts browser requests from.",
        scope=SCOPE_SERVICE,
    ),
    Setting(
        "DISABLE_AUTH",
        "Cayenne API service",
        "Disable auth",
        "bool",
        "false",
        "Local development only: the Cayenne API service skips JWT checks. Refused on protected tiers.",
        scope=SCOPE_SERVICE,
    ),
    Setting(
        "TEST_USER_ID",
        "Cayenne API service",
        "Test user id",
        "str",
        None,
        "The user the Cayenne API service acts as when DISABLE_AUTH is on.",
        scope=SCOPE_SERVICE,
    ),
    Setting(
        "APP_ENV",
        "Cayenne API service",
        "App environment",
        "str",
        None,
        "Deployment tier name; a protected tier refuses DISABLE_AUTH.",
        scope=SCOPE_SERVICE,
    ),
    Setting(
        "ENVIRONMENT",
        "Cayenne API service",
        "Environment (fallback name)",
        "str",
        None,
        "Read when APP_ENV is unset.",
        scope=SCOPE_SERVICE,
    ),
    Setting(
        "REGEN_WORKER_ENABLED",
        "Cayenne API service",
        "Regeneration worker",
        "bool",
        "false",
        "Start the Cayenne API service's background regeneration worker.",
        scope=SCOPE_SERVICE,
    ),
    # System
    Setting(
        "APPDATA",
        "System paths",
        "APPDATA (Windows)",
        "str",
        None,
        "Windows: where the app's settings folder lives (%APPDATA%\\RecipeParser).",
        scope=SCOPE_SYSTEM,
    ),
    Setting(
        "XDG_CONFIG_HOME",
        "System paths",
        "XDG_CONFIG_HOME (Linux)",
        "str",
        None,
        "Linux: base of the app's settings folder ($XDG_CONFIG_HOME/RecipeParser, else ~/.config).",
        scope=SCOPE_SYSTEM,
    ),
    Setting(
        "ALLOW_LIVE_WRITES_IN_TESTS",
        "System paths",
        "Allow live writes in tests",
        "bool",
        "false",
        "Test guard: lets a pytest run write to a real Supabase project. Leave off.",
        scope=SCOPE_SYSTEM,
    ),
]

_PROVIDER_KEYS = {"GEMINI_MODEL", "GEMINI_EMBEDDING_MODEL", "GEMINI_IMAGE_MODEL", "GEMINI_THINKING_LEVEL"}


def registry() -> List[Setting]:
    """All settings: config.py's constants plus every other variable the package reads."""
    out: List[Setting] = []
    for s in parse_config():
        if s.key in _PROVIDER_KEYS:
            s.group = "Provider & models"
        if s.key == "GEMINI_MODEL":
            s.label, s.suggestions = "Generation model", _GEN_SUGGESTIONS
        elif s.key == "GEMINI_EMBEDDING_MODEL":
            s.label, s.suggestions = "Embedding model", _EMB_SUGGESTIONS
        elif s.key == "GEMINI_IMAGE_MODEL":
            s.label, s.scope = "Image model", SCOPE_SERVICE
        elif s.key == "GEMINI_THINKING_LEVEL":
            s.label, s.type, s.choices = "Thinking level", "choice", ["minimal", "low", "medium", "high"]
        elif s.key in ("RP_PICTURE_TIMEOUT_SECS", "RP_CLASSIFY_TIMEOUT_SECS"):
            s.scope = SCOPE_SERVICE
        out.append(s)
    out.extend(_EXTRA)
    return out


def registry_map() -> Dict[str, Setting]:
    return {s.key: s for s in registry()}


SECRET_KEYS = frozenset(s.key for s in _EXTRA if s.type == "secret")


def mask(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    return "••••" + value[-4:] if len(value) > 8 else "••••"


def validate(key: str, value: Optional[str]) -> Optional[str]:
    """An error sentence, or None when *value* is acceptable for *key* (None/"" means 'use the default')."""
    reg = registry_map()
    if key not in reg:
        return f"Unknown setting {key}."
    s = reg[key]
    if not s.editable:
        return f"{key} is managed by the web app and cannot be set by hand."
    if value is None or value == "":
        return None
    v = value.strip()
    if s.type in ("int", "optional_int"):
        if s.type == "optional_int" and v.lower() in ("none", "null", "off"):
            return None
        try:
            n = float(v.replace("_", ""))
        except ValueError:
            return f"{key} must be a whole number."
        if n != int(n):
            return f"{key} must be a whole number."
        if s.min is not None and n < s.min:
            return f"{key} must be at least {s.min:g}."
        if s.max is not None and n > s.max:
            return f"{key} must be at most {s.max:g}."
    elif s.type == "float":
        try:
            n = float(v.replace("_", ""))
        except ValueError:
            return f"{key} must be a number."
        if s.min is not None and n < s.min:
            return f"{key} must be at least {s.min:g}."
        if s.max is not None and n > s.max:
            return f"{key} must be at most {s.max:g}."
    elif s.type == "bool":
        if v.lower() not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
            return f"{key} must be true or false."
    elif s.type == "choice":
        if v not in s.choices:
            return f"{key} must be one of: {', '.join(s.choices)}."
    if key == "GEMINI_EMBEDDING_MODEL":
        provider = os.environ.get("RP_PROVIDER", providers.DEFAULT_PROVIDER)
        verdict, msg = providers.check_embedding_model(
            provider if provider in providers.PROVIDERS else providers.DEFAULT_PROVIDER, v
        )
        if verdict == "rejected":
            return msg
    return None


def truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


# ── secrets file ────────────────────────────────────────────────────────────


def read_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def write_env_file(path: Path, key: str, value: Optional[str]) -> None:
    from dotenv import set_key, unset_key

    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch(mode=0o600)
    if value:
        set_key(str(path), key, value, quote_mode="never")
    else:
        unset_key(str(path), key, quote_mode="never")


# ── resolution ──────────────────────────────────────────────────────────────


def resolve(
    saved: Dict[str, str],
    env_file: Dict[str, str],
    run_overrides: Optional[Dict[str, str]] = None,
    base_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Tuple[Optional[str], str]]:
    """key -> (value, source) for every registered setting; source is default|server env|.env|saved|run."""
    base = dict(os.environ if base_env is None else base_env)
    out: Dict[str, Tuple[Optional[str], str]] = {}
    for s in registry():
        value, source = s.default, "default"
        if base.get(s.key) not in (None, ""):
            value, source = base[s.key], "server env"
        if env_file.get(s.key) not in (None, ""):
            value, source = env_file[s.key], ".env"
        if saved.get(s.key) not in (None, ""):
            value, source = saved[s.key], "saved"
        if run_overrides and run_overrides.get(s.key) not in (None, ""):
            value, source = run_overrides[s.key], "run"
        out[s.key] = (value, source)
    return out


def child_env(
    resolved: Dict[str, Tuple[Optional[str], str]], base_env: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    """The environment for a run's child process."""
    env = dict(os.environ if base_env is None else base_env)
    for key, (value, source) in resolved.items():
        if source == "default" or value is None:
            continue
        reg = registry_map().get(key)
        if reg is not None and reg.type == "bool":
            value = "1" if truthy(value) else "0"
        env[key] = value
    return env


def snapshot(resolved: Dict[str, Tuple[Optional[str], str]]) -> Dict[str, Dict[str, Optional[str]]]:
    """What a run records about its settings: secrets masked, sources kept."""
    return {
        key: {"value": mask(value) if key in SECRET_KEYS else value, "source": source}
        for key, (value, source) in resolved.items()
    }
