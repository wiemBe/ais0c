"""The LiteLLM smoke script, deploy/compose/smoke_litellm.py (T-003 acceptance criterion 4).

The script runs as a subprocess against a fake LiteLLM server on 127.0.0.1, so these tests
never reach a model. test_dev_stack.py runs it against the real dev stack.
"""

import json
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy/compose/smoke_litellm.py"
SETTINGS = ("OPENROUTER_API_KEY", "LITELLM_MASTER_KEY", "LITELLM_BASE_URL")
MASTER_KEY = "sk-test-master-key"
OPENROUTER_KEY = "placeholder-openrouter-key"


def completion(content: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "soc-fast",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 1, "total_tokens": 13},
    }


@dataclass
class Recorded:
    path: str
    headers: dict[str, str]
    body: dict[str, Any]


@dataclass
class FakeLiteLLM:
    """Answers every POST with `status` and `reply`, and records the requests."""

    url: str = ""
    status: int = 200
    reply: dict[str, Any] = field(default_factory=lambda: completion("pong"))
    requests: list[Recorded] = field(default_factory=list)


@pytest.fixture
def fake_litellm() -> Iterator[FakeLiteLLM]:
    fake = FakeLiteLLM()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            fake.requests.append(Recorded(self.path, dict(self.headers), body))
            data = json.dumps(fake.reply).encode()
            self.send_response(fake.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            """Keep the access log out of the test output."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake.url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def run_script(settings: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the script with only the given LiteLLM and OpenRouter settings."""
    env = {key: value for key, value in os.environ.items() if key not in SETTINGS} | settings
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def ready_settings(fake: FakeLiteLLM) -> dict[str, str]:
    return {
        "OPENROUTER_API_KEY": OPENROUTER_KEY,
        "LITELLM_MASTER_KEY": MASTER_KEY,
        "LITELLM_BASE_URL": fake.url,
    }


@pytest.mark.parametrize("openrouter_key", [None, ""], ids=["unset", "empty"])
def test_skips_without_an_openrouter_key(
    fake_litellm: FakeLiteLLM, openrouter_key: str | None
) -> None:
    settings = {"LITELLM_MASTER_KEY": MASTER_KEY, "LITELLM_BASE_URL": fake_litellm.url}
    if openrouter_key is not None:
        settings["OPENROUTER_API_KEY"] = openrouter_key

    result = run_script(settings)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("SKIPPED: OPENROUTER_API_KEY is not set")
    assert fake_litellm.requests == []


def test_sends_one_short_request_to_soc_fast(fake_litellm: FakeLiteLLM) -> None:
    result = run_script(ready_settings(fake_litellm))

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("OK: soc-fast answered")
    assert "'pong'" in result.stdout
    [request] = fake_litellm.requests
    assert request.path == "/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {MASTER_KEY}"
    assert request.body["model"] == "soc-fast"
    assert len(request.body["messages"]) == 1
    # LiteLLM holds the OpenRouter key; the script never sends it.
    assert OPENROUTER_KEY not in json.dumps([request.headers, request.body])


def test_fails_when_litellm_answers_with_an_error(fake_litellm: FakeLiteLLM) -> None:
    fake_litellm.status = 401
    fake_litellm.reply = {"error": {"message": "Authentication Error"}}

    result = run_script(ready_settings(fake_litellm))

    assert result.returncode == 1
    assert result.stderr.startswith("FAILED: LiteLLM answered HTTP 401")


def test_fails_when_the_answer_is_empty(fake_litellm: FakeLiteLLM) -> None:
    fake_litellm.reply = completion("")

    result = run_script(ready_settings(fake_litellm))

    assert result.returncode == 1
    assert result.stderr.startswith("FAILED: the response has no answer text")


def test_fails_without_the_master_key(fake_litellm: FakeLiteLLM) -> None:
    settings = ready_settings(fake_litellm)
    del settings["LITELLM_MASTER_KEY"]

    result = run_script(settings)

    assert result.returncode == 1
    assert result.stderr.startswith("FAILED: LITELLM_MASTER_KEY is not set")
    assert fake_litellm.requests == []


def test_rejects_a_base_url_that_is_not_http(fake_litellm: FakeLiteLLM) -> None:
    settings = ready_settings(fake_litellm) | {"LITELLM_BASE_URL": "file:///etc/passwd"}

    result = run_script(settings)

    assert result.returncode == 1
    assert result.stderr.startswith("FAILED: LITELLM_BASE_URL is not an http(s) URL")


def test_fails_when_litellm_is_unreachable(fake_litellm: FakeLiteLLM) -> None:
    with socket.socket() as probe:  # a port that was free a moment ago and has no listener
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = ready_settings(fake_litellm) | {"LITELLM_BASE_URL": f"http://127.0.0.1:{port}"}

    result = run_script(settings)

    assert result.returncode == 1
    assert result.stderr.startswith("FAILED:")
