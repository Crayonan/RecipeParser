import gzip
import json
import sys
import zipfile
from unittest.mock import MagicMock

from recipeparser.adapters.webapp.runner import Runner
from recipeparser.adapters.webapp.store import Store
from recipeparser.core.fsm import PipelineController
from recipeparser.core.models import Chunk, InputType
from recipeparser.core.pipeline import RecipePipeline
from recipeparser.core.stages.extract import Extraction
from recipeparser.core.stages.refine import refine
from recipeparser.io.writers.cayenne_zip import CayenneZipWriter
from recipeparser.io.writers.paprika_zip import PaprikaWriter
from recipeparser.models import (
    CayenneRefinement,
    IngestResponse,
    RecipeExtraction,
    ReviewNote,
    StructuredIngredient,
    TokenizedDirection,
)


def test_review_note_model():
    note = ReviewNote(kind="missing", text="Salt quantity is missing in directions")
    assert note.kind == "missing"
    assert note.text == "Salt quantity is missing in directions"
    dump = note.model_dump()
    assert dump == {"kind": "missing", "text": "Salt quantity is missing in directions"}


def test_refine_keeps_review_notes(monkeypatch):
    raw = RecipeExtraction(
        name="Apple Tart",
        ingredients=["1 cup flour", "2 apples"],
        directions=["Roll dough", "Bake 30 min"],
    )

    notes = [
        ReviewNote(kind="ambiguous", text="Oven temperature not specified"),
        ReviewNote(kind="missing", text="Baking pan size missing"),
    ]

    mock_refinement = CayenneRefinement(
        title="Apple Tart",
        base_servings=4,
        structured_ingredients=[
            StructuredIngredient(id="i1", amount=1.0, unit="cup", name="flour", fallback_string="1 cup flour"),
            StructuredIngredient(id="i2", amount=2.0, unit="", name="apples", fallback_string="2 apples"),
        ],
        tokenized_directions=[
            TokenizedDirection(step=1, text="Roll dough"),
            TokenizedDirection(step=2, text="Bake 30 min"),
        ],
        review_notes=notes,
    )

    refine_module = sys.modules["recipeparser.core.stages.refine"]
    monkeypatch.setattr(refine_module, "refine_recipe_for_cayenne", lambda **_kwargs: mock_refinement)

    refined = refine(raw=raw, client=MagicMock())
    assert len(refined.review_notes) == 2
    assert refined.review_notes[0].text == "Oven temperature not specified"
    assert refined.review_notes[1].text == "Baking pan size missing"


def test_pipeline_populates_ingest_response_review_notes(monkeypatch):
    raw_extraction = Extraction(
        recipes=[
            RecipeExtraction(
                name="Sponge Cake",
                ingredients=["3 eggs", "100g sugar"],
                directions=["Whisk eggs", "Bake at 180C"],
            )
        ],
        rewritten=[],
    )

    notes = [ReviewNote(kind="diet", text="Contains eggs")]

    mock_refinement = CayenneRefinement(
        title="Sponge Cake",
        base_servings=6,
        structured_ingredients=[
            StructuredIngredient(id="i1", amount=3.0, unit="", name="eggs", fallback_string="3 eggs"),
            StructuredIngredient(id="i2", amount=100.0, unit="g", name="sugar", fallback_string="100g sugar"),
        ],
        tokenized_directions=[
            TokenizedDirection(step=1, text="Whisk eggs"),
            TokenizedDirection(step=2, text="Bake at 180C"),
        ],
        review_notes=notes,
    )

    monkeypatch.setattr("recipeparser.core.pipeline.extract", lambda *_args, **_kwargs: raw_extraction)
    monkeypatch.setattr("recipeparser.core.pipeline.refine", lambda *_args, **_kwargs: mock_refinement)
    monkeypatch.setattr("recipeparser.core.pipeline.embed", lambda *_args, **_kwargs: [0.1] * 1536)
    monkeypatch.setattr("recipeparser.core.pipeline.tag_batch", lambda *_args, **_kwargs: [])

    fake_cat = MagicMock()
    fake_cat.load_axes.return_value = {}
    fake_cat.load_parents.return_value = {}

    pipeline = RecipePipeline(
        client=MagicMock(),
        controller=PipelineController(),
        category_source=fake_cat,
        concurrency=1,
        rpm=500,
    )

    chunks = [Chunk(text="Recipe chunk", input_type=InputType.EPUB, label="c0")]
    results = pipeline.run(chunks)

    assert len(results) == 1
    resp = results[0]
    assert isinstance(resp, IngestResponse)
    assert len(resp.review_notes) == 1
    assert resp.review_notes[0].text == "Contains eggs"


