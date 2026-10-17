"""
recipeparser/adapters/webapp/store.py — the web app's SQLite database.

One file (``<data dir>/webapp.db``) holds everything the browser shows and
everything a person or an agent needs afterwards to see what a run did: the
run's configuration, every event, every model call, every error with its
explanation, every recipe with its review notes. Both the server and each run's
child process write to it; WAL mode lets them do so concurrently.

Plain ``sqlite3`` and SQL, no ORM: the schema below is the whole contract.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

SCHEMA_VERSION = 1

ACTIVE_STATUSES = ("queued", "starting", "running", "pausing", "paused", "stopping")
TERMINAL_STATUSES = ("completed", "completed_with_errors", "limit_reached", "cancelled", "failed", "killed", "crashed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    label TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    ended_at REAL,
    status TEXT NOT NULL,
    stage TEXT,
    outcome TEXT,
    book_path TEXT NOT NULL,
    book_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    gen_model TEXT,
    embed_model TEXT,
    options_json TEXT NOT NULL DEFAULT '{}',
    settings_json TEXT NOT NULL DEFAULT '{}',
    prompts_json TEXT NOT NULL DEFAULT '{}',
    parent_run_id TEXT,
    pid INTEGER,
    exit_code INTEGER,
    chunks_in_book INTEGER,
    chunks_total INTEGER,
    chunks_done INTEGER NOT NULL DEFAULT 0,
    recipes_done INTEGER NOT NULL DEFAULT 0,
    skips INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    warnings INTEGER NOT NULL DEFAULT 0,
    outputs_json TEXT NOT NULL DEFAULT '{}',
    control TEXT,
    control_at REAL,
    heartbeat_at REAL,
    log_path TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts REAL NOT NULL,
    level TEXT NOT NULL,
    kind TEXT NOT NULL,
    stage TEXT,
    chunk INTEGER,
    message TEXT NOT NULL,
    data_json TEXT
);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);

CREATE TABLE IF NOT EXISTS api_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts REAL NOT NULL,
    chunk INTEGER,
    stage TEXT,
    kind TEXT NOT NULL,
    model TEXT,
    duration_ms REAL,
    ok INTEGER NOT NULL,
    status_code INTEGER,
    status TEXT,
    error TEXT,
    prompt_chars INTEGER,
    response_chars INTEGER,
    prompt_tokens INTEGER,
    output_tokens INTEGER,
    thought_tokens INTEGER,
    total_tokens INTEGER,
    finish_reason TEXT,
    prompt_text TEXT,
    response_text TEXT
);
CREATE INDEX IF NOT EXISTS api_calls_run ON api_calls(run_id, id);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts REAL NOT NULL,
    severity TEXT NOT NULL,
    code TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    cause TEXT,
    fix TEXT,
    stage TEXT,
    chunk INTEGER,
    exception_type TEXT,
    traceback TEXT,
    api_call_id INTEGER,
    resolved INTEGER NOT NULL DEFAULT 0,
    resolution_note TEXT,
    resolved_at REAL
);
CREATE INDEX IF NOT EXISTS errors_run ON errors(run_id, id);

CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts REAL NOT NULL,
    chunk INTEGER,
    position INTEGER,
    title TEXT NOT NULL,
    needs_review INTEGER NOT NULL DEFAULT 0,
    review_json TEXT NOT NULL DEFAULT '[]',
    categories_json TEXT NOT NULL DEFAULT '[]',
    embedding_json TEXT,
    data_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS recipes_run ON recipes(run_id, id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS prompt_overrides (
    name TEXT PRIMARY KEY,
    template TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- Recipe Lab (lab.py): edits, judgements and pictures beside a run's recipes; the extraction itself is never changed.
CREATE TABLE IF NOT EXISTS recipe_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recipe_id INTEGER NOT NULL,
  run_id TEXT NOT NULL,
  rev INTEGER,
  ts REAL NOT NULL,
  source TEXT NOT NULL,
  target TEXT,
  instruction TEXT,
  summary TEXT,
  status TEXT NOT NULL,
  base_rev INTEGER NOT NULL DEFAULT 0,
  suggestion_json TEXT,
  data_json TEXT NOT NULL,
  model TEXT,
  tokens INTEGER
);
CREATE INDEX IF NOT EXISTS recipe_revisions_recipe ON recipe_revisions(recipe_id, id);

CREATE TABLE IF NOT EXISTS recipe_judgements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recipe_id INTEGER NOT NULL,
  run_id TEXT NOT NULL,
  ts REAL NOT NULL,
  rev INTEGER NOT NULL,
  model TEXT,
  tokens INTEGER,
  data_json TEXT NOT NULL,
  state_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS recipe_judgements_recipe ON recipe_judgements(recipe_id, id);

CREATE TABLE IF NOT EXISTS recipe_pictures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recipe_id INTEGER NOT NULL,
  run_id TEXT NOT NULL,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  prompt TEXT,
  model TEXT,
  cover INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS recipe_pictures_recipe ON recipe_pictures(recipe_id, id);

CREATE TABLE IF NOT EXISTS prompt_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    template TEXT,
    action TEXT NOT NULL,
    saved_at REAL NOT NULL
);
"""


