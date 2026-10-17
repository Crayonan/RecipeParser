"""
recipeparser/adapters/webapp/manager.py — start, control, watch and end runs.

Each run is a child process (``recipeparser.adapters.webapp.runner``) in its own process
group. The manager:

* resolves the run's settings and prompt overrides, records them on the run and
  hands them to the child as its environment;
* passes Pause / Resume / Stop to the child through the run's ``control`` column
  (the child applies them at the pipeline's cooperative points);
* Kills by signalling the whole process group with SIGKILL — immediate, and it
  cannot be blocked by a stuck call;
* watches every child: an exit the child did not record itself (a crash, an
  out-of-memory kill, a signal) becomes status ``crashed`` with an explained
  error, the exit code and the tail of the run's log; a child that stops
  sending heartbeats is reported as unresponsive;
* on startup, marks runs left active by a previous server whose process is gone.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from recipeparser import providers
from recipeparser.adapters.webapp import settings as settings_mod
from recipeparser.adapters.webapp.store import ACTIVE_STATUSES, Store
from recipeparser.prompts import PROMPT_OVERRIDES_ENV

log = logging.getLogger("recipeparser.adapters.webapp.manager")

REPO_ROOT = Path(__file__).resolve().parents[3]
MAX_RPM = 500
MAX_CONCURRENCY = 256
HEARTBEAT_STALE_SECS = 30


class RunError(ValueError):
    """A run cannot be started or controlled; the message is shown to the user."""


def _signal_name(num: int) -> str:
    try:
        return signal.Signals(num).name
    except ValueError:
        return f"signal {num}"


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _runner_alive(pid: Optional[int], run_id: str) -> bool:
    """True only when *pid* is alive AND is this run's runner process.

    A pid stored in the database outlives its process: after a reboot or a long
    gap it can belong to something unrelated. Its command line must name the
    runner module and this run id before the app treats it as the run, let alone
    signals it. Where /proc is unavailable the pid cannot be verified and is
    treated as gone.
    """
    if not _pid_alive(pid):
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return False
    return "recipeparser.adapters.webapp.runner" in cmd and run_id in cmd


def _tail(path: Path, lines: int = 40) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-lines:])
    except OSError:
        return ""


class RunManager:
    def __init__(self, store: Store, data_dir: Path, env_file: Path) -> None:
        self.store = store
        self.data_dir = Path(data_dir)
        self.env_file = Path(env_file)
        self.procs: Dict[str, "subprocess.Popen[bytes]"] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._stale_reported: Set[str] = set()
        self._monitor: Optional[threading.Thread] = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self.reconcile()
        self._monitor = threading.Thread(target=self._watch, name="run-monitor", daemon=True)
        self._monitor.start()

    def shutdown(self) -> None:
        self._stop.set()

    def reconcile(self) -> None:
        """Runs a previous server left active, whose process is gone, crashed."""
        for run in self.store.query(
            f"SELECT id, pid, status FROM runs WHERE status IN ({','.join('?' * len(ACTIVE_STATUSES))})",
            ACTIVE_STATUSES,
        ):
            if run["id"] in self.procs:
                continue
            if not _runner_alive(run["pid"], run["id"]):
                self._mark_crashed(
                    run["id"],
                    None,
                    "The run's process is gone: the server restarted, or the process "
                    "was ended outside the app, while the run was active.",
                )
            else:
                log.warning(
                    "Run %s (pid %s) is still running from a previous server; watching it by pid.",
                    run["id"],
                    run["pid"],
                )

    # ── starting ─────────────────────────────────────────────────────────────

    def validate_options(self, options: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(options)

        def num(key: str, lo: float, hi: float, default: Any, integer: bool = True) -> None:
            raw = out.get(key, default)
            if raw in (None, ""):
                out[key] = default
                return
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise RunError(f"{key} must be a number.") from None
            if integer and value != int(value):
                raise RunError(f"{key} must be a whole number.")
            if not lo <= value <= hi:
                raise RunError(f"{key} must be between {lo:g} and {hi:g} (got {value:g}).")
            out[key] = int(value) if integer else value

        num("concurrency", 1, MAX_CONCURRENCY, 16)
        num("rpm", 1, MAX_RPM, MAX_RPM)
        num("max_recipes", 0, 1_000_000, 0)
        num("book_percent", 0, 100, 100, integer=False)
        num("start_percent", 0, 100, 0, integer=False)
        if out["start_percent"] + out["book_percent"] > 100.0001:
            raise RunError("start % + % of book cannot exceed 100.")
        if out["book_percent"] == 0 and not out.get("chunk_indices"):
            raise RunError("% of book is 0: the run would process nothing.")
        outputs = out.get("outputs") or ["paprika", "cayenne", "json"]
        bad = set(outputs) - {"paprika", "cayenne", "json"}
        if bad:
            raise RunError(
                f"Unknown output(s): {', '.join(sorted(bad))}. Runs write paprika, cayenne and json "
                "locally; Supabase is not available from the web app yet."
            )
        out["outputs"] = list(outputs)
        if out.get("write_supabase"):
            raise RunError("Supabase writes are disabled in the web app: runs are local only.")
        idx = out.get("chunk_indices")
        if idx is not None:
            try:
                out["chunk_indices"] = sorted({int(i) for i in idx})
            except (TypeError, ValueError):
                raise RunError("chunk_indices must be a list of chunk numbers.") from None
        overrides = out.get("settings_overrides") or {}
        for key, value in overrides.items():
            problem = settings_mod.validate(key, value)
            if problem:
                raise RunError(problem)
            if key in settings_mod.SECRET_KEYS:
                raise RunError(f"{key} is a secret: set it in Settings → API keys, not per run.")
        out["settings_overrides"] = {k: v for k, v in overrides.items() if v not in (None, "")}
        return out

    def start_run(
        self,
        *,
        book_path: str,
        provider: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
        label: Optional[str] = None,
        parent_run_id: Optional[str] = None,
    ) -> str:
        book = Path(book_path).expanduser()
        if not book.is_file():
            raise RunError(f"Book not found: {book}")
        if book.suffix.lower() not in (".epub", ".pdf", ".paprikarecipes"):
            raise RunError(f"Unsupported file type {book.suffix!r}: choose an .epub, .pdf or .paprikarecipes file.")
        opts = self.validate_options(options or {})
        saved = self.store.saved_settings()
        env_file_values = settings_mod.read_env_file(self.env_file)
        provider_id = (
            provider or saved.get("RP_PROVIDER") or os.environ.get("RP_PROVIDER") or providers.DEFAULT_PROVIDER
        )
        try:
            prov = providers.get_provider(provider_id)
        except ValueError as exc:
            raise RunError(str(exc)) from None
        run_overrides = dict(opts["settings_overrides"])
        if prov.offline and book.suffix.lower() == ".pdf":
            # The recordings for text-pages.pdf are keyed to its page chunks (tests/goldens/conftest.py).
            run_overrides.setdefault("RP_MAX_CHUNK_CHARS", "0")
        resolved = settings_mod.resolve(saved, env_file_values, run_overrides)
        gen_model = resolved["GEMINI_MODEL"][0] or ""
        embed_model = resolved["GEMINI_EMBEDDING_MODEL"][0] or ""
        verdict, message = providers.check_embedding_model(provider_id, embed_model)
        if verdict == "rejected":
            raise RunError(message)
        if not prov.offline and not resolved.get(prov.api_key_env, (None, ""))[0]:
            raise RunError(
                f"No {prov.api_key_env} is set. Add it in Settings → API keys, or choose the offline replay provider."
            )
        prompt_overrides = self.store.prompt_overrides()
        run_id = self.store.create_run(
            label=label or None,
            book_path=str(book.resolve()),
            book_name=book.name,
            provider=provider_id,
            gen_model=gen_model,
            embed_model=embed_model,
            options_json=opts,
            settings_json=settings_mod.snapshot(resolved),
            prompts_json=prompt_overrides,
            parent_run_id=parent_run_id,
        )
        run_dir = self.data_dir / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        prompts_file = run_dir / "prompts.json"
        prompts_file.write_text(json.dumps(prompt_overrides, ensure_ascii=False, indent=2), encoding="utf-8")
        env = settings_mod.child_env(resolved)
        env[PROMPT_OVERRIDES_ENV] = str(prompts_file)
        env["RP_PROVIDER"] = provider_id
        env["PYTHONUNBUFFERED"] = "1"
        env.pop("PYTEST_CURRENT_TEST", None)
        self.store.add_event(
            run_id,
            "run.created",
            f"Run created for {book.name} with {prov.label}"
            + (f" · {len(prompt_overrides)} edited prompt(s)" if prompt_overrides else "")
            + (f" · restart of {parent_run_id}" if parent_run_id else ""),
            data={"options": opts, "prompts": sorted(prompt_overrides)},
        )
        try:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "recipeparser.adapters.webapp.runner",
                    "--run-id",
                    run_id,
                    "--data-dir",
                    str(self.data_dir),
                ],
                env=env,
                cwd=str(REPO_ROOT),
                start_new_session=True,
            )
        except OSError as exc:
            self.store.update_run(
                run_id, status="failed", ended_at=time.time(), outcome=f"Could not start the run process: {exc}"
            )
            self.store.add_error(
                run_id,
                severity="fatal",
                code="SPAWN",
                category="crash",
                title="Could not start the run process",
                message=str(exc),
                cause="The operating system refused to start a Python process.",
                fix="Check disk space, process limits and the Python installation.",
            )
            raise RunError(f"Could not start the run process: {exc}") from exc
        with self._lock:
            self.procs[run_id] = proc
        self.store.update_run(run_id, pid=proc.pid, status="starting")
        log.info(
            "Run %s started (pid %d): %s, provider %s, model %s", run_id, proc.pid, book.name, provider_id, gen_model
        )
        return run_id

    # ── control ──────────────────────────────────────────────────────────────

    def _active_run(self, run_id: str) -> Dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise RunError(f"No run {run_id}.")
        return run

    def control(self, run_id: str, command: str) -> Dict[str, Any]:
        if command not in ("pause", "resume", "stop"):
            raise RunError(f"Unknown command {command!r}.")
        run = self._active_run(run_id)
        if run["status"] not in ACTIVE_STATUSES:
            raise RunError(f"The run is {run['status']}; {command} only applies to an active run.")
        if command == "pause" and run["status"] in ("paused", "pausing"):
            raise RunError("The run is already paused.")
        if command == "resume" and run["status"] not in ("paused", "pausing"):
            raise RunError("The run is not paused.")
        self.store.set_control(run_id, command)
        self.store.add_event(
            run_id, "control.requested", f"{command.capitalize()} requested from the web app.", level="WARNING"
        )
        log.warning("Run %s: %s requested", run_id, command)
        return self.store.get_run(run_id) or {}

    def kill(self, run_id: str) -> Dict[str, Any]:
        run = self._active_run(run_id)
        pid = run.get("pid")
        if run["status"] not in ACTIVE_STATUSES:
            raise RunError(f"The run is {run['status']}: there is nothing to kill.")
        killed = False
        with self._lock:
            owned = self.procs.get(run_id)
        # Signal only a process this server started, or one verified to be this run's runner:
        # a stale pid from the database may now belong to an unrelated process.
        target = owned.pid if owned is not None else (pid if _runner_alive(pid, run_id) else None)
        if target:
            try:
                os.killpg(os.getpgid(target), signal.SIGKILL)
                killed = True
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    os.kill(target, signal.SIGKILL)
                    killed = True
                except OSError:
                    pass
        elif pid:
            log.warning("Run %s: stored pid %s is not this run's runner any more; not signalling it.", run_id, pid)
        with self._lock:
            proc = self.procs.pop(run_id, None)
        if proc is not None:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        pct = int((run.get("chunks_done") or 0) * 100 / run["chunks_total"]) if run.get("chunks_total") else 0
        outcome = (
            f"Killed at {pct}% ({run.get('chunks_done') or 0}/{run.get('chunks_total') or 0} chunks, "
            f"{run.get('recipes_done') or 0} recipes recorded; no output archive was written)."
        )
        self.store.update_run(
            run_id, status="killed", ended_at=time.time(), outcome=outcome, control=None, exit_code=-9, stage=None
        )
        self.store.add_event(
            run_id,
            "run.killed",
            outcome + (" Process group sent SIGKILL." if killed else " The process was already gone."),
            level="CRITICAL",
        )
        log.critical("Run %s killed (pid %s)", run_id, pid)
        return self.store.get_run(run_id) or {}

    def restart(self, run_id: str, mode: str = "same", overrides: Optional[Dict[str, Any]] = None) -> str:
        """A new run of the same book and options: everything (same), failed chunks only, or what was not done."""
        run = self._active_run(run_id)
        if run["status"] in ACTIVE_STATUSES:
            if mode != "same":
                raise RunError("Stop or kill the run before retrying parts of it.")
            self.kill(run_id)
            run = self.store.get_run(run_id) or run
        options = dict(run.get("options") or {})
        options.pop("chunk_indices", None)
        if mode in ("failed", "remaining"):
            selected = (run.get("outputs") or {}).get("selected_chunks") or []
            chunks = self.failed_chunks(run_id) if mode == "failed" else self.remaining_chunks(run_id)
            if not chunks:
                raise RunError(
                    "There are no failed chunks to retry."
                    if mode == "failed"
                    else "Every selected chunk was processed: nothing remains."
                )
            options["chunk_indices"] = [selected[c] if c < len(selected) else c for c in chunks]
        elif mode != "same":
            raise RunError(f"Unknown restart mode {mode!r}.")
        if overrides:
            options.update(overrides)
        label = {"same": "Restart", "failed": "Retry failed chunks", "remaining": "Continue remaining"}[mode]
        new_id = self.start_run(
            book_path=run["book_path"],
            provider=run["provider"],
            options=options,
            label=f"{label} of {run.get('label') or run_id}",
            parent_run_id=run_id,
        )
        self.store.add_event(run_id, "run.restarted", f"{label}: new run {new_id}.", data={"new_run": new_id})
        return new_id

    def failed_chunks(self, run_id: str) -> List[int]:
        rows = self.store.query(
            "SELECT DISTINCT chunk FROM events WHERE run_id = ? AND chunk IS NOT NULL AND "
            "(kind = 'chunk.failed' OR (kind = 'chunk.done' AND data_json LIKE '%\"state\": \"failed\"%'))",
            (run_id,),
        )
        return sorted(r["chunk"] for r in rows)

    def remaining_chunks(self, run_id: str) -> List[int]:
        run = self.store.get_run(run_id) or {}
        total = run.get("chunks_total") or 0
        done = {
            r["chunk"]
            for r in self.store.query(
                "SELECT DISTINCT chunk FROM events WHERE run_id = ? AND kind = 'chunk.done' AND chunk IS NOT NULL",
                (run_id,),
            )
        }
        return [i for i in range(total) if i not in done]

    # ── watching ─────────────────────────────────────────────────────────────

    def _watch(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                log.exception("Run monitor error")
            self._stop.wait(0.5)

    def poll_once(self) -> None:
        with self._lock:
            items = list(self.procs.items())
        for run_id, proc in items:
            rc = proc.poll()
            if rc is None:
                self._check_heartbeat(run_id)
                continue
            with self._lock:
                self.procs.pop(run_id, None)
            run = self.store.get_run(run_id) or {}
            if run.get("status") in ACTIVE_STATUSES:
                self._mark_crashed(run_id, rc, None)
            else:
                self.store.update_run(run_id, exit_code=rc)
                log.info("Run %s process exited (%d): %s", run_id, rc, run.get("status"))
        # Runs not started by this server (left over from a previous one).
        for run in self.store.query(
            f"SELECT id, pid FROM runs WHERE status IN ({','.join('?' * len(ACTIVE_STATUSES))})", ACTIVE_STATUSES
        ):
            if run["id"] in self.procs:
                continue
            if not _runner_alive(run["pid"], run["id"]):
                time.sleep(0.2)  # a child that just finished may be writing its final status
                fresh = self.store.get_run(run["id"]) or {}
                if fresh.get("status") in ACTIVE_STATUSES and run["id"] not in self.procs:
                    self._mark_crashed(run["id"], None, "The run's process is gone.")

    def _check_heartbeat(self, run_id: str) -> None:
        run = self.store.get_run(run_id) or {}
        beat = run.get("heartbeat_at")
        if beat and time.time() - beat > HEARTBEAT_STALE_SECS and run_id not in self._stale_reported:
            self._stale_reported.add(run_id)
            self.store.add_event(
                run_id,
                "run.unresponsive",
                f"No heartbeat for {int(time.time() - beat)}s: the run process is alive but not updating. "
                "It may be stuck in a long call; Kill ends it.",
                level="ERROR",
            )
            log.error("Run %s unresponsive (no heartbeat for %ds)", run_id, int(time.time() - beat))
        elif beat and time.time() - beat <= HEARTBEAT_STALE_SECS and run_id in self._stale_reported:
            self._stale_reported.discard(run_id)
            self.store.add_event(run_id, "run.responsive", "Heartbeat is back.")

    def _mark_crashed(self, run_id: str, rc: Optional[int], reason: Optional[str]) -> None:
        run = self.store.get_run(run_id) or {}
        run_dir = self.data_dir / "runs" / run_id
        crash = _tail(run_dir / "crash.log", 60).strip()
        log_tail = _tail(run_dir / "run.log", 40)
        if rc is not None and rc < 0:
            how = f"killed by {_signal_name(-rc)}"
            cause = {
                "SIGKILL": "SIGKILL usually means the operating system's out-of-memory killer, or a kill from "
                "outside the app.",
                "SIGSEGV": "A segmentation fault inside a native library (PDF or image decoding).",
                "SIGTERM": "Something outside the app asked the process to stop.",
            }.get(_signal_name(-rc), "The process received a signal it does not handle.")
        elif rc is not None:
            how = f"exited with code {rc} without recording an outcome"
            cause = "The process ended before it could record what happened (an error during startup or logging)."
        else:
            how = "disappeared"
            cause = reason or "The process ended without recording an outcome."
        message = f"The run process {how}."
        self.store.add_error(
            run_id,
            severity="fatal",
            code=f"EXIT {rc}" if rc is not None else "CRASH",
            category="crash",
            title="Run crashed",
            message=message,
            cause=cause,
            fix="Read the crash log and the last log lines below. Lower concurrency or chunk size if memory ran "
            "out, then Restart (or Continue remaining).",
            stage=run.get("stage"),
            traceback=(crash + "\n\n--- last log lines ---\n" + log_tail).strip() or None,
        )
        self.store.update_run(
            run_id, status="crashed", ended_at=time.time(), exit_code=rc, stage=None, outcome=f"Crashed: {message}"
        )
        self.store.add_event(
            run_id,
            "run.crashed",
            f"Run crashed: {message} {cause}",
            level="CRITICAL",
            data={"exit_code": rc, "crash_log": crash[-4000:], "log_tail": log_tail[-4000:]},
        )
        log.critical("Run %s crashed: %s", run_id, message)
