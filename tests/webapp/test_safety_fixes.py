"""Kill never signals a stale pid; pause/resume logs no false warnings; lost log lines are reported."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time

from recipeparser.adapters.webapp import manager as manager_mod
from recipeparser.adapters.webapp.instrument import StoreLogHandler
from recipeparser.adapters.webapp.manager import RunManager
from recipeparser.adapters.webapp.store import Store
from recipeparser.core.fsm import PipelineController, PipelineStatus


def test_a_live_pid_that_is_not_the_runner_is_not_treated_as_the_run(tmp_path):
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert manager_mod._pid_alive(bystander.pid)
        assert not manager_mod._runner_alive(bystander.pid, "some-run")
    finally:
        bystander.kill()
        bystander.wait()


def test_kill_does_not_signal_a_stale_pid_that_now_belongs_to_another_process(tmp_path):
    store = Store(tmp_path / "webapp.db")
    mgr = RunManager(store, tmp_path, tmp_path / ".env")
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        run_id = store.create_run(book_path="x.epub", book_name="x.epub", provider="replay")
        store.update_run(run_id, status="running", pid=bystander.pid)
        run = mgr.kill(run_id)
        assert run["status"] == "killed"
        time.sleep(0.2)
        assert bystander.poll() is None, "the unrelated process was killed"
    finally:
        bystander.kill()
        bystander.wait()


def test_reconcile_marks_a_run_with_a_reused_pid_crashed(tmp_path):
    store = Store(tmp_path / "webapp.db")
    run_id = store.create_run(book_path="x.epub", book_name="x.epub", provider="replay")
    store.update_run(run_id, status="running", pid=os.getpid())  # alive, but not a runner
    RunManager(store, tmp_path, tmp_path / ".env").reconcile()
    assert store.get_run(run_id)["status"] == "crashed"


def test_pause_and_resume_with_blocked_workers_log_no_invalid_transition(caplog):
    c = PipelineController()
    c.transition("start")
    caplog.set_level(logging.DEBUG, logger="recipeparser.core.fsm")
    assert c.request_pause()
    results = []
    workers = [threading.Thread(target=lambda: results.append(c.wait_if_paused())) for _ in range(3)]
    for w in workers:
        w.start()
    time.sleep(0.1)
    assert c.status == PipelineStatus.PAUSED
    main = threading.Thread(target=lambda: results.append(c.check_pause_point()))
    main.start()
    time.sleep(0.1)
    assert c.request_resume()
    for t in [*workers, main]:
        t.join(2)
    assert results == [True, True, True, True]
    assert c.status == PipelineStatus.RUNNING
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], [r.getMessage() for r in caplog.records]


def test_a_log_line_the_database_refuses_is_counted_and_reported(tmp_path, capsys):
    store = Store(tmp_path / "webapp.db")

    def broken(*_a, **_k):
        raise RuntimeError("database is locked")

    store.add_event = broken  # type: ignore[method-assign]
    handler = StoreLogHandler(store, "run-1")
    logger = logging.getLogger("recipeparser.test_drop")
    logger.addHandler(handler)
    try:
        logger.warning("one")
        logger.warning("two")
    finally:
        logger.removeHandler(handler)
    assert handler.dropped == 2
    err = capsys.readouterr().err
    assert "could not record a log line" in err and "database is locked" in err
