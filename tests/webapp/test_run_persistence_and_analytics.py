from fastapi.testclient import TestClient

from recipeparser.adapters.webapp import analytics
from recipeparser.adapters.webapp.manager import REPO_ROOT
from recipeparser.adapters.webapp.runner import Runner
from recipeparser.adapters.webapp.server import create_app
from recipeparser.adapters.webapp.store import Store

DUAL_UNITS_EPUB = str(REPO_ROOT / "tests" / "goldens" / "corpus" / "dual-units.epub")


def test_replay_run_persistence_and_analytics(tmp_path, monkeypatch):
    monkeypatch.setenv("RP_PROVIDER", "replay")
    monkeypatch.setenv("RP_REPLAY_LATENCY_MS", "0")

    store = Store(tmp_path / "webapp.db")
    run_id1 = store.create_run(
        book_path=DUAL_UNITS_EPUB,
        book_name="dual-units.epub",
        provider="replay",
        options_json={"outputs": ["paprika", "cayenne", "json"]},
        settings_json={"GEMINI_MODEL": {"value": "gemini-3.1-flash-lite", "source": "default"}},
    )

    runner1 = Runner(store, run_id1, tmp_path)
    exit_code1 = runner1.execute()
    assert exit_code1 == 0

    run1 = store.get_run(run_id1)
    assert run1 is not None
    assert run1["status"] == "completed"

    # Verify outputs written
    out_dir = tmp_path / "runs" / run_id1 / "output"
    assert (out_dir / "dual-units.paprikarecipes").is_file()
    assert (out_dir / "dual-units.cayenne.zip").is_file()
    assert (out_dir / "recipes.json").is_file()

    # Verify events
    events = store.query("SELECT kind FROM events WHERE run_id = ?", (run_id1,))
    event_kinds = {e["kind"] for e in events}
    required_events = {
        "run.start",
        "book.read",
        "chunks.selected",
        "pipeline.start",
        "recipe",
        "chunk.done",
        "milestone",
        "output.written",
        "run.finished",
    }
    assert required_events.issubset(event_kinds), f"Missing events: {required_events - event_kinds}"

    # Verify analytics.run_metrics numbers consistent with rows
    recipe_rows = store.query("SELECT * FROM recipes WHERE run_id = ?", (run_id1,))
    api_call_rows = store.query("SELECT * FROM api_calls WHERE run_id = ?", (run_id1,))
    ok_calls = [c for c in api_call_rows if c["ok"]]
    failed_calls = [c for c in api_call_rows if not c["ok"]]

    m1 = analytics.run_metrics(store, run_id1)
    assert m1["run_id"] == run_id1
    assert m1["status"] == "completed"
    assert m1["recipes"]["total"] == len(recipe_rows)
    assert m1["api"]["calls"] == len(api_call_rows)
    assert m1["api"]["ok"] == len(ok_calls)
    assert m1["api"]["failed"] == len(failed_calls)
    assert m1["chunks"]["selected"] == run1["chunks_total"]
    assert m1["chunks"]["done"] == run1["chunks_done"]

    # Run second run for comparison
    run_id2 = store.create_run(
        book_path=DUAL_UNITS_EPUB,
        book_name="dual-units.epub",
        provider="replay",
        options_json={"outputs": ["json"]},
        settings_json={"GEMINI_MODEL": {"value": "gemini-2.5-flash", "source": "saved"}},
    )
    runner2 = Runner(store, run_id2, tmp_path)
    exit_code2 = runner2.execute()
    assert exit_code2 == 0

    # analytics.compare of two runs
    cmp_res = analytics.compare(store, [run_id1, run_id2])
    assert len(cmp_res["runs"]) == 2
    assert "rows" in cmp_res
    assert len(cmp_res["rows"]) > 0

    # settings_diff detects GEMINI_MODEL diff
    assert any(s["key"] == "GEMINI_MODEL" for s in cmp_res["settings_diff"])

    # recipes overlap
    recipes_overlap = cmp_res["recipes"]
    assert recipes_overlap["union"] == len(recipe_rows)
    assert recipes_overlap["common"] == len(recipe_rows)
    assert recipes_overlap["only"] == [[], []]

    # Server endpoints via TestClient
    env_file = tmp_path / ".env"
    env_file.touch()
    app = create_app(tmp_path, env_file, start_monitor=False)
    client = TestClient(app)

    # /api/runs
    r_runs = client.get("/api/runs")
    assert r_runs.status_code == 200
    assert "runs" in r_runs.json()

    # /api/runs/{id}
    r_run = client.get(f"/api/runs/{run_id1}")
    assert r_run.status_code == 200
    assert r_run.json()["run"]["id"] == run_id1

    # /api/runs/{id}/events
    r_ev = client.get(f"/api/runs/{run_id1}/events")
    assert r_ev.status_code == 200
    assert "events" in r_ev.json()

    # /api/runs/{id}/errors and /api/errors
    r_err1 = client.get(f"/api/runs/{run_id1}/errors")
    assert r_err1.status_code == 200
    assert "errors" in r_err1.json()

    r_err_all = client.get("/api/errors")
    assert r_err_all.status_code == 200
    assert "errors" in r_err_all.json()

    # /api/runs/{id}/calls and /api/calls/{call_id}
    r_calls = client.get(f"/api/runs/{run_id1}/calls")
    assert r_calls.status_code == 200
    calls_list = r_calls.json()["calls"]
    assert len(calls_list) > 0

    first_call_id = calls_list[0]["id"]
    r_call = client.get(f"/api/calls/{first_call_id}")
    assert r_call.status_code == 200
    assert r_call.json()["id"] == first_call_id

    # /api/runs/{id}/recipes and /api/recipes/{recipe_id}
    r_recipes = client.get(f"/api/runs/{run_id1}/recipes")
    assert r_recipes.status_code == 200
    rec_list = r_recipes.json()["recipes"]
    assert len(rec_list) > 0

    first_rec_id = rec_list[0]["id"]
    r_rec = client.get(f"/api/recipes/{first_rec_id}")
    assert r_rec.status_code == 200
    assert r_rec.json()["id"] == first_rec_id

    # /api/runs/{id}/analytics
    r_an = client.get(f"/api/runs/{run_id1}/analytics")
    assert r_an.status_code == 200
    assert r_an.json()["run_id"] == run_id1

    # /api/compare
    r_cmp = client.get(f"/api/compare?ids={run_id1},{run_id2}")
    assert r_cmp.status_code == 200
    assert "rows" in r_cmp.json()
