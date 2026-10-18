from pathlib import Path

import pytest

from recipeparser.adapters.webapp.manager import REPO_ROOT, RunError, RunManager
from recipeparser.adapters.webapp.runner import Runner, select_chunks
from recipeparser.adapters.webapp.store import Store

DUAL_UNITS_EPUB = str(REPO_ROOT / "tests" / "goldens" / "corpus" / "dual-units.epub")


def test_select_chunks_percentages_and_rounding():
    # Full range
    assert select_chunks(10, {}) == list(range(10))
    assert select_chunks(10, {"start_percent": 0, "book_percent": 100}) == list(range(10))

    # Half
    assert select_chunks(10, {"start_percent": 50, "book_percent": 50}) == [5, 6, 7, 8, 9]

    # Rounding: floor(10 * 25 / 100) = 2; ceil(10 * 25 / 100) = 3 -> range(2, 5) -> [2, 3, 4]
    assert select_chunks(10, {"start_percent": 25, "book_percent": 25}) == [2, 3, 4]

    # Edges
    assert select_chunks(10, {"start_percent": 0, "book_percent": 10}) == [0]
    assert select_chunks(10, {"start_percent": 90, "book_percent": 10}) == [9]

    # Explicit indices filter bounds and sort
    assert select_chunks(10, {"chunk_indices": [5, 2, 99, -1, 2]}) == [2, 5]


def test_validate_options_percentages():
    store = Store(Path("/tmp/nonexistent_store.db"))
    mgr = RunManager(store, Path("/tmp"), Path("/tmp/.env"))

    # Accepts normal percentages
    opts = mgr.validate_options({"start_percent": 20, "book_percent": 80})
    assert opts["start_percent"] == 20
    assert opts["book_percent"] == 80

    # Rejects sum > 100
    with pytest.raises(RunError, match="start % \\+ % of book cannot exceed 100"):
        mgr.validate_options({"start_percent": 60, "book_percent": 50})

    # Rejects book_percent = 0 without explicit chunks
    with pytest.raises(RunError, match="% of book is 0"):
        mgr.validate_options({"book_percent": 0})

    # Allows book_percent = 0 with explicit chunks
    opts_with_chunks = mgr.validate_options({"book_percent": 0, "chunk_indices": [1, 2]})
    assert opts_with_chunks["chunk_indices"] == [1, 2]


def test_validate_options_rpm_and_concurrency():
    store = Store(Path("/tmp/nonexistent_store.db"))
    mgr = RunManager(store, Path("/tmp"), Path("/tmp/.env"))

    # Boundary values accepted
    opts = mgr.validate_options({"rpm": 500, "concurrency": 256})
    assert opts["rpm"] == 500
    assert opts["concurrency"] == 256

    opts_min = mgr.validate_options({"rpm": 1, "concurrency": 1})
    assert opts_min["rpm"] == 1
    assert opts_min["concurrency"] == 1

    # Rejected rpm
    with pytest.raises(RunError, match="rpm must be between 1 and 500"):
        mgr.validate_options({"rpm": 501})
    with pytest.raises(RunError, match="rpm must be between 1 and 500"):
        mgr.validate_options({"rpm": 0})

    # Rejected concurrency
    with pytest.raises(RunError, match="concurrency must be between 1 and 256"):
        mgr.validate_options({"concurrency": 257})
    with pytest.raises(RunError, match="concurrency must be between 1 and 256"):
        mgr.validate_options({"concurrency": 0})


def test_max_recipes_limit_reached(tmp_path, monkeypatch):
    monkeypatch.setenv("RP_PROVIDER", "replay")
    monkeypatch.setenv("RP_REPLAY_LATENCY_MS", "0")

    store = Store(tmp_path / "webapp.db")
    run_id = store.create_run(
        book_path=DUAL_UNITS_EPUB,
        book_name="dual-units.epub",
        provider="replay",
        options_json={
            "max_recipes": 1,
            "concurrency": 4,
            "rpm": 300,
            "outputs": ["paprika", "cayenne", "json"],
        },
        settings_json={},
    )

    runner = Runner(store, run_id, tmp_path)
    exit_code = runner.execute()
    assert exit_code == 0

    run = store.get_run(run_id)
    assert run is not None
    assert run["status"] == "limit_reached"

    # Exactly 1 recipe stored
    recipes = store.query("SELECT id, title FROM recipes WHERE run_id = ?", (run_id,))
    assert len(recipes) == 1

    # Limit reached event exists
    limit_ev = store.query("SELECT * FROM events WHERE run_id = ? AND kind = 'limit.reached'", (run_id,))
    assert len(limit_ev) == 1

    # Check runner passed concurrency and rpm to pipeline.start event
    pipe_start = store.one("SELECT data_json FROM events WHERE run_id = ? AND kind = 'pipeline.start'", (run_id,))
    assert pipe_start is not None
    import json

    data = json.loads(pipe_start["data_json"])
    assert data["concurrency"] == 4
    assert data["rpm"] == 300
    assert data["max_recipes"] == 1
