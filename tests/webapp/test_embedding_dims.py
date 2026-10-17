from unittest.mock import MagicMock

import pytest

from recipeparser import providers
from recipeparser.adapters.webapp import settings
from recipeparser.adapters.webapp.manager import REPO_ROOT, RunError, RunManager
from recipeparser.adapters.webapp.store import Store
from recipeparser.core.stages.embed import embed
from recipeparser.models import CayenneRefinement, StructuredIngredient, TokenizedDirection

GUTENBERG_EPUB = str(REPO_ROOT / "tests" / "goldens" / "corpus" / "gutenberg-multi.epub")


def test_check_embedding_model_verdicts():
    # ok
    v_ok, msg_ok = providers.check_embedding_model("gemini", "models/gemini-embedding-001")
    assert v_ok == "ok"
    assert "1536" in msg_ok

    v_bare, _ = providers.check_embedding_model("gemini", "gemini-embedding-001")
    assert v_bare == "ok"

    # rejected
    v_rej, msg_rej = providers.check_embedding_model("gemini", "models/text-embedding-004")
    assert v_rej == "rejected"
    assert "768" in msg_rej

    v_rej_bare, _ = providers.check_embedding_model("gemini", "text-embedding-004")
    assert v_rej_bare == "rejected"

    v_empty, _ = providers.check_embedding_model("gemini", "")
    assert v_empty == "rejected"

    # unverified
    v_unv, msg_unv = providers.check_embedding_model("gemini", "custom-model-foo")
    assert v_unv == "unverified"
    assert "not a known" in msg_unv


def test_settings_validate_rejects_text_embedding_004():
    # Both bare and prefixed names must be rejected
    res_prefixed = settings.validate("GEMINI_EMBEDDING_MODEL", "models/text-embedding-004")
    assert res_prefixed is not None
    assert "768" in res_prefixed

    res_bare = settings.validate("GEMINI_EMBEDDING_MODEL", "text-embedding-004")
    assert res_bare is not None
    assert "768" in res_bare

    # Valid model accepted
    assert settings.validate("GEMINI_EMBEDDING_MODEL", "models/gemini-embedding-001") is None


def test_manager_start_run_refuses_rejected_model(tmp_path):
    store = Store(tmp_path / "webapp.db")
    env_file = tmp_path / ".env"
    env_file.touch()
    mgr = RunManager(store, tmp_path, env_file)

    with pytest.raises(RunError, match="768 values"):
        mgr.start_run(
            book_path=GUTENBERG_EPUB,
            provider="gemini",
            options={
                "settings_overrides": {
                    "GEMINI_EMBEDDING_MODEL": "models/text-embedding-004",
                    "GOOGLE_API_KEY": "fake_key",
                }
            },
        )


def test_embed_stage_raises_on_non_1536():
    recipe = CayenneRefinement(
        title="Toast",
        base_servings=1,
        structured_ingredients=[
            StructuredIngredient(id="i1", amount=1, unit="slice", name="bread", fallback_string="1 slice bread")
        ],
        tokenized_directions=[TokenizedDirection(step=1, text="Toast bread")],
    )

    fake_client = MagicMock()
    # Return a 768-dim vector instead of 1536
    mock_response = MagicMock()
    mock_response.embeddings = [MagicMock(values=[0.1] * 768)]
    fake_client.models.embed_content.return_value = mock_response

    with pytest.raises(RuntimeError, match="expected 1536-dim vector, got 768"):
        embed(recipe=recipe, client=fake_client)
