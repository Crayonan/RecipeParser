import ast
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from recipeparser.adapters.webapp import settings
from recipeparser.adapters.webapp.server import create_app


def test_registry_uniqueness():
    reg = settings.registry()
    keys = [s.key for s in reg]
    assert len(keys) == len(set(keys)), f"Duplicate keys in settings registry: {[k for k in keys if keys.count(k) > 1]}"


def test_scan_env_reads_is_subset_of_registry():
    reg_keys = {s.key for s in settings.registry()}
    exempt = {"PYTEST_CURRENT_TEST", "PYTHONUNBUFFERED"}

    repo_root = Path(__file__).resolve().parents[2]
    py_files = list((repo_root / "recipeparser").rglob("*.py"))

    def is_env_obj(node: ast.AST) -> bool:
        if isinstance(node, ast.Name) and node.id in ("env", "environ"):
            return True
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "environ"
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        ):
            return True
        return False

    found_keys = set()
    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # _env_*(...)
                if isinstance(node.func, ast.Name) and node.func.id.startswith("_env_"):
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        found_keys.add(node.args[0].value)
                elif isinstance(node.func, ast.Attribute):
                    # os.getenv(...)
                    if (
                        node.func.attr == "getenv"
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "os"
                    ):
                        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                            found_keys.add(node.args[0].value)
                    # os.environ.get(...) or env.get(...)
                    elif node.func.attr == "get" and is_env_obj(node.func.value):
                        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                            found_keys.add(node.args[0].value)
            # Subscript: os.environ[...] or env[...]
            elif isinstance(node, ast.Subscript):
                if is_env_obj(node.value):
                    if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                        found_keys.add(node.slice.value)
            # Constant assignment: *_ENV = "..."
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id.endswith("ENV")
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    ):
                        found_keys.add(node.value.value)

    unregistered = found_keys - reg_keys - exempt
    assert not unregistered, f"Environment reads found in recipeparser not in registry: {unregistered}"


def test_every_config_env_constant_appears():
    config_settings = settings.parse_config()
    config_keys = {s.key for s in config_settings}
    reg_keys = {s.key for s in settings.registry()}

    assert config_keys.issubset(reg_keys)

    # Specific check on some core constants
    assert "RP_MAX_CHUNK_CHARS" in config_keys
    assert "GEMINI_MODEL" in config_keys
    assert "RP_HTTP_TIMEOUT_SECS" in config_keys


def test_config_reads_rp_override_in_subprocess():
    res = subprocess.run(
        [
            sys.executable,
            "-c",
            "from recipeparser import config; print(config.MAX_CHUNK_CHARS)",
        ],
        env={**os.environ, "RP_MAX_CHUNK_CHARS": "12345"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.stdout.strip() == "12345"


def test_settings_validate_rules():
    # Int rules (RP_REPLAY_LATENCY_MS min 0, max 60000)
    assert settings.validate("RP_REPLAY_LATENCY_MS", "250") is None
    assert settings.validate("RP_REPLAY_LATENCY_MS", "abc") is not None
    assert settings.validate("RP_REPLAY_LATENCY_MS", "12.5") is not None
    assert settings.validate("RP_REPLAY_LATENCY_MS", "-1") is not None
    assert settings.validate("RP_REPLAY_LATENCY_MS", "70000") is not None

    # Float rules (RP_PRICE_INPUT_PER_M min 0)
    assert settings.validate("RP_PRICE_INPUT_PER_M", "0.15") is None
    assert settings.validate("RP_PRICE_INPUT_PER_M", "abc") is not None
    assert settings.validate("RP_PRICE_INPUT_PER_M", "-0.1") is not None

    # Bool rules (RP_STORE_PROMPTS)
    assert settings.validate("RP_STORE_PROMPTS", "true") is None
    assert settings.validate("RP_STORE_PROMPTS", "0") is None
    assert settings.validate("RP_STORE_PROMPTS", "invalid") is not None

    # Choice rules (LOG_LEVEL)
    assert settings.validate("LOG_LEVEL", "DEBUG") is None
    assert settings.validate("LOG_LEVEL", "INFO") is None
    assert settings.validate("LOG_LEVEL", "VERBOSE") is not None

    # Optional int (RP_PDF_PREFLIGHT_MAX_PAGES)
    assert settings.validate("RP_PDF_PREFLIGHT_MAX_PAGES", "none") is None
    assert settings.validate("RP_PDF_PREFLIGHT_MAX_PAGES", "off") is None
    assert settings.validate("RP_PDF_PREFLIGHT_MAX_PAGES", "500") is None
    assert settings.validate("RP_PDF_PREFLIGHT_MAX_PAGES", "abc") is not None

    # Non-editable
    assert settings.validate("RP_WEBAPP_DATA_DIR", "/some/path") is not None

    # Unknown
    assert settings.validate("UNKNOWN_KEY_FOO", "bar") is not None


def test_put_settings_round_trip_via_test_client(tmp_path):
    env_file = tmp_path / ".env"
    app = create_app(tmp_path, env_file, start_monitor=False)
    client = TestClient(app)

    # 1. Update a secret setting and a normal setting
    res = client.put(
        "/api/settings",
        json={"values": {"GOOGLE_API_KEY": "AIzaSySuperSecretKey123", "LOG_LEVEL": "DEBUG"}},
    )
    assert res.status_code == 200

    # Secret went to the env file
    env_content = env_file.read_text(encoding="utf-8")
    assert "GOOGLE_API_KEY=AIzaSySuperSecretKey123" in env_content

    # GET /api/settings masks secrets
    get_res = client.get("/api/settings")
    assert get_res.status_code == 200
    data = get_res.json()["settings"]
    key_setting = next(s for s in data if s["key"] == "GOOGLE_API_KEY")
    assert key_setting["value"] == "••••y123"
    assert key_setting["source"] == ".env"

    log_setting = next(s for s in data if s["key"] == "LOG_LEVEL")
    assert log_setting["value"] == "DEBUG"
    assert log_setting["source"] == "saved"


def test_resolve_priority():
    # priority: default < server env < .env < saved < run
    key = "LOG_LEVEL"  # default is INFO

    # 1. Default only
    res = settings.resolve(saved={}, env_file={}, run_overrides={}, base_env={})
    assert res[key] == ("INFO", "default")

    # 2. Server env overrides default
    res = settings.resolve(saved={}, env_file={}, run_overrides={}, base_env={key: "WARNING"})
    assert res[key] == ("WARNING", "server env")

    # 3. .env overrides server env
    res = settings.resolve(saved={}, env_file={key: "ERROR"}, run_overrides={}, base_env={key: "WARNING"})
    assert res[key] == ("ERROR", ".env")

    # 4. saved overrides .env
    res = settings.resolve(saved={key: "DEBUG"}, env_file={key: "ERROR"}, run_overrides={}, base_env={key: "WARNING"})
    assert res[key] == ("DEBUG", "saved")

    # 5. run overrides saved
    res = settings.resolve(
        saved={key: "DEBUG"},
        env_file={key: "ERROR"},
        run_overrides={key: "INFO"},
        base_env={key: "WARNING"},
    )
    assert res[key] == ("INFO", "run")
