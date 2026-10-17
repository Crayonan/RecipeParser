from unittest.mock import MagicMock

from google.genai import errors as genai_errors

from recipeparser.adapters.webapp import errors
from recipeparser.adapters.webapp.instrument import InstrumentedClient
from recipeparser.adapters.webapp.store import Store
from recipeparser.core import run_context


def test_errors_explain_genai_codes():
    # 400 generic
    e400 = errors.explain(
        genai_errors.ClientError(400, {"error": {"message": "Malformed request", "status": "INVALID_ARGUMENT"}})
    )
    assert e400.status_code == 400
    assert e400.code == "HTTP 400"
    assert e400.severity == "error"
    assert e400.category == "request"

    # 400 API key not valid
    e400_key = errors.explain(
        genai_errors.ClientError(
            400, {"error": {"message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}}
        )
    )
    assert e400_key.status_code == 400
    assert "API key not valid" in e400_key.title
    assert e400_key.category == "auth"
    assert "GOOGLE_API_KEY" in e400_key.cause
    assert "Settings → API keys" in e400_key.fix

    # 403
    e403 = errors.explain(
        genai_errors.ClientError(403, {"error": {"message": "Permission denied", "status": "PERMISSION_DENIED"}})
    )
    assert e403.status_code == 403
    assert e403.category == "auth"
    assert e403.severity == "error"

    # 404
    e404 = errors.explain(
        genai_errors.ClientError(404, {"error": {"message": "Model not found", "status": "NOT_FOUND"}})
    )
    assert e404.status_code == 404
    assert e404.category == "config"
    assert e404.severity == "error"

    # 429 (must be warning because retried)
    e429 = errors.explain(
        genai_errors.ClientError(429, {"error": {"message": "Resource exhausted", "status": "RESOURCE_EXHAUSTED"}})
    )
    assert e429.status_code == 429
    assert e429.severity == "warning"
    assert e429.category == "rate_limit"

    # 500 (warning because retried)
    e500 = errors.explain(
        genai_errors.ServerError(500, {"error": {"message": "Internal server error", "status": "INTERNAL"}})
    )
    assert e500.status_code == 500
    assert e500.severity == "warning"
    assert e500.category == "transient"

    # 503 (warning)
    e503 = errors.explain(
        genai_errors.ServerError(503, {"error": {"message": "Model unavailable", "status": "UNAVAILABLE"}})
    )
    assert e503.status_code == 503
    assert e503.severity == "warning"

    # 504 (warning)
    e504 = errors.explain(
        genai_errors.ServerError(504, {"error": {"message": "Deadline exceeded", "status": "DEADLINE_EXCEEDED"}})
    )
    assert e504.status_code == 504
    assert e504.severity == "warning"


def test_errors_explain_other_types():
    # Timeout
    e_timeout = errors.explain(TimeoutError("Client timed out waiting for server"))
    assert e_timeout.code == "TIMEOUT"
    assert e_timeout.category == "timeout"
    assert e_timeout.severity == "error"

    # ExtractionParseError
    ExtractionParseError = type("ExtractionParseError", (Exception,), {})
    e_parse = errors.explain(ExtractionParseError("Bad json output from model"))
    assert e_parse.code == "PARSE"
    assert e_parse.category == "parse"
    assert e_parse.severity == "error"

    # MissingRecordingError
    MissingRecordingError = type("MissingRecordingError", (Exception,), {})
    e_replay = errors.explain(MissingRecordingError("No recording found for prompt"))
    assert e_replay.code == "REPLAY_MISSING"
    assert e_replay.category == "config"
    assert e_replay.severity == "error"

    # FileNotFoundError
    e_fnf = errors.explain(FileNotFoundError("No such file or directory: 'cookbook.epub'"))
    assert e_fnf.code == "NOT_FOUND"
    assert e_fnf.category == "input"
    assert e_fnf.severity == "fatal"

    # Unknown exception
    e_unk = errors.explain(ValueError("Unexpected input value"))
    assert e_unk.code == "VALUEERROR"
    assert e_unk.category == "unexpected"
    assert e_unk.severity == "error"


def test_explain_reason():
    # ExtractionParseError reason
    e1 = errors.explain_reason("ExtractionParseError: JSON response was truncated")
    assert e1 is not None
    assert e1.code == "PARSE"
    assert e1.exception_type == "ExtractionParseError"

    # HTTP status code embedded
    e2 = errors.explain_reason("ClientError: 429 RESOURCE_EXHAUSTED: quota exceeded")
    assert e2 is not None
    assert e2.status_code == 429
    assert e2.status == "RESOURCE_EXHAUSTED"

    # Ordinary skip is None
    assert errors.explain_reason("no recipe found in this chapter") is None
    assert errors.explain_reason("page was image only") is None


def test_instrumented_client_records_ok_and_failure(tmp_path):
    store = Store(tmp_path / "webapp.db")
    run_id = store.create_run(
        book_path="/fake/book.epub",
        book_name="book.epub",
        provider="gemini",
        options_json={},
        settings_json={},
    )

    inner = MagicMock()
    client = InstrumentedClient(inner, store, run_id, store_text=True)

    # 1. OK call
    run_context.set_chunk(3, "chunk_3")
    run_context.set_stage("REFINING")

    mock_resp = MagicMock()
    mock_resp.text = '{"title": "Soup"}'
    mock_resp.usage_metadata.prompt_token_count = 120
    mock_resp.usage_metadata.candidates_token_count = 45
    mock_resp.usage_metadata.thoughts_token_count = 15
    mock_resp.usage_metadata.total_token_count = 180
    mock_resp.candidates = [MagicMock(finish_reason="STOP")]
    inner.models.generate_content.return_value = mock_resp

    res = client.models.generate_content(model="gemini-3.1-flash-lite", contents="Refine this recipe")
    assert res == mock_resp

    calls = store.query("SELECT * FROM api_calls WHERE run_id = ?", (run_id,))
    assert len(calls) == 1
    c = calls[0]
    assert c["chunk"] == 3
    assert c["stage"] == "REFINING"
    assert c["ok"] == 1
    assert c["status_code"] == 200
    assert c["prompt_tokens"] == 120
    assert c["output_tokens"] == 45
    assert c["thought_tokens"] == 15
    assert c["total_tokens"] == 180
    assert c["finish_reason"] == "STOP"
    assert c["prompt_text"] == "Refine this recipe"

    # 2. Failed call
    inner.models.generate_content.side_effect = genai_errors.ClientError(
        404, {"error": {"message": "Model gemini-foo not found", "status": "NOT_FOUND"}}
    )

    try:
        client.models.generate_content(model="gemini-foo", contents="Another prompt")
    except genai_errors.ClientError:
        pass

    calls = store.query("SELECT * FROM api_calls WHERE run_id = ? ORDER BY id", (run_id,))
    assert len(calls) == 2
    failed_call = calls[1]
    assert failed_call["ok"] == 0
    assert failed_call["status_code"] == 404
    assert failed_call["status"] == "NOT_FOUND"

    errs = store.query("SELECT * FROM errors WHERE run_id = ?", (run_id,))
    assert len(errs) == 1
    err = errs[0]
    assert err["api_call_id"] == failed_call["id"]
    assert err["code"] == "HTTP 404"
    assert "Settings → Provider & models" in err["fix"]
    assert err["chunk"] == 3
    assert err["stage"] == "REFINING"

    run_context.clear()
