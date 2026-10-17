"""
recipeparser/adapters/webapp/server.py — the web app's HTTP API and static frontend.

    python -m recipeparser.adapters.webapp            # http://127.0.0.1:8765

JSON API under ``/api``; the browser UI is the static files in ``static/``
(plain ES modules, no build step). Live updates come from one Server-Sent
Events stream, ``/api/stream``, which carries every new event of every run and
a snapshot of the active runs once a second.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from recipeparser import prompts as prompts_mod
from recipeparser import providers
from recipeparser.adapters.webapp import analytics
from recipeparser.adapters.webapp import lab as lab_mod
from recipeparser.adapters.webapp import settings as settings_mod
from recipeparser.adapters.webapp.manager import MAX_CONCURRENCY, MAX_RPM, REPO_ROOT, RunError, RunManager
from recipeparser.adapters.webapp.store import ACTIVE_STATUSES, Store

log = logging.getLogger("recipeparser.adapters.webapp.server")

STATIC_DIR = Path(__file__).resolve().parent / "static"
CORPUS_DIR = REPO_ROOT / "tests" / "goldens" / "corpus"
REPLAY_DIR = REPO_ROOT / "tests" / "goldens" / "gemini"
BOOK_SUFFIXES = (".epub", ".pdf", ".paprikarecipes")


def _ensure_prompts_registered() -> None:
    # gemini.py registers every prompt template at import.
    import recipeparser.gemini  # noqa: F401


def create_app(data_dir: Path, env_file: Path, *, start_monitor: bool = True) -> FastAPI:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "books").mkdir(exist_ok=True)
    store = Store(data_dir / "webapp.db")
    manager = RunManager(store, data_dir, env_file)
    _ensure_prompts_registered()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if start_monitor:
            manager.start()
        log.info("RecipeParser web app ready · data in %s · secrets in %s", data_dir, env_file)
        yield
        manager.shutdown()

    app = FastAPI(title="RecipeParser web app", lifespan=lifespan)
    app.state.store = store
    app.state.manager = manager
    app.state.data_dir = data_dir
    app.state.env_file = env_file

    @app.exception_handler(RunError)
    async def _run_error(_req: Request, exc: RunError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def prices() -> Dict[str, float]:
        resolved = _resolved()
        return {
            "input": float(resolved["RP_PRICE_INPUT_PER_M"][0] or 0),
            "output": float(resolved["RP_PRICE_OUTPUT_PER_M"][0] or 0),
        }

    def _resolved() -> Dict[str, Any]:
        return settings_mod.resolve(store.saved_settings(), settings_mod.read_env_file(env_file))

    def _run_or_404(run_id: str) -> Dict[str, Any]:
        run = store.get_run(run_id)
        if run is None:
            raise HTTPException(404, f"No run {run_id}.")
        return run

    # ── meta ────────────────────────────────────────────────────────────────

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        return {"ok": True, "time": time.time(), "active_runs": len(manager.procs)}

    @app.get("/api/meta")
    def meta() -> Dict[str, Any]:
        from importlib.metadata import PackageNotFoundError, version

        try:
            ver = version("recipeparser")
        except PackageNotFoundError:
            ver = "dev"
        resolved = _resolved()
        return {
            "version": ver,
            "data_dir": str(data_dir),
            "env_file": str(env_file),
            "limits": {"max_rpm": MAX_RPM, "max_concurrency": MAX_CONCURRENCY},
            "providers": [
                {
                    "id": p.id,
                    "label": p.label,
                    "offline": p.offline,
                    "note": p.note,
                    "api_key_env": p.api_key_env,
                    "api_key_set": bool(not p.api_key_env or resolved.get(p.api_key_env, (None, ""))[0]),
                    "generation_models": [m.__dict__ for m in p.generation_models],
                    "embedding_models": [
                        {**m.__dict__, "dims": list(m.dims), "verdict": providers.check_embedding_model(p.id, m.id)[0]}
                        for m in p.embedding_models
                    ],
                }
                for p in providers.PROVIDERS.values()
            ],
            "defaults": {
                "provider": resolved["RP_PROVIDER"][0],
                "gen_model": resolved["GEMINI_MODEL"][0],
                "embed_model": resolved["GEMINI_EMBEDDING_MODEL"][0],
                "concurrency": 16,
                "rpm": MAX_RPM,
            },
            "embedding_dimensions": providers.EMBEDDING_DIMENSIONS,
        }

    # ── settings ─────────────────────────────────────────────────────────────

    @app.get("/api/settings")
    def get_settings() -> Dict[str, Any]:
        resolved = _resolved()
        out = []
        for s in settings_mod.registry():
            value, source = resolved[s.key]
            secret = s.key in settings_mod.SECRET_KEYS
            out.append(
                {
                    **s.as_dict(),
                    "value": settings_mod.mask(value) if secret else value,
                    "is_set": value not in (None, ""),
                    "source": source,
                    "secret": secret,
                }
            )
        return {"settings": out}

    @app.put("/api/settings")
    def put_settings(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        values: Dict[str, Optional[str]] = {
            k: (None if v is None else str(v)) for k, v in (payload.get("values") or {}).items()
        }
        problems = {}
        for key, value in values.items():
            p = settings_mod.validate(key, value)
            if p:
                problems[key] = p
        if problems:
            raise HTTPException(422, {"message": "Some values are not valid.", "problems": problems})
        plain: Dict[str, Optional[str]] = {}
        for key, value in values.items():
            if key in settings_mod.SECRET_KEYS:
                settings_mod.write_env_file(env_file, key, value or None)
                log.warning("Secret %s %s in %s", key, "updated" if value else "removed", env_file)
            else:
                plain[key] = value if value not in ("",) else None
        store.save_settings(plain)
        log.info("Settings saved: %s", ", ".join(f"{k}={v!r}" for k, v in plain.items()) or "(secrets only)")
        return get_settings()

    @app.post("/api/settings/check-embedding")
    def check_embedding(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        provider = payload.get("provider") or providers.DEFAULT_PROVIDER
        model = payload.get("model") or ""
        try:
            verdict, message = providers.check_embedding_model(provider, model)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"verdict": verdict, "message": message, "dimensions": providers.EMBEDDING_DIMENSIONS}

    # ── prompts ──────────────────────────────────────────────────────────────

    @app.get("/api/prompts")
    def get_prompts() -> Dict[str, Any]:
        overrides = store.prompt_overrides()
        out = []
        for s in prompts_mod.specs():
            out.append(
                {
                    "name": s.name,
                    "title": s.title,
                    "description": s.description,
                    "stage": s.stage,
                    "placeholders": list(s.placeholders),
                    "required": list(s.required),
                    "default": s.default_template(),
                    "override": overrides.get(s.name),
                    "history": store.query(
                        "SELECT id, template, action, saved_at FROM prompt_history WHERE name = ? "
                        "ORDER BY id DESC LIMIT 20",
                        (s.name,),
                    ),
                }
            )
        return {"prompts": out}

    @app.put("/api/prompts/{name}")
    def put_prompt(name: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        template = payload.get("template")
        if not isinstance(template, str):
            raise HTTPException(422, "template must be text.")
        problems = prompts_mod.validate(name, template)
        if problems:
            raise HTTPException(422, {"message": "The prompt cannot be used.", "problems": problems})
        default = prompts_mod.spec(name).default_template()
        store.save_prompt(name, None if template == default else template)
        log.warning("Prompt %s %s", name, "reset to default" if template == default else "edited")
        return get_prompts()

    @app.post("/api/prompts/{name}/validate")
    def validate_prompt(name: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        return {"problems": prompts_mod.validate(name, payload.get("template") or "")}

    @app.delete("/api/prompts/{name}")
    def reset_prompt(name: str) -> Dict[str, Any]:
        if name not in {s.name for s in prompts_mod.specs()}:
            raise HTTPException(404, f"No prompt {name}.")
        store.save_prompt(name, None)
        log.warning("Prompt %s reset to default", name)
        return get_prompts()

    # ── books ────────────────────────────────────────────────────────────────

    @app.get("/api/books")
    def books() -> Dict[str, Any]:
        uploaded = [
            {
                "name": p.name,
                "path": str(p),
                "size": p.stat().st_size,
                "source": "uploaded",
                "replay": (REPLAY_DIR / p.name).is_dir(),
            }
            for p in sorted((data_dir / "books").iterdir())
            if p.suffix.lower() in BOOK_SUFFIXES
        ]
        corpus = []
        if CORPUS_DIR.is_dir():
            corpus = [
                {
                    "name": p.name,
                    "path": str(p),
                    "size": p.stat().st_size,
                    "source": "golden corpus",
                    "replay": (REPLAY_DIR / p.name).is_dir(),
                }
                for p in sorted(CORPUS_DIR.iterdir())
                if p.suffix.lower() in BOOK_SUFFIXES
            ]
        recent = [
            {"name": r["book_name"], "path": r["book_path"], "source": "recent"}
            for r in store.query(
                "SELECT book_name, book_path, MAX(created_at) AS t FROM runs "
                "GROUP BY book_path ORDER BY t DESC LIMIT 10"
            )
            if Path(r["book_path"]).exists()
        ]
        return {"uploaded": uploaded, "corpus": corpus, "recent": recent}

    @app.post("/api/books")
    async def upload_book(file: UploadFile = File(...)) -> Dict[str, Any]:
        name = Path(file.filename or "book").name
        if Path(name).suffix.lower() not in BOOK_SUFFIXES:
            raise HTTPException(422, f"{name}: only .epub, .pdf and .paprikarecipes files can be processed.")
        target = data_dir / "books" / name
        stem, suffix, n = target.stem, target.suffix, 1
        while target.exists():
            n += 1
            target = target.with_name(f"{stem} ({n}){suffix}")
        with open(target, "wb") as fh:
            shutil.copyfileobj(file.file, fh)
        log.info("Book uploaded: %s (%d bytes)", target, target.stat().st_size)
        return {"name": target.name, "path": str(target), "size": target.stat().st_size}

    # ── runs ─────────────────────────────────────────────────────────────────

    @app.get("/api/runs")
    def list_runs(limit: int = 200) -> Dict[str, Any]:
        return {"runs": [_run_summary(r) for r in store.list_runs(limit)]}

    @app.post("/api/runs")
    def create_run(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        run_id = manager.start_run(
            book_path=str(payload.get("book_path") or ""),
            provider=payload.get("provider"),
            options=payload.get("options") or {},
            label=payload.get("label"),
        )
        return {"id": run_id, "run": _run_summary(store.get_run(run_id) or {})}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> Dict[str, Any]:
        run = _run_or_404(run_id)
        chunk_rows = store.query(
            "SELECT chunk, kind, stage, data_json FROM events WHERE run_id = ? AND chunk IS NOT NULL AND "
            "kind IN ('stage', 'chunk.done', 'chunk.failed', 'chunk.skip') ORDER BY id",
            (run_id,),
        )
        chunks: Dict[int, Dict[str, Any]] = {}
        for row in chunk_rows:
            c = chunks.setdefault(
                row["chunk"], {"chunk": row["chunk"], "state": "queued", "stage": None, "recipes": 0, "skips": 0}
            )
            if row["kind"] == "stage":
                c["stage"] = row["stage"]
                if c["state"] == "queued":
                    c["state"] = "active"
            elif row["kind"] == "chunk.done":
                d = json.loads(row["data_json"] or "{}")
                c["state"] = d.get("state", "done")
                c["recipes"] = d.get("recipes", 0)
                c["skips"] = len(d.get("skips") or [])
            elif row["kind"] == "chunk.failed":
                c["failed"] = True
        selected = (run.get("outputs") or {}).get("selected_chunks") or []
        grid = []
        for i in range(int(run.get("chunks_total") or 0)):
            c = chunks.get(i, {"chunk": i, "state": "queued", "stage": None, "recipes": 0, "skips": 0})
            if run["status"] not in ACTIVE_STATUSES and c["state"] in ("queued", "active"):
                c = {**c, "state": "not processed" if c["state"] == "queued" else "interrupted"}
            c["book_chunk"] = selected[i] if i < len(selected) else None
            grid.append(c)
        stage_counts: Dict[str, int] = {}
        for c in grid:
            if c["state"] == "active":
                stage_counts[c["stage"] or "QUEUED"] = stage_counts.get(c["stage"] or "QUEUED", 0) + 1
        return {
            "run": _run_summary(run, full=True),
            "chunks": grid,
            "active_stages": stage_counts,
            "can": _capabilities(run),
            "failed_chunks": manager.failed_chunks(run_id) if run["status"] not in ACTIVE_STATUSES else [],
            "remaining_chunks": len(manager.remaining_chunks(run_id)) if run["status"] not in ACTIVE_STATUSES else None,
        }

    @app.post("/api/runs/{run_id}/{command}")
    def run_command(run_id: str, command: str, payload: Optional[Dict[str, Any]] = Body(None)) -> Dict[str, Any]:
        _run_or_404(run_id)
        if command in ("pause", "resume", "stop"):
            run = manager.control(run_id, command)
            return {"run": _run_summary(run)}
        if command == "kill":
            return {"run": _run_summary(manager.kill(run_id))}
        if command == "restart":
            mode = (payload or {}).get("mode", "same")
            new_id = manager.restart(run_id, mode, (payload or {}).get("options"))
            return {"id": new_id}
        raise HTTPException(404, f"Unknown command {command}.")

    @app.patch("/api/runs/{run_id}")
    def patch_run(run_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        _run_or_404(run_id)
        if "label" in payload:
            store.update_run(run_id, label=payload["label"] or None)
        return {"run": _run_summary(store.get_run(run_id) or {})}

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: str) -> Dict[str, Any]:
        run = _run_or_404(run_id)
        if run["status"] in ACTIVE_STATUSES:
            raise HTTPException(409, "Stop or kill the run before deleting it.")
        store.delete_run(run_id)
        shutil.rmtree(data_dir / "runs" / run_id, ignore_errors=True)
        log.warning("Run %s deleted", run_id)
        return {"ok": True}

    @app.get("/api/runs/{run_id}/events")
    def run_events(
        run_id: str,
        after: int = 0,
        before: Optional[int] = None,
        limit: int = Query(500, le=5000),
        level: Optional[str] = None,
        kind: Optional[str] = None,
        chunk: Optional[int] = None,
        q: Optional[str] = None,
    ) -> Dict[str, Any]:
        sql = "SELECT * FROM events WHERE run_id = ? AND id > ?"
        params: List[Any] = [run_id, after]
        if before:
            sql += " AND id < ?"
            params.append(before)
        if level:
            levels = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
            allowed = [lv for lv, n in levels.items() if n >= levels.get(level.upper(), 0)]
            sql += f" AND level IN ({','.join('?' * len(allowed))})"
            params.extend(allowed)
        if kind:
            sql += " AND kind LIKE ?"
            params.append(kind.replace("*", "%"))
        if chunk is not None:
            sql += " AND chunk = ?"
            params.append(chunk)
        if q:
            sql += " AND message LIKE ?"
            params.append(f"%{q}%")
        sql += " ORDER BY id DESC LIMIT ?" if before or not after else " ORDER BY id ASC LIMIT ?"
        params.append(limit)
        rows = store.query(sql, params)
        rows.sort(key=lambda r: r["id"])
        return {"events": [_event(r) for r in rows]}

    @app.get("/api/runs/{run_id}/log", response_class=PlainTextResponse)
    def run_log(run_id: str, lines: int = 2000) -> str:
        _run_or_404(run_id)
        path = data_dir / "runs" / run_id / "run.log"
        if not path.exists():
            return ""
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-lines:])

    @app.get("/api/runs/{run_id}/errors")
    def run_errors(run_id: str) -> Dict[str, Any]:
        return {"errors": store.query("SELECT * FROM errors WHERE run_id = ? ORDER BY id DESC", (run_id,))}

    @app.get("/api/errors")
    def all_errors(
        unresolved: bool = False, code: Optional[str] = None, severity: Optional[str] = None, limit: int = 500
    ) -> Dict[str, Any]:
        sql = (
            "SELECT e.*, r.book_name, r.label AS run_label, r.status AS run_status FROM errors e "
            "LEFT JOIN runs r ON r.id = e.run_id WHERE 1=1"
        )
        params: List[Any] = []
        if unresolved:
            sql += " AND e.resolved = 0"
        if code:
            sql += " AND e.code = ?"
            params.append(code)
        if severity:
            sql += " AND e.severity = ?"
            params.append(severity)
        sql += " ORDER BY e.id DESC LIMIT ?"
        params.append(limit)
        rows = store.query(sql, params)
        summary = store.query(
            "SELECT code, title, category, severity, COUNT(*) AS n, SUM(resolved = 0) AS unresolved, "
            "COUNT(DISTINCT run_id) AS runs, MAX(ts) AS last_ts, MAX(fix) AS fix, MAX(cause) AS cause "
            "FROM errors GROUP BY code ORDER BY last_ts DESC"
        )
        return {"errors": rows, "summary": summary}

    @app.patch("/api/errors/{error_id}")
    def patch_error(error_id: int, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        store.resolve_error(error_id, bool(payload.get("resolved")), payload.get("note"))
        return store.one("SELECT * FROM errors WHERE id = ?", (error_id,)) or {}

    @app.get("/api/runs/{run_id}/calls")
    def run_calls(
        run_id: str,
        after: int = 0,
        limit: int = Query(500, le=5000),
        ok: Optional[int] = None,
        stage: Optional[str] = None,
        chunk: Optional[int] = None,
    ) -> Dict[str, Any]:
        sql = (
            "SELECT id, ts, chunk, stage, kind, model, duration_ms, ok, status_code, status, error, "
            "prompt_chars, response_chars, prompt_tokens, output_tokens, thought_tokens, total_tokens, "
            "finish_reason FROM api_calls WHERE run_id = ? AND id > ?"
        )
        params: List[Any] = [run_id, after]
        if ok is not None:
            sql += " AND ok = ?"
            params.append(ok)
        if stage:
            sql += " AND stage = ?"
            params.append(stage)
        if chunk is not None:
            sql += " AND chunk = ?"
            params.append(chunk)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return {"calls": store.query(sql, params)}

    @app.get("/api/calls/{call_id}")
    def get_call(call_id: int) -> Dict[str, Any]:
        row = store.one("SELECT * FROM api_calls WHERE id = ?", (call_id,))
        if row is None:
            raise HTTPException(404, f"No API call {call_id}.")
        return row

    @app.get("/api/runs/{run_id}/recipes")
    def run_recipes(run_id: str, needs_review: Optional[int] = None) -> Dict[str, Any]:
        sql = (
            "SELECT id, ts, chunk, position, title, needs_review, review_json, categories_json, embedding_json "
            "FROM recipes WHERE run_id = ?"
        )
        params: List[Any] = [run_id]
        if needs_review is not None:
            sql += " AND needs_review = ?"
            params.append(needs_review)
        rows = store.query(sql + " ORDER BY id", params)
        for r in rows:
            r["review"] = json.loads(r.pop("review_json") or "[]")
            r["categories"] = json.loads(r.pop("categories_json") or "[]")
            r["embedding"] = json.loads(r.pop("embedding_json") or "null")
        return {"recipes": rows}

    @app.get("/api/recipes/{recipe_id}")
    def get_recipe(recipe_id: int) -> Dict[str, Any]:
        r = store.one("SELECT * FROM recipes WHERE id = ?", (recipe_id,))
        if r is None:
            raise HTTPException(404, f"No recipe {recipe_id}.")
        r["review"] = json.loads(r.pop("review_json") or "[]")
        r["categories"] = json.loads(r.pop("categories_json") or "[]")
        r["embedding"] = json.loads(r.pop("embedding_json") or "null")
        r["data"] = json.loads(r.pop("data_json") or "{}")
        r["calls"] = store.query(
            "SELECT id, stage, kind, ok, status_code, duration_ms, total_tokens FROM api_calls "
            "WHERE run_id = ? AND chunk IS ? ORDER BY id",
            (r["run_id"], r["chunk"]),
        )
        return r

    @app.get("/api/runs/{run_id}/analytics")
    def run_analytics(run_id: str) -> Dict[str, Any]:
        _run_or_404(run_id)
        return analytics.run_metrics(store, run_id, prices())

    @app.get("/api/compare")
    def compare(ids: str) -> Dict[str, Any]:
        run_ids = [i for i in ids.split(",") if i]
        if len(run_ids) < 2:
            raise HTTPException(422, "Pick at least two runs to compare.")
        for rid in run_ids:
            _run_or_404(rid)
        return analytics.compare(store, run_ids, prices())

    @app.get("/api/runs/{run_id}/files/{kind}")
    def run_file(run_id: str, kind: str) -> FileResponse:
        run = _run_or_404(run_id)
        run_dir = data_dir / "runs" / run_id
        if kind in ("run.log", "crash.log", "prompts.json"):
            path = run_dir / kind
        else:
            p = (run.get("outputs") or {}).get(kind)
            if not p:
                raise HTTPException(404, f"The run has no {kind} output.")
            path = Path(p)
        if not path.exists():
            raise HTTPException(404, f"{path.name} does not exist.")
        return FileResponse(path, filename=path.name)

    # ── live stream ──────────────────────────────────────────────────────────

    @app.get("/api/stream")
    async def stream(request: Request, after: Optional[int] = None) -> StreamingResponse:
        async def gen() -> AsyncIterator[str]:
            last = after
            if last is None:
                row = store.one("SELECT MAX(id) AS m FROM events")
                last = int(row["m"] or 0) if row else 0
            last_runs = 0.0
            yield f"event: hello\ndata: {json.dumps({'after': last})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                rows = await asyncio.to_thread(
                    store.query, "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT 500", (last,)
                )
                if rows:
                    last = rows[-1]["id"]
                    yield f"event: events\ndata: {json.dumps([_event(r) for r in rows], default=str)}\n\n"
                now = time.time()
                if now - last_runs >= 1.0:
                    last_runs = now
                    runs = await asyncio.to_thread(
                        store.query,
                        "SELECT * FROM runs WHERE status IN (%s) OR ended_at > ? ORDER BY created_at DESC LIMIT 50"
                        % ",".join("?" * len(ACTIVE_STATUSES)),
                        (*ACTIVE_STATUSES, now - 15),
                    )
                    from recipeparser.adapters.webapp.store import _decode_run

                    yield (
                        "event: runs\ndata: "
                        + json.dumps([_run_summary(_decode_run(r)) for r in runs], default=str)
                        + "\n\n"
                    )
                await asyncio.sleep(0.4)

        return StreamingResponse(
            gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    # -- recipe lab: viewer, judge, refine, pictures ---------------------------

    lab_mod.register(app, store, data_dir, _resolved)

    # ── frontend ─────────────────────────────────────────────────────────────

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app


def _capabilities(run: Dict[str, Any]) -> Dict[str, bool]:
    status = run.get("status")
    active = status in ACTIVE_STATUSES
    return {
        "pause": status in ("running", "starting"),
        "resume": status in ("paused", "pausing"),
        "stop": active and status != "stopping",
        "kill": active,
        "restart": True,
        "retry_failed": not active,
        "continue_remaining": not active,
        "delete": not active,
    }


def _run_summary(run: Dict[str, Any], full: bool = False) -> Dict[str, Any]:
    if not run:
        return {}
    keys = [
        "id",
        "label",
        "created_at",
        "started_at",
        "ended_at",
        "status",
        "stage",
        "outcome",
        "book_name",
        "book_path",
        "provider",
        "gen_model",
        "embed_model",
        "parent_run_id",
        "pid",
        "exit_code",
        "chunks_in_book",
        "chunks_total",
        "chunks_done",
        "recipes_done",
        "skips",
        "errors",
        "warnings",
        "heartbeat_at",
        "options",
        "outputs",
    ]
    out = {k: run.get(k) for k in keys}
    total = run.get("chunks_total") or 0
    out["progress"] = round((run.get("chunks_done") or 0) * 100 / total, 1) if total else 0
    end = run.get("ended_at") or time.time()
    out["elapsed"] = round(end - (run.get("started_at") or run.get("created_at") or end), 1)
    out["active"] = run.get("status") in ACTIVE_STATUSES
    out["can"] = _capabilities(run)
    if full:
        out["settings"] = run.get("settings")
        out["prompts"] = run.get("prompts")
        out["log_path"] = run.get("log_path")
    return out


def _event(r: Dict[str, Any]) -> Dict[str, Any]:
    data = r.get("data_json")
    return {
        "id": r["id"],
        "run_id": r["run_id"],
        "ts": r["ts"],
        "level": r["level"],
        "kind": r["kind"],
        "stage": r["stage"],
        "chunk": r["chunk"],
        "message": r["message"],
        "data": json.loads(data) if data else None,
    }


def default_data_dir() -> Path:
    from recipeparser.paths import get_app_data_dir

    env = os.environ.get("RP_WEBAPP_DATA_DIR", "").strip()
    return Path(env) if env else get_app_data_dir() / "webapp"
