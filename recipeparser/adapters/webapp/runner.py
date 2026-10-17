"""
recipeparser/adapters/webapp/runner.py — one extraction run, in its own process.

    python -m recipeparser.adapters.webapp.runner --run-id <id> --data-dir <dir>

The server starts this with the run's resolved settings as its environment (so
every ``config.py`` constant takes the value chosen in the browser) in its own
process group (so Kill can end it, and anything it started, at once).

What it does, in order, recording each step as events the browser shows live:

1. preflight — the provider, the API key, one connectivity call, one embedding
   probe that must return EMBEDDING_DIMENSIONS values;
2. read the book into chunks and select the part the run asked for (a percentage
   range of the book, or explicit chunk numbers when retrying failed chunks);
3. run ``RecipePipeline`` with pause / resume / stop driven by the ``control``
   column the server writes, and with every model call instrumented;
4. write the outputs locally (Paprika and Cayenne archives, recipes.json) —
   never Supabase;
5. settle the final status and a one-line outcome.

A failure anywhere is explained (``errors.explain``) and recorded before the
process exits; a crash it cannot catch (a segfault, a kill) is detected by the
server from the process's exit.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import logging
import math
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from recipeparser.adapters.webapp.errors import Explanation, explain, explain_reason
from recipeparser.adapters.webapp.instrument import ConsoleFormatter, InstrumentedClient, StoreLogHandler
from recipeparser.adapters.webapp.store import Store

log = logging.getLogger("recipeparser.adapters.webapp.runner")

REPO_ROOT = Path(__file__).resolve().parents[3]
MILESTONES = (10, 25, 50, 75, 90, 100)


class RunAborted(Exception):
    """A preflight or setup step failed; carries the explanation to record."""

    def __init__(self, explanation: Explanation) -> None:
        super().__init__(explanation.message)
        self.explanation = explanation


def _fatal(code: str, title: str, message: str, cause: str, fix: str, category: str = "config") -> RunAborted:
    return RunAborted(
        Explanation(code=code, category=category, severity="fatal", title=title, message=message, cause=cause, fix=fix)
    )


def _fmt_secs(secs: float) -> str:
    secs = int(round(secs))
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"


def embedding_stats(vector: List[float], model: str) -> Dict[str, Any]:
    n = len(vector)
    if not n:
        return {"dims": 0, "model": model}
    norm = math.sqrt(sum(v * v for v in vector))
    mean = sum(vector) / n
    return {
        "model": model,
        "dims": n,
        "norm": round(norm, 6),
        "mean": round(mean, 6),
        "min": round(min(vector), 6),
        "max": round(max(vector), 6),
        "zeros": sum(1 for v in vector if v == 0.0),
        "head": [round(v, 6) for v in vector[:12]],
    }


def select_chunks(total: int, options: Dict[str, Any]) -> List[int]:
    """Book chunk numbers this run processes, from explicit numbers or a percentage range of the book."""
    explicit = options.get("chunk_indices")
    if explicit:
        return sorted({int(i) for i in explicit if 0 <= int(i) < total})
    start_pct = float(options.get("start_percent") or 0)
    raw_pct = options.get("book_percent")
    book_pct = float(raw_pct) if raw_pct is not None else 100.0
    start_pct = min(max(start_pct, 0.0), 100.0)
    book_pct = min(max(book_pct, 0.0), 100.0 - start_pct)
    start = int(math.floor(total * start_pct / 100.0))
    count = int(math.ceil(total * book_pct / 100.0))
    if book_pct > 0:
        count = max(count, 1)
    return list(range(start, min(total, start + count)))


class Runner:
    def __init__(self, store: Store, run_id: str, data_dir: Path) -> None:
        self.store = store
        self.run_id = run_id
        self.data_dir = data_dir
        run = store.get_run(run_id)
        if run is None:
            raise SystemExit(f"No run {run_id} in {store.path}")
        self.run = run
        self.options: Dict[str, Any] = run.get("options") or {}
        self.run_dir = data_dir / "runs" / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.started = time.time()
        self.controller: Any = None
        self.client: Optional[InstrumentedClient] = None
        self.stop_flag = threading.Event()
        self.limit_reached = False
        self.recipe_count = 0
        self.max_recipes = int(self.options.get("max_recipes") or 0)
        self.recipe_rows: Dict[int, int] = {}
        self.chunk_recipes: Dict[int, int] = {}
        self.chunk_stage: Dict[int, str] = {}
        self.chunk_skips: Dict[int, List[str]] = {}
        self.chunk_map: List[int] = []
        self.milestones_hit: Set[int] = set()
        self.status = "starting"
        self.calls = 0
        self.failed_calls = 0

    # ── logging ────────────────────────────────────────────────────────────────

    def setup_logging(self) -> None:
        level_name = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
        level = logging.getLevelName(level_name)
        if not isinstance(level, int):
            level = logging.INFO
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
        root.setLevel(level)
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(ConsoleFormatter(self.run_id))
        root.addHandler(console)
        log_path = self.run_dir / "run.log"
        fileh = logging.FileHandler(log_path, encoding="utf-8")
        fileh.setFormatter(ConsoleFormatter(self.run_id, color=False))
        root.addHandler(fileh)
        self.store_handler = StoreLogHandler(self.store, self.run_id, level)
        root.addHandler(self.store_handler)
        logging.captureWarnings(True)
        import warnings

        # ebooklib's own deprecation notices say nothing about this run.
        warnings.filterwarnings("ignore", module=r"ebooklib(\..*)?")
        self.crash_file = open(self.run_dir / "crash.log", "w", encoding="utf-8")
        faulthandler.enable(self.crash_file)
        self.store.update_run(self.run_id, log_path=str(log_path))

    # ── events ────────────────────────────────────────────────────────────────

    def event(
        self,
        kind: str,
        message: str,
        *,
        level: str = "INFO",
        stage: Optional[str] = None,
        chunk: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None,
        echo: bool = True,
    ) -> None:
        self.store.add_event(self.run_id, kind, message, level=level, stage=stage, chunk=chunk, data=data)
        if echo:
            # Console only: the event row is the browser's copy.
            logging.getLogger("recipeparser.adapters.webapp.event").handle(
                logging.LogRecord(
                    "recipeparser.adapters.webapp.event", logging.getLevelName(level), __file__, 0, message, None, None
                )
            )

    def record_error(self, e: Explanation, *, stage: Optional[str] = None, chunk: Optional[int] = None) -> None:
        self.store.add_error(
            self.run_id,
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
        )

    def set_status(self, status: str, **fields: Any) -> None:
        self.status = status
        self.store.update_run(self.run_id, status=status, **fields)

    # ── control ───────────────────────────────────────────────────────────────

    def control_loop(self) -> None:
        last_beat = 0.0
        announced_pause = False
        while not self.stop_flag.is_set():
            try:
                row = self.store.one("SELECT control FROM runs WHERE id = ?", (self.run_id,))
                command = row["control"] if row else None
                if command:
                    self.apply_control(command)
                    self.store.set_control(self.run_id, None)
                c = self.controller
                if c is not None:
                    st = c.status.value
                    if st == "paused" and not announced_pause:
                        announced_pause = True
                        self.set_status("paused")
                        self.event(
                            "run.paused",
                            "Paused: no new model calls start until you resume. Calls already in flight finish.",
                            level="WARNING",
                        )
                    elif st == "running" and announced_pause:
                        announced_pause = False
                        self.set_status("running")
                        self.event("run.resumed", "Resumed.")
                now = time.time()
                if now - last_beat >= 1.0:
                    last_beat = now
                    self.store.update_run(self.run_id, heartbeat_at=now)
            except Exception:
                log.exception("Control loop error (the run continues).")
            self.stop_flag.wait(0.25)

    def apply_control(self, command: str) -> None:
        c = self.controller
        if c is None:
            if command == "stop":
                self.event(
                    "control.stop", "Stop requested before processing began: the run ends after setup.", level="WARNING"
                )
                self.stop_requested_early = True
            return
        if command == "pause":
            if c.request_pause():
                self.set_status("pausing")
                self.event(
                    "control.pause", "Pause requested: chunks hold before their next model call.", level="WARNING"
                )
            else:
                self.event("control.ignored", f"Pause ignored: the pipeline is {c.status.value}.", level="WARNING")
        elif command == "resume":
            if c.request_resume():
                self.event("control.resume", "Resume requested.")
            else:
                self.event("control.ignored", f"Resume ignored: the pipeline is {c.status.value}.", level="WARNING")
        elif command == "stop":
            if c.request_cancel():
                self.set_status("stopping")
                self.event(
                    "control.stop",
                    "Stop requested: finishing the calls in flight, then writing what was extracted so far.",
                    level="WARNING",
                )
            else:
                self.event("control.ignored", f"Stop ignored: the pipeline is {c.status.value}.", level="WARNING")

    # ── steps ─────────────────────────────────────────────────────────────────

    def build_client(self) -> Any:
        from recipeparser import config, providers

        provider_id = self.run["provider"]
        provider = providers.get_provider(provider_id)
        verdict, msg = providers.check_embedding_model(provider_id, config.GEMINI_EMBEDDING_MODEL)
        if verdict == "rejected":
            raise _fatal(
                "EMBED_DIMS",
                "Embedding model cannot be used",
                msg,
                f"Every recipe embedding must be exactly {config.EMBEDDING_DIMENSIONS} values.",
                "Choose another embedding model in Settings → Provider & models.",
            )
        if verdict == "unverified":
            self.event("preflight.embedding", msg, level="WARNING")
        latency = 0
        if provider.offline:
            golden_dir = REPO_ROOT / "tests" / "goldens"
            if not golden_dir.is_dir():
                raise _fatal(
                    "REPLAY_UNAVAILABLE",
                    "Offline replay is not available",
                    str(golden_dir),
                    "Replay reads the recordings in tests/goldens, which this installation lacks.",
                    "Run from a source checkout, or switch the provider to Gemini.",
                )
            if str(REPO_ROOT) not in sys.path:
                sys.path.insert(0, str(REPO_ROOT))
            import importlib

            # Imported by name: the recordings live in the test tree, which is not part of the package.
            GoldenClient = importlib.import_module("tests.goldens.golden_client").GoldenClient
            GEMINI_DIR = importlib.import_module("tests.goldens.paths").GEMINI_DIR

            fixture = Path(self.run["book_path"]).name
            if not (GEMINI_DIR / fixture).is_dir():
                raise _fatal(
                    "REPLAY_MISSING",
                    "No recordings for this book",
                    fixture,
                    "Offline replay only has recorded replies for the golden corpus books.",
                    "Pick a book from the golden corpus list, or switch the provider to Gemini.",
                )
            import warnings

            # Every replayed reply whose prompt changed since recording warns; one line says it instead.
            warnings.filterwarnings("ignore", message="prompt_sha256 mismatch")
            self.event("preflight.replay", "Offline replay: answers come from the recordings in "
                       f"tests/goldens/gemini/{fixture}; edited prompts are not re-asked.", level="WARNING")
            inner: Any = GoldenClient(fixture_id=fixture, root=GEMINI_DIR)
            latency = int(os.environ.get("RP_REPLAY_LATENCY_MS", "250") or 0)
        else:
            key = os.environ.get(provider.api_key_env, "").strip()
            if not key:
                raise _fatal(
                    "NO_API_KEY",
                    "No API key",
                    f"{provider.api_key_env} is not set.",
                    f"{provider.label} needs an API key and none is configured.",
                    f"Paste the key in Settings → API keys ({provider.api_key_env}).",
                    category="auth",
                )
            from google import genai

            inner = genai.Client(api_key=key)
        store_text = os.environ.get("RP_STORE_PROMPTS", "1").strip().lower() in ("1", "true", "yes", "on")
        self.client = InstrumentedClient(
            inner, self.store, self.run_id, store_text=store_text, latency_ms=latency, on_call=self._on_call
        )
        return self.client

    def _on_call(self, row: Dict[str, Any]) -> None:
        self.calls += 1
        if not row.get("ok"):
            self.failed_calls += 1

    def preflight(self) -> None:
        from recipeparser import config, gemini
        from recipeparser.core import run_context

        run_context.set_chunk(None)
        run_context.set_stage("PREFLIGHT")
        self.event("preflight.start", "Preflight: provider, key, model and embedding checks.", stage="PREFLIGHT")
        client = self.client
        if not self.run_provider_offline():
            t0 = time.monotonic()
            if not gemini.verify_connectivity(client):
                last = self.store.one("SELECT * FROM errors WHERE run_id = ? ORDER BY id DESC LIMIT 1", (self.run_id,))
                if last:
                    raise RunAborted(
                        Explanation(
                            code=last["code"],
                            category=last["category"],
                            severity="fatal",
                            title="Preflight failed: " + last["title"],
                            message=last["message"],
                            cause=last["cause"] or "",
                            fix=last["fix"] or "",
                            status_code=None,
                        )
                    )
                raise _fatal(
                    "PREFLIGHT",
                    "Connectivity check failed",
                    "The model did not answer the probe.",
                    "See the log for the exception.",
                    "Check the key, model name and network.",
                )
            self.event(
                "preflight.connectivity",
                f"Model {config.GEMINI_MODEL} answered in {(time.monotonic() - t0) * 1000:.0f} ms.",
                stage="PREFLIGHT",
            )
        t0 = time.monotonic()
        try:
            vector = gemini.get_embeddings("RecipeParser embedding probe", client, max_retries=1)
        except Exception as exc:
            e = explain(exc)
            e.severity = "fatal"
            e.title = "Embedding probe failed: " + e.title
            raise RunAborted(e) from exc
        if len(vector) != config.EMBEDDING_DIMENSIONS:
            raise _fatal(
                "EMBED_DIMS",
                "Embedding model returned the wrong size",
                f"{config.GEMINI_EMBEDDING_MODEL} returned {len(vector)} values.",
                f"Recipes are stored as {config.EMBEDDING_DIMENSIONS}-value vectors.",
                f"Choose an embedding model that supports output_dimensionality {config.EMBEDDING_DIMENSIONS}.",
            )
        stats = embedding_stats(vector, config.GEMINI_EMBEDDING_MODEL)
        self.event(
            "preflight.embedding",
            f"Embedding model {config.GEMINI_EMBEDDING_MODEL} returned {len(vector)} values "
            f"in {(time.monotonic() - t0) * 1000:.0f} ms.",
            stage="PREFLIGHT",
            data=stats,
        )
        run_context.clear()

    def run_provider_offline(self) -> bool:
        from recipeparser import providers

        return providers.get_provider(self.run["provider"]).offline

    def read_book(self) -> List[Any]:
        from recipeparser.core import run_context

        run_context.set_stage("READING")
        path = Path(self.run["book_path"])
        if not path.is_file():
            raise _fatal(
                "NOT_FOUND",
                "Book not found",
                str(path),
                "The book file does not exist (any more).",
                "Choose the book again on the New run page.",
                category="input",
            )
        suffix = path.suffix.lower()
        if suffix == ".epub":
            from recipeparser.io.readers.epub import EpubReader

            reader: Any = EpubReader()
        elif suffix == ".pdf":
            from recipeparser.io.readers.pdf import PdfReader

            reader = PdfReader()
        elif suffix == ".paprikarecipes":
            from recipeparser.io.readers.paprika import PaprikaReader

            reader = PaprikaReader()
        else:
            raise _fatal(
                "UNSUPPORTED",
                "Unsupported file type",
                suffix,
                "Runs read .epub, .pdf and .paprikarecipes files.",
                "Choose a supported file.",
                category="input",
            )
        self.event("book.reading", f"Reading {path.name} ({path.stat().st_size / 1e6:.1f} MB)…", stage="READING")
        t0 = time.monotonic()
        chunks: List[Any] = reader.read(str(path))
        if not chunks:
            raise _fatal(
                "EMPTY_BOOK",
                "The book produced no text",
                path.name,
                "The reader found nothing to extract (an empty, image-only or DRM-protected file).",
                "Check the file opens in a reader; scanned PDFs over RP_PDF_OCR_MAX_PAGES pages are refused.",
                category="input",
            )
        sizes = [len(c.text or "") for c in chunks]
        images = sum(len(c.images or {}) for c in chunks)
        self.event(
            "book.read",
            f"{path.name}: {len(chunks)} chunks, {sum(sizes):,} characters, {images} images "
            f"({(time.monotonic() - t0):.1f}s).",
            stage="READING",
            data={"chunks": len(chunks), "chars": sum(sizes), "images": images, "chunk_chars": sizes[:2000]},
        )
        run_context.clear()
        return chunks

    def run_pipeline(self, chunks: List[Any]) -> List[Any]:
        from recipeparser.core import run_context
        from recipeparser.core.fsm import PipelineController
        from recipeparser.core.pipeline import RecipePipeline

        concurrency = int(self.options.get("concurrency") or 8)
        rpm = int(self.options.get("rpm") or 500)
        total = len(chunks)
        self.controller = PipelineController(on_stage_change=self._on_stage)
        if getattr(self, "stop_requested_early", False):
            self.controller.cancel_requested = True
            return []
        pipeline = RecipePipeline(
            client=self.client,
            controller=self.controller,
            category_source=self.category_source(),
            concurrency=concurrency,
            rpm=rpm,
        )
        self.set_status("running", stage="PROCESSING")
        self.event(
            "pipeline.start",
            f"Processing {total} chunks · concurrency {concurrency} · {rpm} requests/min"
            + (f" · stop after {self.max_recipes} recipes" if self.max_recipes else ""),
            stage="PROCESSING",
            data={"concurrency": concurrency, "rpm": rpm, "chunks": total, "max_recipes": self.max_recipes},
        )
        self.total_chunks = total
        results = pipeline.run(
            chunks,
            on_progress=self._on_progress,
            on_result=self._on_result,
            on_skip=self._on_skip,
            on_tags=self._on_tags,
            on_tag_failed=self._on_tag_failed,
        )
        run_context.clear()
        if self.max_recipes and len(results) > self.max_recipes:
            results = results[: self.max_recipes]
        return results

    def category_source(self) -> Any:
        from recipeparser.core.ports import CategorySource

        if self.run_provider_offline():

            class _Fixed(CategorySource):
                # The taxonomy the golden recordings were made against (tests/goldens/conftest.py).
                def load_axes(self, user_id: str = "") -> Dict[str, List[str]]:
                    return {
                        "Cuisine": ["American", "British", "French", "Italian"],
                        "Meal Type": ["Breakfast", "Dessert", "Dinner", "Bread"],
                    }

                def load_category_ids(self, user_id: str = "") -> Dict[str, str]:
                    return {}

            return _Fixed()
        from recipeparser.io.category_sources.yaml_source import YamlCategorySource
        from recipeparser.paths import get_categories_file

        yaml_path = get_categories_file()
        return YamlCategorySource(yaml_path=yaml_path if yaml_path.exists() else None)

    # ── pipeline callbacks ───────────────────────────────────────────────────

    def _book_chunk(self, pos: Optional[int]) -> Optional[int]:
        if pos is None or pos >= len(self.chunk_map):
            return None
        return self.chunk_map[pos]

    def _on_stage(self, stage: str) -> None:
        from recipeparser.core import run_context

        chunk, _label, _ = run_context.current()
        first = chunk is not None and chunk not in self.chunk_stage
        if chunk is not None:
            self.chunk_stage[chunk] = stage
        if first:
            self.event(
                "chunk.start",
                f"Chunk {chunk} started (book chunk {self._book_chunk(chunk)}).",
                stage=stage,
                chunk=chunk,
                data={"book_chunk": self._book_chunk(chunk)},
                echo=False,
            )
        self.store.add_event(
            self.run_id, "stage", f"{stage}", level="DEBUG" if chunk is not None else "INFO", stage=stage, chunk=chunk
        )
        self.store.update_run(self.run_id, stage=stage)

    def _on_progress(self, _stage: str, completed: int, total: int) -> None:
        from recipeparser.core import run_context

        chunk, _label, _ = run_context.current()
        if chunk is not None:
            n = self.chunk_recipes.get(chunk, 0)
            skips = self.chunk_skips.get(chunk, [])
            failed = any(explain_reason(s) is not None for s in skips)
            state = "failed" if failed and not n else ("done" if n else "empty")
            if self.controller is not None and self.controller.cancel_requested and chunk not in self.chunk_stage:
                state = "cancelled"
            self.chunk_stage[chunk] = state.upper()
            self.store.add_event(
                self.run_id,
                "chunk.done",
                f"Chunk {chunk}: {n} recipe(s)" + (f", {len(skips)} skipped" if skips else ""),
                level="INFO",
                chunk=chunk,
                data={"state": state, "recipes": n, "skips": skips, "book_chunk": self._book_chunk(chunk)},
            )
        self.store.update_run(self.run_id, chunks_done=completed, chunks_total=total)
        pct = int(completed * 100 / total) if total else 100
        crossed = [m for m in MILESTONES if pct >= m and m not in self.milestones_hit]
        if crossed:
            # A jump past several milestones at once (a small run) reports only the highest.
            self.milestones_hit.update(crossed)
            m = crossed[-1]
            elapsed = time.time() - self.started_pipeline
            eta = elapsed / completed * (total - completed) if completed else 0
            row = self.store.get_run(self.run_id) or {}
            self.event(
                "milestone",
                f"{m}% — {completed}/{total} chunks, {self.recipe_count} recipes, "
                f"{row.get('errors', 0)} errors, {row.get('warnings', 0)} warnings · elapsed {_fmt_secs(elapsed)}"
                + (f" · about {_fmt_secs(eta)} left" if completed < total else ""),
                data={"percent": m, "completed": completed, "total": total, "recipes": self.recipe_count,
                      "elapsed": elapsed, "eta": eta},
            )

    def _on_result(self, recipe: Any) -> None:
        from recipeparser import config
        from recipeparser.core import run_context
        from recipeparser.models import REVIEW_KINDS

        chunk, _label, _ = run_context.current()
        if self.max_recipes and self.recipe_count >= self.max_recipes:
            return
        self.recipe_count += 1
        self.chunk_recipes[chunk if chunk is not None else -1] = (
            self.chunk_recipes.get(chunk if chunk is not None else -1, 0) + 1
        )
        notes = [
            {"kind": n.kind if n.kind in REVIEW_KINDS else "note", "text": n.text}
            for n in getattr(recipe, "review_notes", [])
        ]
        data = recipe.model_dump(exclude={"embedding"})
        row_id = self.store.add_recipe(
            self.run_id,
            chunk=chunk,
            position=self.recipe_count,
            title=recipe.title,
            needs_review=1 if any(n["kind"] == "needs_review" for n in notes) else 0,
            review_json=notes,
            categories_json=list(recipe.categories or []),
            embedding_json=embedding_stats(list(recipe.embedding or []), config.GEMINI_EMBEDDING_MODEL),
            data_json=data,
        )
        self.recipe_rows[id(recipe)] = row_id
        flag = sum(1 for n in notes if n["kind"] == "needs_review")
        self.event(
            "recipe",
            f"Recipe “{recipe.title}”: {len(recipe.structured_ingredients)} ingredients, "
            f"{len(recipe.tokenized_directions)} steps" + (f", {flag} to review" if flag else ""),
            chunk=chunk,
            data={"recipe_id": row_id, "needs_review": flag},
        )
        if self.max_recipes and self.recipe_count >= self.max_recipes and not self.limit_reached:
            self.limit_reached = True
            self.event(
                "limit.reached",
                f"Recipe limit reached ({self.max_recipes}): stopping. Calls in flight finish; their "
                "recipes are not kept.",
                level="WARNING",
            )
            if self.controller is not None:
                self.controller.request_cancel()
                self.set_status("stopping")

    def _on_skip(self, chunk_obj: Any, reason: str, index: int) -> None:
        self.chunk_skips.setdefault(index, []).append(reason)
        self.store.bump_run(self.run_id, skips=1)
        e = explain_reason(reason)
        stage = self.chunk_stage.get(index)
        if e is not None:
            self.record_error(e, stage=stage, chunk=index)
            self.event(
                "chunk.failed",
                f"Chunk {index} failed: {e.code} {e.title} — {e.message[:300]}",
                level="ERROR",
                stage=stage,
                chunk=index,
                data={"reason": reason, "code": e.code},
            )
        else:
            self.store.add_error(
                self.run_id,
                severity="warning",
                code="SKIPPED",
                category="skip",
                title="Recipe dropped by a check",
                message=reason,
                cause="EXTRACT rewrote the ingredient lines instead of copying them verbatim, so the recipe was "
                "dropped rather than stored with altered quantities.",
                fix="Retry this chunk; if it repeats, check an edited extraction prompt or try another model.",
                stage=stage,
                chunk=index,
            )
            self.event("chunk.skip", f"Chunk {index}: {reason}", level="WARNING", stage=stage, chunk=index)

    def _on_tags(self, recipe: Any) -> None:
        row_id = self.recipe_rows.get(id(recipe))
        if row_id is not None:
            self.store.update_recipe(
                row_id,
                categories_json=list(recipe.categories or []),
                data_json=recipe.model_dump(exclude={"embedding"}),
            )

    def _on_tag_failed(self, failure: Any) -> None:
        self.store.add_error(
            self.run_id,
            severity="warning",
            code="TAG_FAILED",
            category="tagging",
            title=f"Tagging failed on axis “{failure.axis}”",
            message=f"{failure.reason} — recipes left untagged on this axis: {', '.join(failure.titles)[:1500]}",
            cause="The TAG call for this axis failed twice for a batch; the recipes are kept without those tags.",
            fix="Check the categorize prompt and the API call errors; re-tag later with the recategorise tool.",
            stage="CATEGORIZING",
        )

    # ── outputs ───────────────────────────────────────────────────────────────

    def write_outputs(self, results: List[Any]) -> Dict[str, Any]:
        from recipeparser.io.writers.cayenne_zip import CayenneZipWriter
        from recipeparser.io.writers.paprika_zip import PaprikaWriter

        out_dir = self.run_dir / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(self.run["book_path"]).stem
        wanted = self.options.get("outputs") or ["paprika", "cayenne", "json"]
        outputs: Dict[str, Any] = {"dir": str(out_dir)}
        if not results:
            self.event("output.skipped", "No recipes to write.", level="WARNING")
            return outputs
        if "paprika" in wanted:
            p = out_dir / f"{stem}.paprikarecipes"
            PaprikaWriter(output_path=p).write(results)
            outputs["paprika"] = str(p)
        if "cayenne" in wanted:
            p = out_dir / f"{stem}.cayenne.zip"
            CayenneZipWriter(output_path=p).write(results)
            outputs["cayenne"] = str(p)
        if "json" in wanted:
            p = out_dir / "recipes.json"
            payload = []
            for r in results:
                d = r.model_dump(exclude={"embedding"})
                d["review_notes"] = [n.model_dump() for n in r.review_notes]
                payload.append(d)
            p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            outputs["json"] = str(p)
        files = {k: v for k, v in outputs.items() if k != "dir"}
        self.event(
            "output.written",
            "Wrote "
            + ", ".join(f"{Path(v).name} ({Path(v).stat().st_size / 1024:.0f} KB)" for v in files.values())
            + f" to {out_dir}",
            data=outputs,
        )
        return outputs

    # ── main ──────────────────────────────────────────────────────────────────

    def execute(self) -> int:
        self.setup_logging()
        self.set_status("starting", started_at=self.started, pid=os.getpid())
        from recipeparser import config

        self.event(
            "run.start",
            f"Run {self.run_id} started: {self.run['book_name']} · provider {self.run['provider']} · "
            f"model {config.GEMINI_MODEL} · embeddings {config.GEMINI_EMBEDDING_MODEL}",
            data={"pid": os.getpid(), "python": sys.version.split()[0], "options": self.options},
        )
        control = threading.Thread(target=self.control_loop, name="control", daemon=True)
        control.start()

        def _term(_signum: int, _frame: Any) -> None:
            self.apply_control("stop")

        signal.signal(signal.SIGTERM, _term)
        exit_code = 0
        results: List[Any] = []
        try:
            self.build_client()
            self.preflight()
            chunks = self.read_book()
            self.chunk_map = select_chunks(len(chunks), self.options)
            selected = [chunks[i] for i in self.chunk_map]
            self.store.update_run(
                self.run_id,
                chunks_in_book=len(chunks),
                chunks_total=len(selected),
                outputs_json={"selected_chunks": self.chunk_map},
            )
            self.event(
                "chunks.selected",
                f"Selected {len(selected)} of {len(chunks)} chunks"
                + (f" (book chunks {self.chunk_map[0]}–{self.chunk_map[-1]})" if self.chunk_map else ""),
                data={"selected": self.chunk_map, "book_chunks": len(chunks)},
            )
            if not selected:
                raise _fatal(
                    "NOTHING_SELECTED",
                    "No chunks selected",
                    "The % of book / chunk selection is empty.",
                    "The run was asked to process 0% of the book.",
                    "Raise '% of book' on the New run page.",
                    category="input",
                )
            self.started_pipeline = time.time()
            results = self.run_pipeline(selected)
            outputs = self.write_outputs(results)
            run = self.store.get_run(self.run_id) or {}
            prev = run.get("outputs") or {}
            self.store.update_run(self.run_id, outputs_json={**prev, **outputs})
            cancelled = bool(self.controller is not None and self.controller.cancel_requested)
            errors = int(run.get("errors") or 0)
            if self.limit_reached:
                status = "limit_reached"
            elif cancelled:
                status = "cancelled"
            elif not results:
                status = "failed"
                self.record_error(
                    Explanation(
                        code="NO_RECIPES",
                        category="outcome",
                        severity="fatal",
                        title="No recipes were produced",
                        message=f"0 recipes from {len(selected)} chunks.",
                        cause="Every chunk failed or contained no recipe. See the other errors for why.",
                        fix="Fix the most frequent error code below, then Restart or Retry failed chunks.",
                    )
                )
                exit_code = 1
            elif errors:
                status = "completed_with_errors"
            else:
                status = "completed"
            self.finish(status, results)
        except RunAborted as aborted:
            e = aborted.explanation
            self.record_error(e, stage=(self.store.get_run(self.run_id) or {}).get("stage"))
            self.event("run.failed", f"{e.code}: {e.title} — {e.message} · Fix: {e.fix}", level="CRITICAL")
            self.finish("failed", results, outcome=f"{e.code}: {e.title}")
            exit_code = 1
        except BaseException as exc:  # noqa: BLE001 — the last line of defence records the crash
            e = explain(exc)
            e.severity = "fatal"
            self.record_error(e)
            self.event(
                "run.failed",
                f"Unexpected {type(exc).__name__}: {exc}",
                level="CRITICAL",
                data={"traceback": e.traceback},
            )
            log.critical("Run failed with an unexpected error", exc_info=True)
            self.finish("failed", results, outcome=f"{e.code}: {e.title}")
            exit_code = 1
        finally:
            self.stop_flag.set()
        return exit_code

    def finish(self, status: str, results: List[Any], outcome: Optional[str] = None) -> None:
        elapsed = time.time() - self.started
        run = self.store.get_run(self.run_id) or {}
        codes = self.store.query(
            "SELECT code, COUNT(*) AS n FROM errors WHERE run_id = ? GROUP BY code ORDER BY n DESC", (self.run_id,)
        )
        code_text = ", ".join(f"{c['code']} ×{c['n']}" for c in codes[:5])
        if outcome is None:
            outcome = (
                f"{len(results)} recipes from {run.get('chunks_done', 0)}/{run.get('chunks_total', 0)} chunks "
                f"in {_fmt_secs(elapsed)}"
            )
            if run.get("errors"):
                outcome += f" · {run['errors']} errors"
            if run.get("warnings"):
                outcome += f" · {run['warnings']} warnings"
            if code_text:
                outcome += f" ({code_text})"
        self.set_status(
            status, ended_at=time.time(), outcome=outcome, stage=None, exit_code=0 if status not in ("failed",) else 1
        )
        dropped = getattr(getattr(self, "store_handler", None), "dropped", 0)
        if dropped:
            self.store.add_error(
                self.run_id, severity="warning", code="LOG_DROPPED", category="logging",
                title="Some log lines were not recorded in the database",
                message=f"{dropped} log line(s) could not be written to the database during this run.",
                cause="The database was busy or unavailable for those writes.",
                fix="Read the complete log in the Raw log tab (run.log); lower concurrency if it repeats.",
            )
        level = {
            "completed": "INFO",
            "completed_with_errors": "WARNING",
            "limit_reached": "INFO",
            "cancelled": "WARNING",
        }.get(status, "CRITICAL")
        self.event(
            "run.finished",
            f"Run {status.replace('_', ' ')}: {outcome}",
            level=level,
            data={
                "status": status,
                "elapsed": elapsed,
                "recipes": len(results),
                "calls": self.calls,
                "failed_calls": self.failed_calls,
            },
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m recipeparser.adapters.webapp.runner")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)
    store = Store(data_dir / "webapp.db")
    return Runner(store, args.run_id, data_dir).execute()


if __name__ == "__main__":
    sys.exit(main())
