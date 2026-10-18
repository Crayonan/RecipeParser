import time

from recipeparser.adapters.webapp.manager import REPO_ROOT, RunManager, _pid_alive
from recipeparser.adapters.webapp.store import Store

GUTENBERG_EPUB = str(REPO_ROOT / "tests" / "goldens" / "corpus" / "gutenberg-multi.epub")


def test_subprocess_pause_resume_stop(tmp_path):
    store = Store(tmp_path / "webapp.db")
    env_file = tmp_path / ".env"
    env_file.touch()
    mgr = RunManager(store, tmp_path, env_file)
    mgr.start()
    try:
        run_id = mgr.start_run(
            book_path=GUTENBERG_EPUB,
            provider="replay",
            options={
                "concurrency": 2,
                "settings_overrides": {"RP_REPLAY_LATENCY_MS": "300"},
            },
        )
        assert run_id

        # 1. Wait until running
        t0 = time.time()
        run = None
        while time.time() - t0 < 10:
            run = store.get_run(run_id)
            if run and run["status"] == "running":
                break
            time.sleep(0.1)
        assert run is not None and run["status"] == "running"

        # Wait until at least 1 api_call is recorded
        t0 = time.time()
        calls = []
        while time.time() - t0 < 10:
            calls = store.query("SELECT id FROM api_calls WHERE run_id = ?", (run_id,))
            if calls:
                break
            time.sleep(0.1)
        assert len(calls) > 0

        # 2. Pause
        mgr.control(run_id, "pause")
        t0 = time.time()
        while time.time() - t0 < 10:
            run = store.get_run(run_id)
            if run and run["status"] == "paused":
                break
            time.sleep(0.1)
        assert run is not None and run["status"] == "paused"

        # Assert no new api calls during pause
        call_count_1 = len(store.query("SELECT id FROM api_calls WHERE run_id = ?", (run_id,)))
        time.sleep(0.6)
        call_count_2 = len(store.query("SELECT id FROM api_calls WHERE run_id = ?", (run_id,)))
        assert call_count_1 == call_count_2

        # 3. Resume
        mgr.control(run_id, "resume")
        t0 = time.time()
        while time.time() - t0 < 10:
            run = store.get_run(run_id)
            if run and run["status"] == "running":
                break
            time.sleep(0.1)
        assert run is not None and run["status"] == "running"

        # 4. Stop
        mgr.control(run_id, "stop")
        t0 = time.time()
        while time.time() - t0 < 10:
            run = store.get_run(run_id)
            if run and run["status"] == "cancelled":
                break
            time.sleep(0.1)
        assert run is not None and run["status"] == "cancelled"
    finally:
        mgr.shutdown()


def test_subprocess_kill(tmp_path):
    store = Store(tmp_path / "webapp.db")
    env_file = tmp_path / ".env"
    env_file.touch()
    mgr = RunManager(store, tmp_path, env_file)
    mgr.start()
    try:
        run_id = mgr.start_run(
            book_path=GUTENBERG_EPUB,
            provider="replay",
            options={
                "concurrency": 2,
                "settings_overrides": {"RP_REPLAY_LATENCY_MS": "300"},
            },
        )
        t0 = time.time()
        run = None
        while time.time() - t0 < 10:
            run = store.get_run(run_id)
            if run and run["status"] == "running":
                break
            time.sleep(0.1)
        assert run is not None and run["status"] == "running"
        pid = run.get("pid")
        assert pid is not None
        assert _pid_alive(pid)

        # Kill
        res = mgr.kill(run_id)
        assert res["status"] == "killed"

        run = store.get_run(run_id)
        assert run is not None
        assert run["status"] == "killed"
        assert run["exit_code"] == -9

        # Process must be gone
        time.sleep(0.2)
        assert not _pid_alive(pid)
    finally:
        mgr.shutdown()


def test_manager_restart_modes(tmp_path):
    store = Store(tmp_path / "webapp.db")
    env_file = tmp_path / ".env"
    env_file.touch()
    mgr = RunManager(store, tmp_path, env_file)

    # Create a completed/stopped parent run with events
    parent_id = store.create_run(
        book_path=GUTENBERG_EPUB,
        book_name="gutenberg-multi.epub",
        provider="replay",
        options_json={"concurrency": 2},
        settings_json={},
    )
    store.update_run(
        parent_id,
        status="cancelled",
        chunks_total=5,
        chunks_done=2,
        outputs_json={"selected_chunks": [10, 11, 12, 13, 14]},
    )

    # Chunk 0 done
    store.add_event(parent_id, "chunk.done", "Chunk 0 done", chunk=0, data={"state": "done"})
    # Chunk 1 failed
    store.add_event(parent_id, "chunk.failed", "Chunk 1 failed", chunk=1)
    # Chunk 2 done with state failed
    store.add_event(parent_id, "chunk.done", "Chunk 2 failed", chunk=2, data={"state": "failed"})
    # Chunks 3, 4 not reached

    assert mgr.failed_chunks(parent_id) == [1, 2]
    assert mgr.remaining_chunks(parent_id) == [1, 3, 4]

    # Mode 'same'
    same_id = mgr.restart(parent_id, mode="same")
    same_run = store.get_run(same_id)
    assert same_run is not None
    assert same_run["parent_run_id"] == parent_id
    assert "chunk_indices" not in same_run["options"]

    # Mode 'failed': mapped via selected_chunks [10, 11, 12, 13, 14] -> indices 1 and 2 map to [11, 12]
    failed_id = mgr.restart(parent_id, mode="failed")
    failed_run = store.get_run(failed_id)
    assert failed_run is not None
    assert failed_run["parent_run_id"] == parent_id
    assert failed_run["options"]["chunk_indices"] == [11, 12]

    # Mode 'remaining': chunks 1, 3, 4 map to [11, 13, 14]
    remaining_id = mgr.restart(parent_id, mode="remaining")
    remaining_run = store.get_run(remaining_id)
    assert remaining_run is not None
    assert remaining_run["parent_run_id"] == parent_id
    assert remaining_run["options"]["chunk_indices"] == [11, 13, 14]

    # Clean up spawned child procs
    for cid in (same_id, failed_id, remaining_id):
        try:
            mgr.kill(cid)
        except Exception:
            pass