def new_run_id() -> str:
    """Short, sortable, unique: a time prefix and 6 random hex digits."""
    return time.strftime("%y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class Store:
    """Thread-safe access to the web app database. One instance per process."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # executescript commits on its own, so it runs outside tx().
        conn = self._conn()
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    # ── connections ──────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._conn().execute(sql, params).fetchall()]

    def one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        row = self._conn().execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ── runs ─────────────────────────────────────────────────────────────────

    def create_run(self, **fields: Any) -> str:
        run_id = fields.pop("id", None) or new_run_id()
        row = {
            "id": run_id,
            "created_at": time.time(),
            "status": "queued",
            **{k: (_dumps(v) if k.endswith("_json") and not isinstance(v, str) else v) for k, v in fields.items()},
        }
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self.tx() as db:
            db.execute(f"INSERT INTO runs ({cols}) VALUES ({marks})", list(row.values()))
        return run_id

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = [(_dumps(v) if k.endswith("_json") and not isinstance(v, str) else v) for k, v in fields.items()]
        with self.tx() as db:
            db.execute(f"UPDATE runs SET {sets} WHERE id = ?", [*values, run_id])

    def bump_run(self, run_id: str, **deltas: int) -> None:
        sets = ", ".join(f"{k} = {k} + ?" for k in deltas)
        with self.tx() as db:
            db.execute(f"UPDATE runs SET {sets} WHERE id = ?", [*deltas.values(), run_id])

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self.one("SELECT * FROM runs WHERE id = ?", (run_id,))
        return _decode_run(row) if row else None

    def list_runs(self, limit: int = 200) -> List[Dict[str, Any]]:
        return [_decode_run(r) for r in self.query("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))]

    def delete_run(self, run_id: str) -> None:
        with self.tx() as db:
            lab = ("recipe_revisions", "recipe_judgements", "recipe_pictures")
            for table in ("events", "api_calls", "errors", "recipes", *lab):
                db.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
            db.execute("DELETE FROM runs WHERE id = ?", (run_id,))

    def set_control(self, run_id: str, command: Optional[str]) -> None:
        self.update_run(run_id, control=command, control_at=time.time() if command else None)

    # ── events / calls / errors / recipes ────────────────────────────────────

    def add_event(
        self,
        run_id: str,
        kind: str,
        message: str,
        *,
        level: str = "INFO",
        stage: Optional[str] = None,
        chunk: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None,
        ts: Optional[float] = None,
    ) -> int:
        with self.tx() as db:
            cur = db.execute(
                "INSERT INTO events (run_id, ts, level, kind, stage, chunk, message, data_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, ts or time.time(), level, kind, stage, chunk, message, _dumps(data) if data else None),
            )
            return int(cur.lastrowid or 0)

    def add_api_call(self, run_id: str, **fields: Any) -> int:
        fields = {"ts": time.time(), **fields, "run_id": run_id}
        cols = ", ".join(fields)
        with self.tx() as db:
            cur = db.execute(
                f"INSERT INTO api_calls ({cols}) VALUES ({', '.join('?' for _ in fields)})", list(fields.values())
            )
            return int(cur.lastrowid or 0)

    def add_error(self, run_id: str, **fields: Any) -> int:
        fields = {"ts": time.time(), **fields, "run_id": run_id}
        cols = ", ".join(fields)
        counter = "errors" if fields.get("severity") in ("error", "fatal") else "warnings"
        with self.tx() as db:
            cur = db.execute(
                f"INSERT INTO errors ({cols}) VALUES ({', '.join('?' for _ in fields)})", list(fields.values())
            )
            db.execute(f"UPDATE runs SET {counter} = {counter} + 1 WHERE id = ?", (run_id,))
            return int(cur.lastrowid or 0)

    def resolve_error(self, error_id: int, resolved: bool, note: Optional[str]) -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE errors SET resolved = ?, resolution_note = ?, resolved_at = ? WHERE id = ?",
                (1 if resolved else 0, note, time.time() if resolved else None, error_id),
            )

    def add_recipe(self, run_id: str, **fields: Any) -> int:
        fields = {
            "ts": time.time(),
            **{k: (_dumps(v) if k.endswith("_json") and not isinstance(v, str) else v) for k, v in fields.items()},
            "run_id": run_id,
        }
        cols = ", ".join(fields)
        with self.tx() as db:
            cur = db.execute(
                f"INSERT INTO recipes ({cols}) VALUES ({', '.join('?' for _ in fields)})", list(fields.values())
            )
            db.execute("UPDATE runs SET recipes_done = recipes_done + 1 WHERE id = ?", (run_id,))
            return int(cur.lastrowid or 0)

    def update_recipe(self, recipe_id: int, **fields: Any) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = [(_dumps(v) if k.endswith("_json") and not isinstance(v, str) else v) for k, v in fields.items()]
        with self.tx() as db:
            db.execute(f"UPDATE recipes SET {sets} WHERE id = ?", [*values, recipe_id])

    # ── settings / prompts ───────────────────────────────────────────────────

    def saved_settings(self) -> Dict[str, str]:
        return {r["key"]: r["value"] for r in self.query("SELECT key, value FROM settings")}

    def save_settings(self, values: Dict[str, Optional[str]]) -> None:
        now = time.time()
        with self.tx() as db:
            for key, value in values.items():
                if value is None:
                    db.execute("DELETE FROM settings WHERE key = ?", (key,))
                else:
                    db.execute(
                        "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                        (key, value, now),
                    )

    def prompt_overrides(self) -> Dict[str, str]:
        return {r["name"]: r["template"] for r in self.query("SELECT name, template FROM prompt_overrides")}

    def save_prompt(self, name: str, template: Optional[str]) -> None:
        now = time.time()
        with self.tx() as db:
            if template is None:
                db.execute("DELETE FROM prompt_overrides WHERE name = ?", (name,))
                db.execute(
                    "INSERT INTO prompt_history(name, template, action, saved_at) VALUES (?, NULL, 'reset', ?)",
                    (name, now),
                )
            else:
                db.execute(
                    "INSERT INTO prompt_overrides(name, template, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET template = excluded.template, updated_at = excluded.updated_at",
                    (name, template, now),
                )
                db.execute(
                    "INSERT INTO prompt_history(name, template, action, saved_at) VALUES (?, ?, 'save', ?)",
                    (name, template, now),
                )


def _decode_run(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    for key in list(out):
        if key.endswith("_json"):
            raw = out.pop(key)
            try:
                out[key[:-5]] = json.loads(raw) if raw else {}
            except ValueError:
                out[key[:-5]] = {}
    return out