def test_review_notes_not_present_in_model_dump_or_exports(tmp_path):
    note = ReviewNote(kind="needs_review", text="Check sugar amount")
    resp = IngestResponse(
        title="Pudding",
        base_servings=4,
        structured_ingredients=[
            StructuredIngredient(id="i1", amount=1, unit="cup", name="milk", fallback_string="1 cup milk")
        ],
        tokenized_directions=[TokenizedDirection(step=1, text="Boil milk")],
        embedding=[0.2] * 1536,
    )
    resp.attach_review_notes([note])
    assert len(resp.review_notes) == 1

    # 1. model_dump must NOT include review_notes (it is a PrivateAttr)
    dump = resp.model_dump()
    assert "review_notes" not in dump
    assert "_review_notes" not in dump

    # 2. Cayenne zip must NOT include review_notes
    cayenne_zip_path = tmp_path / "test.cayenne.zip"
    CayenneZipWriter(output_path=cayenne_zip_path).write([resp])
    assert cayenne_zip_path.is_file()

    with zipfile.ZipFile(cayenne_zip_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith(".json"):
                data = json.loads(zf.read(name).decode("utf-8"))
                assert "review_notes" not in data, f"review_notes leaked into cayenne zip {name}"

    # 3. Paprika export must NOT include review_notes
    paprika_path = tmp_path / "test.paprikarecipes"
    PaprikaWriter(output_path=paprika_path).write([resp])
    assert paprika_path.is_file()

    with zipfile.ZipFile(paprika_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith(".paprikarecipe"):
                data = json.loads(gzip.decompress(zf.read(name)).decode("utf-8"))
                assert "review_notes" not in data, f"review_notes leaked into paprika export {name}"


def test_runner_on_result_stores_review_notes_and_sets_flag(tmp_path):
    store = Store(tmp_path / "webapp.db")
    run_id = store.create_run(
        book_path="/dummy/book.epub",
        book_name="book.epub",
        provider="replay",
        options_json={},
        settings_json={},
    )

    runner = Runner(store, run_id, tmp_path)

    # 1. Recipe with review notes -> needs_review is 1
    resp1 = IngestResponse(
        title="Recipe with Note",
        base_servings=2,
        structured_ingredients=[
            StructuredIngredient(id="i1", amount=1, unit="tsp", name="salt", fallback_string="1 tsp salt")
        ],
        tokenized_directions=[TokenizedDirection(step=1, text="Add salt")],
        embedding=[0.1] * 1536,
    )
    resp1.attach_review_notes([ReviewNote(kind="needs_review", text="Salt quantity 1 tsp given only in method")])

    runner._on_result(resp1)

    row1 = store.one("SELECT * FROM recipes WHERE run_id = ? AND title = 'Recipe with Note'", (run_id,))
    assert row1 is not None
    assert row1["needs_review"] == 1
    reviews1 = json.loads(row1["review_json"])
    assert len(reviews1) == 1
    assert reviews1[0]["kind"] == "needs_review"
    assert reviews1[0]["text"] == "Salt quantity 1 tsp given only in method"

    # 2. Recipe without review notes -> needs_review is 0
    resp2 = IngestResponse(
        title="Recipe without Note",
        base_servings=2,
        structured_ingredients=[
            StructuredIngredient(id="i1", amount=1, unit="tsp", name="sugar", fallback_string="1 tsp sugar")
        ],
        tokenized_directions=[TokenizedDirection(step=1, text="Add sugar")],
        embedding=[0.1] * 1536,
    )
    runner._on_result(resp2)

    row2 = store.one("SELECT * FROM recipes WHERE run_id = ? AND title = 'Recipe without Note'", (run_id,))
    assert row2 is not None
    assert row2["needs_review"] == 0
    reviews2 = json.loads(row2["review_json"])
    assert reviews2 == []
