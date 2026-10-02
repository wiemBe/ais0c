"""Acceptance criterion 1: a model built from an alias talks to LiteLLM's chat completions API.

The address and key come from the environment. A local fake LiteLLM shows what a request
carries; no real model is reached.
"""

import asyncio
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

import pytest
from pydantic_ai import Agent, models
from pydantic_ai.settings import ModelSettings

from ais0c_agents import (
    LITELLM_API_KEY_ENV,
    LITELLM_BASE_URL_ENV,
    MODEL_ALIASES,
    ModelAlias,
    ModelConfigError,
    build_model,
)

BASE_URL = "http://litellm.example.com:4000"
ENV = {LITELLM_BASE_URL_ENV: BASE_URL, LITELLM_API_KEY_ENV: "test-key"}


@pytest.mark.parametrize("alias", MODEL_ALIASES)
def test_model_sends_the_alias_to_litellm(alias: ModelAlias) -> None:
    model = build_model(alias, environ=ENV)

    assert model.model_name == alias
    assert model.system == "litellm"
    assert model.base_url == f"{BASE_URL}/v1/"


def test_address_and_key_come_from_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LITELLM_BASE_URL_ENV, "https://litellm.internal.example.com/")
    monkeypatch.setenv(LITELLM_API_KEY_ENV, "env-key")

    assert build_model("soc-fast").base_url == "https://litellm.internal.example.com/v1/"


def test_a_path_prefix_is_kept() -> None:
    env = ENV | {LITELLM_BASE_URL_ENV: "https://gw.example.com/litellm/"}

    assert build_model("soc-report", environ=env).base_url == "https://gw.example.com/litellm/v1/"


def test_settings_apply_to_the_model() -> None:
    settings = ModelSettings(parallel_tool_calls=False)

    assert build_model("soc-fast", settings=settings, environ=ENV).settings == settings


@pytest.mark.parametrize("alias", ["soc-turbo", "SOC-FAST", "fast", ""])
def test_unknown_alias_is_rejected(alias: str) -> None:
    with pytest.raises(ModelConfigError, match="unknown model alias"):
        build_model(cast("ModelAlias", alias), environ=ENV)


@pytest.mark.parametrize("variable", [LITELLM_BASE_URL_ENV, LITELLM_API_KEY_ENV])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_setting_is_rejected(variable: str, value: str | None) -> None:
    env = {name: setting for name, setting in ENV.items() if name != variable}
    if value is not None:
        env[variable] = value

    with pytest.raises(ModelConfigError, match=variable):
        build_model("soc-fast", environ=env)


@pytest.mark.parametrize(
    "url",
    [
        "litellm.example.com:4000",
        "ftp://litellm.example.com",
        "http://",
        "http://user:secret@litellm.example.com",
        "http://litellm.example.com/?key=secret",
        "http://litellm.example.com/#fragment",
        "http://litellm.example.com:4000/v1",
        "http://litellm.example.com:4000/v1/",
    ],
)
def test_invalid_address_is_rejected(url: str) -> None:
    with pytest.raises(ModelConfigError, match=LITELLM_BASE_URL_ENV):
        build_model("soc-fast", environ=ENV | {LITELLM_BASE_URL_ENV: url})


# --- a request against a fake LiteLLM ---------------------------------------------------------


class _FakeLiteLLM(BaseHTTPRequestHandler):
    """Answers every chat completion with the text "pong" and records the request."""

    seen: list[tuple[str, dict[str, str], dict[str, Any]]]

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        headers = {name.lower(): value for name, value in self.headers.items()}
        self.seen.append((self.path, headers, body))
        reply = json.dumps(
            {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1790000000,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 1, "total_tokens": 13},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def fake_litellm(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[str, list[tuple[str, dict[str, str], dict[str, Any]]]]]:
    for variable in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(variable, raising=False)
    seen: list[tuple[str, dict[str, str], dict[str, Any]]] = []
    handler = type("Handler", (_FakeLiteLLM,), {"seen": seen})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_request_reaches_litellm_with_the_alias_key_and_settings(
    fake_litellm: tuple[str, list[tuple[str, dict[str, str], dict[str, Any]]]],
) -> None:
    base_url, seen = fake_litellm
    model = build_model(
        "soc-fast",
        settings=ModelSettings(parallel_tool_calls=False),
        environ={LITELLM_BASE_URL_ENV: base_url, LITELLM_API_KEY_ENV: "test-key"},
    )
    agent = Agent(model, output_type=str)

    @agent.tool_plain
    def get_offense() -> str:
        """Unused; makes the request carry tools."""
        return ""

    with models.override_allow_model_requests(True):
        result = asyncio.run(agent.run("Reply with pong."))

    assert result.output == "pong"
    [(path, headers, body)] = seen
    assert path == "/v1/chat/completions"
    assert headers["authorization"] == "Bearer test-key"
    assert body["model"] == "soc-fast"
    assert body["parallel_tool_calls"] is False
