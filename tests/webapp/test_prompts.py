import json
import time

from fastapi.testclient import TestClient

from recipeparser import gemini
from recipeparser import prompts as prompts_mod
from recipeparser.adapters.webapp.manager import REPO_ROOT, RunManager
from recipeparser.adapters.webapp.server import create_app
from recipeparser.adapters.webapp.store import Store

DUAL_UNITS_EPUB = str(REPO_ROOT / "tests" / "goldens" / "corpus" / "dual-units.epub")


def test_prompts_render_with_override_file(tmp_path, monkeypatch):
    override_file = tmp_path / "prompt_overrides.json"
    template_content = 'Custom preamble: {"json_example": 123} {{source_host}} RAW RECIPE: {{raw_recipe}}'
    override_file.write_text(json.dumps({"refine": template_content}), encoding="utf-8")

    monkeypatch.setenv(prompts_mod.PROMPT_OVERRIDES_ENV, str(override_file))

    rendered = prompts_mod.render("refine", source_host="example.com", raw_recipe="flour 100g")
    assert 'Custom preamble: {"json_example": 123}' in rendered
    assert "example.com RAW RECIPE: flour 100g" in rendered


def test_prompts_default_rendering_identical_to_gemini(monkeypatch):
    monkeypatch.delenv(prompts_mod.PROMPT_OVERRIDES_ENV, raising=False)

    # table prompt
    assert prompts_mod.render("table", text_chunk="100% flour") == gemini.build_table_prompt("100% flour")

    # plain_text prompt
    assert prompts_mod.render("plain_text", text="Cake recipe") == gemini.build_plain_text_prompt("Cake recipe")

    # extract prompt
    assert prompts_mod.render("extract", text_chunk="Chapter 1") == gemini.build_extract_prompt("Chapter 1")

    # refine prompt
    assert prompts_mod.render(
        "refine", source_host="myhost.org", raw_recipe="pancake raw"
    ) == gemini.build_refine_prompt("pancake raw", "myhost.org")


def test_prompts_validate_rules():
    # Missing required placeholder (refine requires source_host and raw_recipe)
    problems_missing = prompts_mod.validate("refine", "Only {{source_host}} is here")
    assert any("raw_recipe" in p for p in problems_missing)

    # Unknown placeholder
    problems_unknown = prompts_mod.validate("refine", "Here is {{source_host}} and {{raw_recipe}} and {{unknown_xyz}}")
    assert any("unknown_xyz" in p for p in problems_unknown)

    # Valid template
    problems_valid = prompts_mod.validate("refine", "Header {{source_host}} body {{raw_recipe}}")
    assert problems_valid == []

    # Nonexistent prompt
    problems_bad_name = prompts_mod.validate("nonexistent_prompt", "content")
    assert len(problems_bad_name) == 1


def test_put_prompts_and_runner_uses_override(tmp_path):
    env_file = tmp_path / ".env"
    env_file.touch()
    store = Store(tmp_path / "webapp.db")

    app = create_app(tmp_path, env_file, start_monitor=False)
    client = TestClient(app)

    # Built-in refine default template
    orig_template = prompts_mod.spec("refine").default_template()
    # Add a recognizable custom header before the marker, leaving the rest identical
    custom_template = f"MY_CUSTOM_HEADER_12345\n{orig_template}"

    # 1. PUT /api/prompts/refine
    put_res = client.put("/api/prompts/refine", json={"template": custom_template})
    assert put_res.status_code == 200
    assert "prompts" in put_res.json()

    # 2. RunManager starts a replay run on dual-units.epub
    mgr = RunManager(store, tmp_path, env_file)
    mgr.start()
    try:
        run_id = mgr.start_run(
            book_path=DUAL_UNITS_EPUB,
            provider="replay",
            options={"concurrency": 2, "settings_overrides": {"RP_REPLAY_LATENCY_MS": "0"}},
        )

        t0 = time.time()
        run = None
        while time.time() - t0 < 10:
            run = store.get_run(run_id)
            if run and run["status"] in ("completed", "failed", "crashed"):
                break
            time.sleep(0.1)

        assert run is not None and run["status"] == "completed"

        # Run records prompts_json
        assert "refine" in (run.get("prompts") or {})
        assert "MY_CUSTOM_HEADER_12345" in run["prompts"]["refine"]

        # Runner child used the override: api_calls prompt_text has the header
        calls = store.query(
            "SELECT prompt_text FROM api_calls WHERE run_id = ? AND stage = 'REFINING'",
            (run_id,),
        )
        assert len(calls) > 0
        for c in calls:
            assert c["prompt_text"] is not None
            assert c["prompt_text"].startswith("MY_CUSTOM_HEADER_12345")
    finally:
        mgr.shutdown()
