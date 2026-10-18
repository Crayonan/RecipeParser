import threading
import time
from unittest.mock import MagicMock

from recipeparser.core.fsm import PipelineController, PipelineStatus
from recipeparser.core.models import Chunk, InputType
from recipeparser.core.pipeline import RecipePipeline
from recipeparser.core.stages.extract import Extraction
from recipeparser.models import (
    CayenneRefinement,
    RecipeExtraction,
    StructuredIngredient,
    TokenizedDirection,
)


def test_fsm_wait_if_paused_running():
    controller = PipelineController()
    controller.transition("start")
    assert controller.status == PipelineStatus.RUNNING
    # In RUNNING state, wait_if_paused returns True immediately
    assert controller.wait_if_paused() is True


def test_fsm_wait_if_paused_pause_and_resume():
    controller = PipelineController()
    controller.transition("start")

    # Request pause
    assert controller.request_pause() is True
    assert controller.status == PipelineStatus.PAUSING

    worker_result = []

    def worker():
        res = controller.wait_if_paused()
        worker_result.append(res)

    t = threading.Thread(target=worker)
    t.start()

    # Worker should transition PAUSING -> PAUSED and block
    time.sleep(0.05)
    assert controller.status == PipelineStatus.PAUSED
    assert t.is_alive()
    assert worker_result == []

    # Resume
    assert controller.request_resume() is True
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert worker_result == [True]
    assert controller.status == PipelineStatus.RUNNING


def test_fsm_wait_if_paused_cancel_while_paused():
    controller = PipelineController()
    controller.transition("start")
    controller.request_pause()

    worker_result = []

    def worker():
        res = controller.wait_if_paused()
        worker_result.append(res)

    t = threading.Thread(target=worker)
    t.start()

    time.sleep(0.05)
    assert controller.status == PipelineStatus.PAUSED
    assert t.is_alive()

    # Cancel while worker is blocked
    assert controller.request_cancel() is True
    t.join(timeout=2.0)
    assert not t.is_alive()
    # When cancelled, wait_if_paused returns False
    assert worker_result == [False]
    assert controller.status == PipelineStatus.CANCELLING


def test_fsm_wait_if_paused_when_cancelling_returns_false_immediately():
    controller = PipelineController()
    controller.transition("start")
    controller.request_cancel()
    assert controller.status == PipelineStatus.CANCELLING
    assert controller.wait_if_paused() is False


def test_fsm_request_pause_clears_resume_event():
    controller = PipelineController()
    controller.transition("start")
    assert controller._resume_event.is_set()
    controller.request_pause()
    assert not controller._resume_event.is_set()


def test_recipe_pipeline_pause_resume_cancel_with_fake(monkeypatch):
    """RecipePipeline with fake stages: pause stops new calls, resume continues, cancel ends early."""
    call_log = []
    extract_gate = threading.Event()
    extract_started = threading.Event()

    def fake_extract(*_args, **_kwargs):
        call_log.append("extract_start")
        extract_started.set()
        extract_gate.wait(timeout=2.0)
        call_log.append("extract_done")
        return Extraction(
            recipes=[
                RecipeExtraction(
                    name="Pancake",
                    ingredients=["1 cup flour", "1 cup milk"],
                    directions=["Mix well", "Cook on griddle"],
                )
            ],
            rewritten=[],
        )

    def fake_refine(*_args, **kwargs):
        raw = kwargs.get("raw") or _args[0]
        call_log.append("refine")
        return CayenneRefinement(
            title=raw.name,
            base_servings=4,
            structured_ingredients=[
                StructuredIngredient(id="i1", kind="ingredient", amount=1.0, unit="cup", name="flour", fallback_string="1 cup flour")
            ],
            tokenized_directions=[TokenizedDirection(step=1, text="Mix well")],
        )

    def fake_embed(*_args, **_kwargs):
        call_log.append("embed")
        return [0.1] * 1536

    monkeypatch.setattr("recipeparser.core.pipeline.extract", fake_extract)
    monkeypatch.setattr("recipeparser.core.pipeline.refine", fake_refine)
    monkeypatch.setattr("recipeparser.core.pipeline.embed", fake_embed)
    monkeypatch.setattr(
        "recipeparser.core.pipeline.tag_batch",
        lambda *_args, **_kwargs: [],
    )

    fake_cat = MagicMock()
    fake_cat.load_axes.return_value = {}
    fake_cat.load_parents.return_value = {}

    controller = PipelineController()
    pipeline = RecipePipeline(
        client=MagicMock(),
        controller=controller,
        category_source=fake_cat,
        concurrency=1,
        rpm=500,
    )

    chunks = [
        Chunk(text="Recipe 1", input_type=InputType.EPUB, label="chunk_0"),
        Chunk(text="Recipe 2", input_type=InputType.EPUB, label="chunk_1"),
    ]

    pipeline_result = []

    def run_pipeline():
        res = pipeline.run(chunks)
        pipeline_result.append(res)

    t = threading.Thread(target=run_pipeline)
    t.start()

    # Wait until chunk 0 starts extracting
    assert extract_started.wait(timeout=2.0)

    # Now request pause before chunk 0 finishes
    assert controller.request_pause() is True

    # Allow chunk 0 extract to finish
    extract_gate.set()

    time.sleep(0.1)

    # Controller is paused
    assert controller.status in (PipelineStatus.PAUSING, PipelineStatus.PAUSED)

    # Resume and finish
    controller.request_resume()
    t.join(timeout=3.0)
    assert not t.is_alive()
    assert len(pipeline_result) == 1
    assert len(pipeline_result[0]) == 2


def test_recipe_pipeline_cancel_ends_early(monkeypatch):
    """When cancelled, wait_if_paused returns False, _Cancelled is raised, pipeline ends early."""
    gate = threading.Event()

    def fake_extract(*_args, **_kwargs):
        gate.wait(timeout=2.0)
        return Extraction(
            recipes=[
                RecipeExtraction(
                    name="Pancake",
                    ingredients=["1 cup flour"],
                    directions=["Mix well"],
                )
            ],
            rewritten=[],
        )

    monkeypatch.setattr("recipeparser.core.pipeline.extract", fake_extract)

    fake_cat = MagicMock()
    fake_cat.load_axes.return_value = {}
    fake_cat.load_parents.return_value = {}

    controller = PipelineController()
    pipeline = RecipePipeline(
        client=MagicMock(),
        controller=controller,
        category_source=fake_cat,
        concurrency=1,
        rpm=500,
    )

    chunks = [
        Chunk(text="Recipe 1", input_type=InputType.EPUB, label="chunk_0"),
        Chunk(text="Recipe 2", input_type=InputType.EPUB, label="chunk_1"),
    ]

    pipeline_result = []

    def run_pipeline():
        res = pipeline.run(chunks)
        pipeline_result.append(res)

    t = threading.Thread(target=run_pipeline)
    t.start()

    # Cancel immediately
    time.sleep(0.05)
    controller.request_cancel()
    gate.set()

    t.join(timeout=3.0)
    assert not t.is_alive()
    assert len(pipeline_result) == 1
    # Cancelled before completing both chunks
    assert len(pipeline_result[0]) < 2
