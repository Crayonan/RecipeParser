"""Recipe Lab (adapters/webapp/lab.py): judge, refine, revisions and pictures, through the HTTP API."""

import io
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image

from recipeparser import prompts as prompts_mod
from recipeparser.adapters.webapp.server import create_app

FOCACCIA = {
    "title": "Zucchini Focaccia",
    "description": "A summer focaccia.",
    "base_servings": None,
    "servings_note": None,
    "prep_time": None,
    "cook_time": "20 minutes",
    "notes": None,
    "source": "Flour Water Salt Yeast",
    "ingredient_lines": [],
    "direction_steps": [],
    "structured_ingredients": [
        {"id": "ing_01", "name": "dough", "fallback_string": "800 grams dough"},
        {"id": "ing_02", "name": "zucchini", "fallback_string": "2 zucchini, thinly sliced"},
    ],
    "tokenized_directions": [
        {"step": 1, "text": "Bring the {{ing_01|dough|all}} to room temperature."},
        {"step": 2, "text": "Moderately flour the work surface and stretch the"},
    ],
}


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (40, 30), (200, 120, 40, 255)).save(buf, "PNG")
    return buf.getvalue()


class FakeGemini:
    """Answers generate_content from a queue; records each call's model and prompt."""

    def __init__(self) -> None:
        self.replies: list = []
        self.calls: list = []
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "prompt": contents})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, bytes):
            part = SimpleNamespace(inline_data=SimpleNamespace(data=reply, mime_type="image/png"))
            return SimpleNamespace(
                prompt_feedback=None, candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))]
            )
        return SimpleNamespace(text=json.dumps(reply), usage_metadata=SimpleNamespace(total_token_count=321))


JUDGEMENT = {
    "overall": 14,  # out of range on purpose: clamped to 10
    "scores": {"completeness": 2, "consistency": 3, "clarity": 0, "plausibility": 4},
    "summary": "Step 2 is cut off and flour is not listed.",
    "suggestions": [
        {
            "kind": "fix",
            "target": "ingredients",
            "severity": "high",
            "issue": "Flour is used in step 2 but not listed.",
            "suggestion": "Add flour for dusting to the ingredients.",
            "quote": "Moderately flour the work surface",
        },
        {"kind": "tip", "target": "general", "severity": "low", "issue": "Bland.", "suggestion": "Add lemon zest."},
    ],
}


def _setup(tmp_path):
    env_file = tmp_path / ".env"
    env_file.touch()
    app = create_app(tmp_path, env_file, start_monitor=False)
    fake = FakeGemini()
    app.state.lab_client = fake
    store = app.state.store
    run_id = store.create_run(
        book_path="/b/book.epub", book_name="book.epub", provider="gemini", options_json={}, settings_json={}
    )
    recipe_id = store.add_recipe(
        run_id,
        chunk=0,
        position=1,
        title="Zucchini Focaccia",
        needs_review=1,
        review_json=[{"kind": "needs_review", "text": "Flour is not listed."}],
        categories_json=["Bread"],
        data_json=FOCACCIA,
    )
    return TestClient(app), fake, store, run_id, recipe_id


def _proposal(**changes):
    draft = {
        "title": "Zucchini Focaccia",
        "description": "A summer focaccia.",
        "servings": None,
        "prep_time": None,
        "cook_time": "20 minutes",
        "ingredients": ["800 grams dough", "2 zucchini, thinly sliced"],
        "directions": [
            "Bring the dough to room temperature.",
            "Moderately flour the work surface and stretch the",
        ],
        "notes": None,
        "change_summary": "Done.",
    }
    draft.update(changes)
    return draft


def test_lab_shows_the_extraction_as_an_editable_draft(tmp_path):
    client, _fake, _store, run_id, rid = _setup(tmp_path)

    lab = client.get(f"/api/recipes/{rid}/lab").json()

    assert lab["revision"] == 0
    assert lab["current"] == lab["original"]
    assert lab["current"]["ingredients"] == ["800 grams dough", "2 zucchini, thinly sliced"]
    # Fat tokens become their display words.
    assert lab["current"]["directions"][0] == "Bring the dough to room temperature."
    assert lab["recipe"]["run_id"] == run_id and lab["recipe"]["categories"] == ["Bread"]
    assert lab["judgement"] is None and lab["pending"] is None and lab["pictures"] == []
    assert lab["ai"]["available"] is True
    assert client.get("/api/recipes/9999/lab").status_code == 404


