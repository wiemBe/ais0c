"""Production-shadow preflight checks use only local stand-ins."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

import ais0c_activities.deploy as deploy
import ais0c_worker.model_release as model_release
from ais0c_activities import load_model_releases
from ais0c_worker import preflight

REPO_ROOT = Path(__file__).resolve().parents[3]
TOKEN_A = "profile-a-token-0123456789abcdef"  # noqa: S105  # gitleaks:allow
TOKEN_B = "profile-b-token-0123456789abcdef"  # noqa: S105  # gitleaks:allow


class FakeServices:
    """A gateway and LiteLLM stand-in on one local HTTP server."""

    def __init__(self) -> None:
        self.profiles = {TOKEN_A: "profile-a", TOKEN_B: "profile-b"}
        self.empty_profile: str | None = None
        self.failing_alias: str | None = None
        self.gateway_requests: list[tuple[str, str]] = []
        self.model_requests: list[dict[str, Any]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/healthz":
                    self._answer({"status": "ok"})
                    return
                if self.path != "/v1/tools":
                    self.send_error(404)
                    return
                authorization = self.headers.get("Authorization", "")
                token = authorization.removeprefix("Bearer ")
                profile = fake.profiles.get(token)
                if profile is None:
                    self.send_error(401)
                    return
                fake.gateway_requests.append((token, profile))
                tools = [] if profile == fake.empty_profile else [{"id": "example_read"}]
                self._answer({"name": profile, "connector": "qradar", "tools": tools})

            def do_POST(self) -> None:
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length))
                fake.model_requests.append(request)
                if request.get("model") == fake.failing_alias:
                    self.send_error(503)
                    return
                self._answer({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

            def _answer(self, body: object) -> None:
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_services() -> Iterator[FakeServices]:
    services = FakeServices()
    try:
        yield services
    finally:
        services.close()


@pytest.fixture
def preflight_env(tmp_path: Path, fake_services: FakeServices) -> dict[str, str]:
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "gateway-token-profile-a").write_text(TOKEN_A, encoding="utf-8")
    (secrets_dir / "gateway-token-profile-b").write_text(TOKEN_B, encoding="utf-8")
    return {
        "AIS0C_DATABASE_URL": "test-database-url",
        "AIS0C_GATEWAY_URL": fake_services.url,
        "AIS0C_WORKER_SECRETS_DIR": str(secrets_dir),
        "AIS0C_WORKER_ROOT": str(REPO_ROOT),
        "AIS0C_MODEL_REGISTRY": "config/models/registry.prod.yaml",
        "AIS0C_SKILLS_MODE": "prod",
        "LITELLM_BASE_URL": fake_services.url,
        "TEMPORAL_ADDRESS": "fake-temporal:7233",
        "TEMPORAL_NAMESPACE": "default",
    }


@pytest.fixture(autouse=True)
def dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy, "database_revision", lambda _url: ("0012", "0012"))
    monkeypatch.setattr(deploy, "writes_enabled", lambda _url: False)
    monkeypatch.setattr(deploy, "approved_skill_count", lambda _root: 1)
    monkeypatch.setattr(model_release, "verify", lambda _r, _l, _e: [])

    async def temporal(_address: str, namespace: str) -> str:
        return namespace

    monkeypatch.setattr(preflight, "temporal_namespace", temporal)


def test_preflight_fails_when_writes_are_enabled(
    preflight_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(deploy, "writes_enabled", lambda _url: True)

    assert preflight.main([], preflight_env) == 1
    assert "FAIL shadow         writes_enabled is on" in capsys.readouterr().out


def test_preflight_fails_shadow_when_the_flag_read_raises(
    preflight_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(_url: str) -> bool:
        raise RuntimeError("synthetic flag read failure")

    monkeypatch.setattr(deploy, "writes_enabled", fail)

    assert preflight.main([], preflight_env) == 1
    output = capsys.readouterr().out
    assert "FAIL shadow" in output
    assert "synthetic flag read failure" in output


def test_preflight_passes_shadow_when_the_flag_row_is_missing(
    preflight_env: dict[str, str],
    fake_services: FakeServices,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert preflight.main([], preflight_env) == 0
    assert "PASS shadow         writes_enabled is off" in capsys.readouterr().out
    assert fake_services.model_requests
    assert all(request["max_tokens"] == 1 for request in fake_services.model_requests)


def test_preflight_fails_in_dev_skills_mode(
    preflight_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    preflight_env["AIS0C_SKILLS_MODE"] = "dev"

    assert preflight.main([], preflight_env) == 1
    assert "FAIL skills_mode" in capsys.readouterr().out


def test_preflight_warns_without_approved_skills(
    preflight_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(deploy, "approved_skill_count", lambda _root: 0)

    assert preflight.main([], preflight_env) == 0
    assert "WARN skills" in capsys.readouterr().out


def test_preflight_fails_when_model_release_verify_fails(
    preflight_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(model_release, "verify", lambda _r, _l, _e: 1)

    assert preflight.main([], preflight_env) == 1
    assert "FAIL model_registry verify exited 1 (H-7)" in capsys.readouterr().out


def test_preflight_fails_on_a_non_prod_registry(
    preflight_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    preflight_env["AIS0C_MODEL_REGISTRY"] = "config/models/registry.dev.yaml"

    assert preflight.main(["--skip-models"], preflight_env) == 1
    assert "FAIL model_registry" in capsys.readouterr().out


def test_preflight_fails_when_a_profile_has_no_tools(
    preflight_env: dict[str, str],
    fake_services: FakeServices,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_services.empty_profile = "profile-b"

    assert preflight.main([], preflight_env) == 1
    output = capsys.readouterr().out
    assert "FAIL gateway" in output
    assert "profile-b has no tools" in output


def test_gateway_check_visits_every_token_file(
    preflight_env: dict[str, str],
    fake_services: FakeServices,
) -> None:
    assert preflight.main([], preflight_env) == 0
    assert fake_services.gateway_requests == list(fake_services.profiles.items())


def test_preflight_fails_when_an_alias_does_not_answer(
    preflight_env: dict[str, str],
    fake_services: FakeServices,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_services.failing_alias = "soc-fast"

    assert preflight.main([], preflight_env) == 1
    output = capsys.readouterr().out
    assert "FAIL models" in output
    assert "soc-fast answered HTTP 503" in output


def test_models_check_calls_every_registry_alias(
    preflight_env: dict[str, str], fake_services: FakeServices
) -> None:
    aliases = load_model_releases(REPO_ROOT / "config/models/registry.prod.yaml")

    assert preflight.main([], preflight_env) == 0

    requested = [request["model"] for request in fake_services.model_requests]
    assert requested == list(aliases)
    assert len(requested) == len(set(requested))
    assert all(request["max_tokens"] == 1 for request in fake_services.model_requests)


def test_models_check_fails_on_an_empty_registry(
    preflight_env: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = tmp_path / "config/models/registry.prod.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("{}\n", encoding="utf-8")
    preflight_env["AIS0C_WORKER_ROOT"] = str(tmp_path)

    assert preflight.main([], preflight_env) == 1
    output = capsys.readouterr().out
    assert "FAIL models" in output
    assert "model registry has no aliases" in output


def test_skip_models_warns(
    preflight_env: dict[str, str],
    fake_services: FakeServices,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert preflight.main(["--skip-models"], preflight_env) == 0
    assert "WARN models         skipped" in capsys.readouterr().out
    assert fake_services.model_requests == []


def test_preflight_json_lists_every_check(
    preflight_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert preflight.main(["--json", "--skip-models"], preflight_env) == 0
    results = json.loads(capsys.readouterr().out)

    assert [result["name"] for result in results] == list(preflight.CHECK_NAMES)
    assert len(results) == 8


def test_preflight_fails_database_when_the_revision_read_raises(
    preflight_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(_url: str) -> tuple[str | None, str]:
        raise RuntimeError("synthetic revision read failure")

    monkeypatch.setattr(deploy, "database_revision", fail)

    assert preflight.main([], preflight_env) == 1
    output = capsys.readouterr().out
    assert "FAIL database" in output
    assert "synthetic revision read failure" in output
