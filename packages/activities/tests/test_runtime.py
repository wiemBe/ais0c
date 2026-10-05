"""Building the case worker's runtime from the environment (T-012 criterion 6).

The model comes from the Triage manifest's alias and goes to LiteLLM: the code names no model
provider, and what the alias points to is read from the model registry. A stand-in gateway
serves the profile's tools over HTTP, as `GET /v1/tools` does.
"""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from pydantic_ai.models import Model
from sqlalchemy import URL

from ais0c_activities import GatewayOffenseSource, RuntimeConfigError, load_case_runtime
from ais0c_activities.runtime import model_target, read_token
from ais0c_agents import GatewayUnavailableError, ToolsetProfile, ToolSpec
from ais0c_contracts import CostClass

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY = "config/models/registry.dev.yaml"
TOKEN = "test-gateway-token-0123456789abcdef"  # noqa: S105 - a test value
PROFILE = ToolsetProfile(
    name="qradar-triage-read",
    connector="qradar",
    tools=(
        ToolSpec(
            id="get_offense",
            description="Read one offense.",
            schema_version="1",
            cost_class=CostClass.LOW,
            parameters={"type": "object", "properties": {"offense_id": {"type": "integer"}}},
        ),
    ),
)


class StubGateway:
    """Serves the profile's tool list to the right token; records each request."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str | None]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                authorization = self.headers.get("Authorization")
                stub.requests.append((self.path, authorization))
                if self.path != "/v1/tools" or authorization != f"Bearer {TOKEN}":
                    self.send_error(401)
                    return
                body = PROFILE.model_dump_json().encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def gateway() -> Iterator[StubGateway]:
    stub = StubGateway()
    yield stub
    stub.close()


@pytest.fixture
def environ(database_url: URL, gateway: StubGateway, tmp_path: Path) -> dict[str, str]:
    (tmp_path / "gateway-token-qradar-triage-read").write_text(TOKEN + "\n", encoding="utf-8")
    return {
        "AIS0C_DATABASE_URL": database_url.render_as_string(hide_password=False),
        "AIS0C_GATEWAY_URL": gateway.url,
        "AIS0C_WORKER_SECRETS_DIR": str(tmp_path),
        "AIS0C_WORKER_ROOT": str(REPO_ROOT),
        "AIS0C_MODEL_REGISTRY": REGISTRY,
        "LITELLM_BASE_URL": "http://127.0.0.1:4000",
        "LITELLM_API_KEY": "litellm-test-key",
        "AIS0C_MAX_CONCURRENT_CASES": "3",
    }


async def test_the_runtime_uses_the_manifests_alias_through_litellm(
    environ: dict[str, str], gateway: StubGateway
) -> None:
    runtime = await load_case_runtime(environ)
    try:
        agent = runtime.triage.agent
        model = agent.agent.model
        assert isinstance(model, Model)
        # The alias is the model name LiteLLM gets, at the configured LiteLLM address.
        assert (agent.manifest.model_alias, model.model_name) == ("soc-fast", "soc-fast")
        assert model.base_url == "http://127.0.0.1:4000/v1/"
        # What the alias points to is the registry's business, recorded with each run.
        registry = yaml.safe_load((REPO_ROOT / REGISTRY).read_text(encoding="utf-8"))
        assert runtime.triage.model_target == registry["soc-fast"]["target"]
        # The tools are the gateway's, read with the profile's token.
        assert agent.profile == PROFILE
        assert gateway.requests == [("/v1/tools", f"Bearer {TOKEN}")]
        assert isinstance(runtime.source, GatewayOffenseSource)
        assert runtime.settings.max_concurrent_cases == 3
    finally:
        await runtime.close()


@pytest.mark.parametrize(
    ("variable", "message"),
    [
        ("AIS0C_MODEL_REGISTRY", "AIS0C_MODEL_REGISTRY is not set"),
        ("AIS0C_GATEWAY_URL", "AIS0C_GATEWAY_URL is not set"),
    ],
)
async def test_a_missing_setting_stops_the_runtime(
    environ: dict[str, str], variable: str, message: str
) -> None:
    del environ[variable]

    with pytest.raises(RuntimeConfigError, match=message):
        await load_case_runtime(environ)


@pytest.mark.parametrize("token", ["", "short", "two words " * 5])
async def test_a_weak_or_missing_token_stops_the_runtime(
    environ: dict[str, str], tmp_path: Path, token: str
) -> None:
    secret = tmp_path / "gateway-token-qradar-triage-read"
    if token:
        secret.write_text(token, encoding="utf-8")
    else:
        secret.unlink()

    with pytest.raises(RuntimeConfigError, match="gateway-token-qradar-triage-read") as error:
        await load_case_runtime(environ)
    if token:
        assert token not in str(error.value)


async def test_an_unreachable_gateway_stops_the_runtime(
    environ: dict[str, str], gateway: StubGateway
) -> None:
    gateway.close()

    with pytest.raises(GatewayUnavailableError):
        await load_case_runtime(environ)


def test_the_model_target_comes_from_the_registry(tmp_path: Path) -> None:
    registry = tmp_path / "registry.yaml"
    registry.write_text(json.dumps({"soc-fast": {"target": "example/model-a"}}), encoding="utf-8")

    assert model_target(registry, "soc-fast") == "example/model-a"
    with pytest.raises(RuntimeConfigError, match="no target for soc-report"):
        model_target(registry, "soc-report")


def test_a_token_is_read_without_surrounding_space(tmp_path: Path) -> None:
    secret = tmp_path / "gateway-token-x"
    secret.write_text(f"  {TOKEN}\n", encoding="utf-8")

    assert read_token(secret).get_secret_value() == TOKEN