def test_judge_scores_are_clamped_and_suggestions_get_ids(tmp_path):
    client, fake, _store, _run, rid = _setup(tmp_path)
    fake.replies.append(JUDGEMENT)

    lab = client.post(f"/api/recipes/{rid}/judge").json()

    j = lab["judgement"]
    assert j["overall"] == 10
    assert j["scores"] == {"completeness": 2, "consistency": 3, "clarity": 1, "plausibility": 4}
    assert [s["id"] for s in j["suggestions"]] == ["s1", "s2"]
    assert [s["state"] for s in j["suggestions"]] == ["open", "open"]
    assert j["stale"] is False and j["revision"] == 0
    assert "Moderately flour the work surface" in fake.calls[0]["prompt"]


def test_judge_uses_the_saved_prompt_override(tmp_path):
    client, fake, store, _run, rid = _setup(tmp_path)
    store.save_prompt("judge", "CUSTOM JUDGE {{recipe}}")
    fake.replies.append(JUDGEMENT)

    client.post(f"/api/recipes/{rid}/judge")

    assert fake.calls[0]["prompt"].startswith("CUSTOM JUDGE {")
    assert "lab" in {s.stage.lower() for s in prompts_mod.specs()}


def test_model_failure_is_a_502_with_the_reason(tmp_path):
    client, fake, _store, _run, rid = _setup(tmp_path)
    fake.replies.append(ValueError("model exploded"))

    r = client.post(f"/api/recipes/{rid}/judge")

    assert r.status_code == 502
    assert "model exploded" in r.json()["detail"]
    assert client.get(f"/api/recipes/{rid}/lab").json()["judgement"] is None


def test_applying_a_suggestion_proposes_then_accepts_within_its_scope(tmp_path):
    client, fake, _store, _run, rid = _setup(tmp_path)
    fake.replies.append(JUDGEMENT)
    jid = client.post(f"/api/recipes/{rid}/judge").json()["judgement"]["id"]
    # The model also rewrites the title, which is outside the suggestion's "ingredients" scope.
    fake.replies.append(
        _proposal(
            title="SHOUTY TITLE", ingredients=["800 grams dough", "2 zucchini, thinly sliced", "Flour, for dusting"]
        )
    )

    lab = client.post(
        f"/api/recipes/{rid}/refine",
        json={
            "instruction": "Add flour for dusting.",
            "target": "ingredients",
            "suggestion": {"judgement_id": jid, "suggestion_id": "s1"},
        },
    ).json()

    pending = lab["pending"]
    assert pending["status"] == "pending" and pending["source"] == "suggestion"
    assert pending["data"]["title"] == "Zucchini Focaccia"
    assert pending["data"]["ingredients"][-1] == "Flour, for dusting"
    assert lab["current"]["ingredients"] == lab["original"]["ingredients"]
    assert lab["judgement"]["suggestions"][0]["state"] == "proposed"

    lab = client.post(f"/api/recipes/{rid}/revisions/{pending['id']}/accept").json()

    assert lab["revision"] == 1 and lab["pending"] is None
    assert lab["current"]["ingredients"][-1] == "Flour, for dusting"
    assert lab["judgement"]["suggestions"][0]["state"] == "applied"
    assert lab["judgement"]["stale"] is True
    listed = client.get("/api/recipes").json()["recipes"][0]
    assert listed["revision"] == 1 and listed["score"] == 10 and listed["stale_score"] is True
    # Accepting twice is refused.
    assert client.post(f"/api/recipes/{rid}/revisions/{pending['id']}/accept").status_code == 409


def test_a_new_proposal_replaces_the_pending_one_and_discard_keeps_current(tmp_path):
    client, fake, _store, _run, rid = _setup(tmp_path)
    fake.replies += [_proposal(title="Focaccia One"), _proposal(title="Focaccia Two")]

    first = client.post(f"/api/recipes/{rid}/refine", json={"instruction": "Retitle", "target": "title"}).json()
    second = client.post(f"/api/recipes/{rid}/refine", json={"instruction": "Retitle", "target": "title"}).json()

    assert second["pending"]["data"]["title"] == "Focaccia Two"
    statuses = {r["id"]: r["status"] for r in second["revisions"]}
    assert statuses[first["pending"]["id"]] == "discarded"
    lab = client.post(f"/api/recipes/{rid}/revisions/{second['pending']['id']}/discard").json()
    assert lab["pending"] is None and lab["current"]["title"] == "Zucchini Focaccia"


