# SPDX-License-Identifier: Apache-2.0
"""Acceptance criterion 7: the token comes from the environment or a secret file and never
appears in log output (or in tool output)."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP

from qradar_mcp.fork.api_version import discover_api_version
from qradar_mcp.fork.app import create_server
from qradar_mcp.fork.logging_setup import configure_logging
from qradar_mcp.fork.redaction import REDACTED
from qradar_mcp.fork.settings import Settings, SettingsError, load_settings
from qradar_mcp.utils import mcp_logger

from .conftest import base_env, make_server
from .fake_qradar import MCP_TOKEN, OFFENSE_ID, QRADAR_TOKEN, FakeQRadar

# --- where the token comes from -----------------------------------------------------------


def test_token_is_read_from_the_environment() -> None:
    settings = load_settings(base_env())
    assert settings.qradar_token.get_secret_value() == QRADAR_TOKEN


def test_token_is_read_from_a_secret_file(tmp_path: Path) -> None:
    secret = tmp_path / "qradar_token"
    secret.write_text(f"{QRADAR_TOKEN}\n", encoding="utf-8")
    env = base_env(QRADAR_AUTH_TOKEN_FILE=str(secret))
    del env["QRADAR_AUTH_TOKEN"]
    assert load_settings(env).qradar_token.get_secret_value() == QRADAR_TOKEN


def test_mcp_token_is_read_from_a_secret_file(tmp_path: Path) -> None:
    secret = tmp_path / "mcp_token"
    secret.write_text(MCP_TOKEN, encoding="utf-8")
    env = base_env(MCP_AUTH_TOKEN_FILE=str(secret))
    del env["MCP_AUTH_TOKEN"]
    assert load_settings(env).mcp_auth_token.get_secret_value() == MCP_TOKEN


def _without(name: str, **overrides: str) -> dict[str, str]:
    env = base_env(**overrides)
    env.pop(name, None)
    return env


@pytest.mark.parametrize(
    ("env", "message"),
    [
        (_without("QRADAR_AUTH_TOKEN"), "QRADAR_AUTH_TOKEN or QRADAR_AUTH_TOKEN_FILE is required"),
        (base_env(QRADAR_AUTH_TOKEN="  "), "is required"),
        (base_env(QRADAR_AUTH_TOKEN_FILE="/run/secrets/x"), "not both"),
        (
            _without("QRADAR_AUTH_TOKEN", QRADAR_AUTH_TOKEN_FILE="/nonexistent/qradar_token"),
            "cannot read",
        ),
        (_without("MCP_AUTH_TOKEN"), "MCP_AUTH_TOKEN or MCP_AUTH_TOKEN_FILE is required"),
        (base_env(MCP_AUTH_TOKEN="too-short"), "at least 32 characters"),
        (base_env(MCP_AUTH_TOKEN=QRADAR_TOKEN), "must differ"),
        (_without("QRADAR_CONSOLE_FQDN"), "QRADAR_CONSOLE_FQDN is required"),
        (base_env(QRADAR_CONSOLE_FQDN="http://qradar.example.com"), "must use https"),
        (base_env(QRADAR_CONSOLE_FQDN="https://user:pw@qradar.example.com"), "host name"),
        (base_env(QRADAR_CONSOLE_FQDN="qradar.example.com/api"), "host name"),
        (base_env(QRADAR_VERIFY_SSL="maybe"), "true or false"),
        (base_env(REQUESTS_CA_BUNDLE="/nonexistent/ca.pem"), "does not point to a file"),
        (base_env(QRADAR_API_VERSIONS="27.0,latest"), "invalid version"),
        (base_env(MCP_HTTPX_TIMEOUT="0"), "positive"),
        (base_env(LOG_LEVEL="CHATTY"), "LOG_LEVEL"),
    ],
)
def test_unusable_configuration_is_refused(env: dict[str, str], message: str) -> None:
    with pytest.raises(SettingsError, match=message) as raised:
        load_settings(env)
    assert QRADAR_TOKEN not in str(raised.value)
    assert MCP_TOKEN not in str(raised.value)


def test_unreadable_secret_file_error_does_not_contain_the_secret(tmp_path: Path) -> None:
    directory = tmp_path / "secret_dir"
    directory.mkdir()
    env = _without("QRADAR_AUTH_TOKEN", QRADAR_AUTH_TOKEN_FILE=str(directory))
    with pytest.raises(SettingsError, match="cannot read QRADAR_AUTH_TOKEN_FILE"):
        load_settings(env)


def test_console_url_forms_are_normalised() -> None:
    for value in ("qradar.example.com", "https://qradar.example.com/", "qradar.example.com/"):
        assert (
            load_settings(base_env(QRADAR_CONSOLE_FQDN=value)).console_host == "qradar.example.com"
        )
    with_port = load_settings(base_env(QRADAR_CONSOLE_FQDN="https://qradar.example.com:8443"))
    assert with_port.console_host == "qradar.example.com:8443"


def test_tls_verification_is_on_unless_disabled() -> None:
    assert load_settings(base_env()).ssl_verify() is True
    assert load_settings(base_env(QRADAR_VERIFY_SSL="false")).ssl_verify() is False


def test_settings_repr_does_not_show_secrets() -> None:
    settings = load_settings(base_env())
    for text in (repr(settings), str(settings)):
        assert QRADAR_TOKEN not in text
        assert MCP_TOKEN not in text


# --- logs ----------------------------------------------------------------------------------


@pytest.fixture
def isolated_logging() -> Iterator[None]:
    """configure_logging() changes process-wide state; put it back afterwards."""
    loggers = {
        name: (logger.handlers[:], logger.propagate, logger.level)
        for name, logger in logging.Logger.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    root = logging.getLogger()
    root_state = (root.handlers[:], root.level)
    upstream_state = (mcp_logger.MCPLogger._instance, mcp_logger._MCP_LOGGER)
    yield
    for name, logger in logging.Logger.manager.loggerDict.items():
        if not isinstance(logger, logging.Logger):
            continue
        handlers, propagate, level = loggers.get(name, ([], True, logging.NOTSET))
        logger.handlers, logger.propagate, logger.level = handlers, propagate, level
    root.handlers, root.level = root_state
    mcp_logger.MCPLogger._instance, mcp_logger._MCP_LOGGER = upstream_state


async def _exercise(server: FastMCP) -> None:
    async with Client(server) as client:
        await client.call_tool("get_offense", {"offense_id": OFFENSE_ID})
        await client.call_tool("list_offenses", {"limit": 5})
        # Errors: QRadar 404 whose message reflects the SEC header, and invalid arguments.
        await client.call_tool("get_offense", {"offense_id": 999}, raise_on_error=False)
        await client.call_tool("get_offense", {"offense_id": "x"}, raise_on_error=False)
        await client.call_tool(
            "delete_ariel_search", {"search_id": "not-ours"}, raise_on_error=False
        )


@pytest.mark.usefixtures("isolated_logging")
def test_log_output_never_contains_a_token(capfd: pytest.CaptureFixture[str]) -> None:
    settings = load_settings(base_env(LOG_LEVEL="DEBUG"))
    configure_logging("DEBUG", settings.secrets())
    fake = FakeQRadar(reflect_token_in_errors=True)

    selected = discover_api_version(settings, transport=fake.transport())
    server = create_server("qradar-read", settings, selected.version, transport=fake.transport())
    asyncio.run(_exercise(server))
    # Anything that logs a secret by mistake, in a message, an argument or a traceback.
    logging.getLogger("some.library").warning("token=%s", QRADAR_TOKEN)
    logging.getLogger("qradar-mcp").info("inbound token %s", MCP_TOKEN)
    try:
        raise RuntimeError(f"request failed with SEC {QRADAR_TOKEN}")
    except RuntimeError:
        logging.getLogger("fastmcp.server").exception("unexpected error")

    out, err = capfd.readouterr()
    output = out + err
    assert QRADAR_TOKEN not in output
    assert MCP_TOKEN not in output
    assert REDACTED in output
    # The redacting handler really carried upstream, FastMCP and fork logs.
    assert "Tool execution started: get_offense" in output
    assert "Error calling tool" in output
    assert "Refused to delete Ariel search not-ours" in output


@pytest.mark.asyncio
async def test_a_reflected_token_is_scrubbed_from_tool_errors(settings: Settings) -> None:
    fake = FakeQRadar(reflect_token_in_errors=True)
    server = make_server("qradar-read", fake, settings)
    async with Client(server) as client:
        result = await client.call_tool("get_offense", {"offense_id": 999}, raise_on_error=False)
    assert result.is_error
    assert QRADAR_TOKEN not in result.content[0].text
    assert REDACTED in result.content[0].text


def test_failed_startup_never_prints_the_token(tmp_path: Path) -> None:
    """The real entry point, from settings to logging, with the token in a secret file."""
    token_file = tmp_path / "qradar_token"
    token_file.write_text(f"{QRADAR_TOKEN}\n", encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "QRADAR_CONSOLE_FQDN": "127.0.0.1:9",
        "QRADAR_AUTH_TOKEN_FILE": str(token_file),
        "MCP_AUTH_TOKEN": MCP_TOKEN,
        "LOG_LEVEL": "DEBUG",
        "MCP_HTTPX_TIMEOUT": "5",
    }
    completed = subprocess.run(
        [sys.executable, "-m", "qradar_mcp.fork", "--profile", "qradar-read"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 1
    assert "Refusing to start: cannot reach QRadar" in completed.stderr
    assert QRADAR_TOKEN not in completed.stdout + completed.stderr
    assert MCP_TOKEN not in completed.stdout + completed.stderr
