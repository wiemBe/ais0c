"""`python -m ais0c_worker.model_release verify` against stand-in vLLM servers (T-016 criterion 4).

Each stand-in serves `/v1/models` and `/version` as vLLM does. The registries and LiteLLM
configurations are written per test, except in the test that checks the repository's own prod
files; model names are synthetic.
"""

import json
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

from ais0c_worker.model_release import main

REPO_ROOT = Path(__file__).resolve().parents[3]
API_KEY = "vllm-test-key-0123456789"  # gitleaks:allow - a synthetic test value
VERSION = "0.11.2"


def card(name: str, *, root: str | None = None, max_model_len: int = 131072) -> dict[str, Any]:
    """A model card as vLLM's /v1/models lists it."""
    return {
        "id": name,
        "object": "model",
        "created": 1790000000,
        "owned_by": "vllm",
        "root": name if root is None else root,
        "parent": None,
        "max_model_len": max_model_len,
        "permission": [{"id": "modelperm-1", "object": "model_permission"}],
    }


class FakeVllm:
    """Serves /v1/models and /version; with `api_key`, /v1 asks for it, as `vllm --api-key`
    does. `answer`, when set, replaces every answer. Records the path and Authorization header
    of each request."""

    def __init__(
        self,
        models: list[dict[str, Any]],
        *,
        version: str = VERSION,
        api_key: str | None = None,
        answer: object = None,
    ) -> None:
        self.models = models
        self.version = version
        self.api_key = api_key
        self.answer = answer
        self.requests: list[tuple[str, str | None]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                authorization = self.headers.get("Authorization")
                fake.requests.append((self.path, authorization))
                body: object
                if self.path == "/version":
                    body = {"version": fake.version}
                elif self.path == "/v1/models":
                    if fake.api_key is not None and authorization != f"Bearer {fake.api_key}":
                        self.send_error(401)
                        return
                    body = {"object": "list", "data": fake.models}
                else:
                    self.send_error(404)
                    return
                data = json.dumps(body if fake.answer is None else fake.answer).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

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
def servers() -> Iterator[list[FakeVllm]]:
    """Stand-ins the test starts; each one is closed at the end."""
    started: list[FakeVllm] = []
    yield started
    for server in started:
        server.close()


def entry(model: str, *, max_context: int = 131072, **changes: object) -> dict[str, object]:
    """A prod registry entry whose release matches a stand-in serving `model`."""
    return {
        "target": f"hosted_vllm/{model}",
        "prod_equivalent": f"hosted_vllm/{model}",
        "capabilities": ["tool_calling", "structured_output"],
        "parallel_tool_calls": False,
        "forced_tool_choice": True,
        "context_window": max_context,
        "tool_parser": "example_parser",
        "reasoning_parser": None,
        "turkish_quality": None,
        "artifact": model,
        "artifact_hash": "3f1c2a9e8b7d6c5b4a39281706f5e4d3c2b1a090",
        "quantization": "fp8",
        "tokenizer": f"{model} sha256:{'ab' * 32}",
        "engine_version": VERSION,
        "inference_params": {},
    } | changes


class Setup:
    """A registry and a LiteLLM configuration in `root`, and the environment that names the
    servers: soc-fast on server A (with an API key), soc-verifier on server B."""

    def __init__(self, root: Path, a: FakeVllm, b: FakeVllm) -> None:
        self.root = root
        self.registry = root / "config/models/registry.prod.yaml"
        self.litellm = root / "config/litellm/litellm.prod.yaml"
        self.environ = {
            "AIS0C_WORKER_ROOT": str(root),
            "TEST_VLLM_A_API_BASE": f"{a.url}/v1",
            "TEST_VLLM_A_API_KEY": API_KEY,
            "TEST_VLLM_B_API_BASE": f"{b.url}/v1/",
        }
        self.entries: dict[str, dict[str, Any]] = {
            "soc-fast": entry("example-org/Model-A"),
            "soc-verifier": entry("example-org/Model-B", max_context=262144),
        }
        self.models: list[dict[str, Any]] = [
            {
                "model_name": "soc-fast",
                "litellm_params": {
                    "model": "hosted_vllm/example-org/Model-A",
                    "api_base": "os.environ/TEST_VLLM_A_API_BASE",
                    "api_key": "os.environ/TEST_VLLM_A_API_KEY",
                },
            },
            {
                "model_name": "soc-verifier",
                "litellm_params": {
                    "model": "hosted_vllm/example-org/Model-B",
                    "api_base": "os.environ/TEST_VLLM_B_API_BASE",
                    "api_key": "os.environ/TEST_VLLM_B_API_KEY",
                },
            },
        ]

    def write(self) -> None:
        self.registry.parent.mkdir(parents=True, exist_ok=True)
        self.litellm.parent.mkdir(parents=True, exist_ok=True)
        self.registry.write_text(yaml.safe_dump(self.entries), encoding="utf-8")
        self.litellm.write_text(yaml.safe_dump({"model_list": self.models}), encoding="utf-8")


@pytest.fixture
def server_a(servers: list[FakeVllm]) -> FakeVllm:
    server = FakeVllm([card("example-org/Model-A")], api_key=API_KEY)
    servers.append(server)
    return server


@pytest.fixture
def server_b(servers: list[FakeVllm]) -> FakeVllm:
    server = FakeVllm([card("example-org/Model-B", max_model_len=262144)])
    servers.append(server)
    return server


@pytest.fixture
def setup(tmp_path: Path, server_a: FakeVllm, server_b: FakeVllm) -> Setup:
    return Setup(tmp_path, server_a, server_b)


def run(setup: Setup, capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, list[str]]:
    setup.write()
    status = main(["verify", *args], setup.environ)
    return status, capsys.readouterr().out.splitlines()


def test_a_registry_that_matches_its_servers_passes(
    setup: Setup,
    server_a: FakeVllm,
    server_b: FakeVllm,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status, out = run(setup, capsys)

    assert status == 0
    assert out[-1] == "The registry matches the servers."
    assert "read on the server by hand: quantization, tokenizer, tool_parser" in out[0]
    # Each server is asked once for each endpoint, with its key where LiteLLM has one.
    assert server_a.requests == [
        ("/v1/models", f"Bearer {API_KEY}"),
        ("/version", f"Bearer {API_KEY}"),
    ]
    assert server_b.requests == [("/v1/models", None), ("/version", None)]


def test_each_difference_is_listed_and_the_status_is_1(
    setup: Setup,
    server_a: FakeVllm,
    server_b: FakeVllm,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server_a.models = [card("example-org/Model-A", root="/models/model-a-awq", max_model_len=65536)]
    server_a.version = "0.12.0"
    server_b.models = [card("example-org/Model-C")]

    status, out = run(setup, capsys)

    assert status == 1
    assert out[:-2] == [
        f"soc-fast at {server_a.url}: artifact: registry 'example-org/Model-A', "
        "server '/models/model-a-awq'",
        f"soc-fast at {server_a.url}: max_context: registry 131072, server 65536",
        f"soc-fast at {server_a.url}: engine_version: registry '0.11.2', server '0.12.0'",
        f"soc-verifier at {server_b.url}: the server does not serve 'example-org/Model-B'; "
        "it serves ['example-org/Model-C']",
    ]
    assert out[-1] == "The registry differs from the servers in 4 place(s)."


def test_an_unknown_value_in_the_registry_is_a_difference(
    setup: Setup, server_a: FakeVllm, capsys: pytest.CaptureFixture[str]
) -> None:
    """A null that the server can answer is reported with the server's value, to record."""
    setup.entries["soc-fast"]["engine_version"] = None

    status, out = run(setup, capsys)

    assert status == 1
    assert out[0] == f"soc-fast at {server_a.url}: engine_version: registry None, server '0.11.2'"


def test_a_release_without_artifact_hash_is_reported(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    setup.entries["soc-verifier"]["artifact_hash"] = None

    status, out = run(setup, capsys)

    assert status == 1
    assert out[0] == "soc-verifier: artifact_hash is not recorded; contracts.md requires it in prod"


def test_litellm_sending_the_alias_elsewhere_is_reported(
    setup: Setup, server_a: FakeVllm, capsys: pytest.CaptureFixture[str]
) -> None:
    setup.models[0]["litellm_params"]["model"] = "hosted_vllm/example-org/Model-A-Next"

    status, out = run(setup, capsys)

    assert status == 1
    assert out[:2] == [
        f"soc-fast at {server_a.url}: target: registry 'hosted_vllm/example-org/Model-A', "
        "LiteLLM 'hosted_vllm/example-org/Model-A-Next'",
        f"soc-fast at {server_a.url}: the server does not serve 'example-org/Model-A-Next'; "
        "it serves ['example-org/Model-A']",
    ]


def test_every_deployment_of_an_alias_is_checked(
    setup: Setup,
    servers: list[FakeVllm],
    server_a: FakeVllm,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """LiteLLM may balance an alias over several servers; an older one is found too."""
    replica = FakeVllm([card("example-org/Model-A")], version="0.10.1")
    servers.append(replica)
    setup.environ["TEST_VLLM_A2_API_BASE"] = replica.url
    second = {
        "model_name": "soc-fast",
        "litellm_params": {
            "model": "hosted_vllm/example-org/Model-A",
            "api_base": "os.environ/TEST_VLLM_A2_API_BASE",
        },
    }
    setup.models.append(second)

    status, out = run(setup, capsys)

    assert status == 1
    assert (
        out[0] == f"soc-fast at {replica.url}: engine_version: registry '0.11.2', server '0.10.1'"
    )
    assert replica.requests == [("/v1/models", None), ("/version", None)]


def test_a_server_that_cannot_be_read_is_reported_and_the_others_still_checked(
    setup: Setup,
    server_a: FakeVllm,
    server_b: FakeVllm,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server_a.close()
    server_b.answer = {"unexpected": True}

    status, out = run(setup, capsys)

    assert status == 1
    assert out[0].startswith(f"soc-fast at {server_a.url}: cannot read {server_a.url}/v1/models:")
    assert out[1] == (
        f"soc-verifier at {server_b.url}: {server_b.url}/v1/models does not answer as vLLM does"
    )


def test_a_wrong_api_key_is_a_server_that_cannot_be_read(
    setup: Setup, server_a: FakeVllm, capsys: pytest.CaptureFixture[str]
) -> None:
    setup.environ["TEST_VLLM_A_API_KEY"] = "another-key-0123456789"

    status, out = run(setup, capsys)

    assert status == 1
    assert "401" in out[0]
    assert API_KEY not in "\n".join(out)


def test_an_alias_litellm_does_not_know_is_reported(
    setup: Setup, capsys: pytest.CaptureFixture[str]
) -> None:
    del setup.models[1]

    status, out = run(setup, capsys)

    assert status == 1
    assert out[0] == f"soc-verifier: {setup.litellm} has no deployment for it"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda s: s.environ.pop("TEST_VLLM_B_API_BASE"), "TEST_VLLM_B_API_BASE is not set"),
        (
            lambda s: s.environ.update(TEST_VLLM_B_API_BASE="vllm-b.example.com:8000/v1"),
            "the api_base of soc-verifier must be an http(s) URL",
        ),
        (
            lambda s: s.models[1]["litellm_params"].pop("api_base"),
            "the LiteLLM configuration gives soc-verifier no api_base",
        ),
        (
            lambda s: s.models[1].pop("litellm_params"),
            "a model_list entry lacks model_name or litellm_params",
        ),
        (lambda s: s.entries["soc-fast"].pop("artifact"), "invalid model registry"),
    ],
    ids=["unset-variable", "not-a-url", "no-api-base", "no-params", "no-artifact"],
)
def test_a_broken_input_stops_the_check_before_any_request(
    setup: Setup,
    server_a: FakeVllm,
    server_b: FakeVllm,
    capsys: pytest.CaptureFixture[str],
    change: Callable[[Setup], object],
    message: str,
) -> None:
    change(setup)
    setup.write()

    status = main(["verify"], setup.environ)

    assert status == 2
    assert message in capsys.readouterr().err
    assert server_a.requests == server_b.requests == []


def test_a_non_positive_timeout_is_a_usage_error(setup: Setup) -> None:
    setup.write()

    with pytest.raises(SystemExit) as stopped:
        main(["verify", "--timeout", "0"], setup.environ)
    assert stopped.value.code == 2


def test_the_command_runs_as_a_module_on_the_default_files(setup: Setup) -> None:
    setup.write()

    result = subprocess.run(
        [sys.executable, "-m", "ais0c_worker.model_release", "verify"],
        env=setup.environ,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[-1] == "The registry matches the servers."


def test_the_repositorys_prod_registry_lacks_only_what_the_servers_give(
    servers: list[FakeVllm], capsys: pytest.CaptureFixture[str]
) -> None:
    """The repository's prod registry against stand-ins that serve each model as the registry
    describes it, at the addresses the repository's LiteLLM configuration names. What is left
    is what the deployment must record: artifact_hash and engine_version of every alias."""
    registry = yaml.safe_load(
        (REPO_ROOT / "config/models/registry.prod.yaml").read_text(encoding="utf-8")
    )
    litellm = yaml.safe_load(
        (REPO_ROOT / "config/litellm/litellm.prod.yaml").read_text(encoding="utf-8")
    )
    environ = {"AIS0C_WORKER_ROOT": str(REPO_ROOT)}
    by_variable: dict[str, FakeVllm] = {}
    server_of: dict[str, FakeVllm] = {}
    for deployment in litellm["model_list"]:
        params = deployment["litellm_params"]
        variable = params["api_base"].removeprefix("os.environ/")
        described = registry[deployment["model_name"]]
        if variable not in by_variable:
            by_variable[variable] = FakeVllm([])
            servers.append(by_variable[variable])
        server = server_of[deployment["model_name"]] = by_variable[variable]
        name = params["model"].partition("/")[2]
        if all(model["id"] != name for model in server.models):
            server.models.append(
                card(name, root=described["artifact"], max_model_len=described["context_window"])
            )
        environ[variable] = f"{server.url}/v1"

    status = main(["verify"], environ)

    out = capsys.readouterr().out.splitlines()
    assert status == 1
    assert out[:-2] == [
        line
        for alias in registry
        for line in (
            f"{alias}: artifact_hash is not recorded; contracts.md requires it in prod",
            f"{alias} at {server_of[alias].url}: engine_version: registry None, server '{VERSION}'",
        )
    ]