def test_refine_without_a_change_or_instruction_is_refused(tmp_path):
    client, fake, _store, _run, rid = _setup(tmp_path)
    fake.replies.append(_proposal(change_summary="The recipe already says that."))

    r = client.post(f"/api/recipes/{rid}/refine", json={"instruction": "Say it", "target": "all"})
    assert r.status_code == 422 and "already says that" in r.json()["detail"]
    assert client.post(f"/api/recipes/{rid}/refine", json={"instruction": "  "}).status_code == 422
    assert client.post(f"/api/recipes/{rid}/refine", json={"instruction": "x", "target": "nope"}).status_code == 422


def test_manual_edit_and_restore_original(tmp_path):
    client, _fake, _store, _run, rid = _setup(tmp_path)
    draft = client.get(f"/api/recipes/{rid}/lab").json()["current"]
    draft["title"] = "  Squash Focaccia "
    draft["directions"] = draft["directions"] + ["", "Bake until golden."]

    lab = client.post(f"/api/recipes/{rid}/revisions", json={"data": draft}).json()

    assert lab["revision"] == 1 and lab["current"]["title"] == "Squash Focaccia"
    assert lab["current"]["directions"][-1] == "Bake until golden." and "" not in lab["current"]["directions"]
    assert client.post(f"/api/recipes/{rid}/revisions", json={"data": lab["current"]}).status_code == 422
    assert client.get("/api/recipes?q=squash").json()["total"] == 1

    lab = client.post(f"/api/recipes/{rid}/revisions/0/restore").json()

    assert lab["revision"] == 2 and lab["current"] == lab["original"]
    assert lab["revisions"][0]["source"] == "revert"
    exported = client.get(f"/api/recipes/{rid}/export")
    assert "zucchini-focaccia.json" in exported.headers["content-disposition"]
    assert exported.json()["title"] == "Zucchini Focaccia" and exported.json()["revision"] == 2


def test_pictures_generate_upload_cover_and_delete(tmp_path):
    client, fake, _store, _run, rid = _setup(tmp_path)
    fake.replies.append(_png())

    lab = client.post(f"/api/recipes/{rid}/pictures/generate").json()

    first = lab["pictures"][0]
    assert first["kind"] == "generated" and first["cover"] is True
    assert "Zucchini Focaccia" in fake.calls[0]["prompt"]
    img = client.get(first["url"])
    assert img.headers["content-type"] == "image/jpeg" and img.content[:2] == b"\xff\xd8"

    lab = client.post(f"/api/recipes/{rid}/pictures", files={"file": ("p.png", _png(), "image/png")}).json()
    uploaded = next(p for p in lab["pictures"] if p["kind"] == "uploaded")
    assert uploaded["cover"] is False
    assert client.get("/api/recipes").json()["recipes"][0]["cover_url"] == first["url"]

    lab = client.post(f"/api/pictures/{uploaded['id']}/cover").json()
    assert [p["id"] for p in lab["pictures"] if p["cover"]] == [uploaded["id"]]

    lab = client.delete(f"/api/pictures/{uploaded['id']}").json()
    assert [(p["id"], p["cover"]) for p in lab["pictures"]] == [(first["id"], True)]
    assert client.get(uploaded["url"]).status_code == 404

    bad = client.post(f"/api/recipes/{rid}/pictures", files={"file": ("x.png", b"not a picture", "image/png")})
    assert bad.status_code == 422


def test_without_gemini_the_lab_says_why_and_refuses_model_calls(tmp_path):
    client, _fake, store, _run, rid = _setup(tmp_path)
    client.app.state.lab_client = None
    store.save_settings({"RP_PROVIDER": "replay"})

    lab = client.get(f"/api/recipes/{rid}/lab").json()
    r = client.post(f"/api/recipes/{rid}/judge")

    assert lab["ai"]["available"] is False and "Gemini" in lab["ai"]["reason"]
    assert r.status_code == 409 and r.json()["detail"] == lab["ai"]["reason"]


def test_deleting_the_run_removes_its_lab_data(tmp_path):
    client, fake, store, run_id, rid = _setup(tmp_path)
    fake.replies += [JUDGEMENT, _png()]
    client.post(f"/api/recipes/{rid}/judge")
    client.post(f"/api/recipes/{rid}/pictures/generate")
    store.update_run(run_id, status="completed")

    assert client.delete(f"/api/runs/{run_id}").status_code == 200

    for table in ("recipe_judgements", "recipe_pictures", "recipe_revisions"):
        assert store.query(f"SELECT * FROM {table}") == []
    assert not (tmp_path / "runs" / run_id).exists()
