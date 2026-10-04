"""Starting the gateway from its environment and secret files (settings.py, service.py).

Startup refuses to run with a weaker setup: a short or shared token, a missing MCP endpoint or
token, no database URL. Only profiles with a token file are served (criterion 1).
"""

import secrets
from pathlib import Path

import httpx2
import pytest
from gateway_support import CONFIG_DIR, auth, new_token

from ais0c_mcp_gateway.logs import REDACTED
from ais0c_mcp_gateway.registry import RegistryError
from ais0c_mcp_gateway.service import Service, build_service
from ais0c_mcp_gateway.settings import Settings, SettingsError
from ais0c_storage import ConfigurationError

pytestmark = pytest.mark.anyio

DB_PASSWORD = secrets.token_hex(16)
DATABASE_URL = f"postgresql+psycopg://ais0c_app:{DB_PASSWORD}@127.0.0.1:1/ais0c"
MCP_URL = "http://127.0.0.1:1/mcp"


def write_secret(directory: Path, name: str, value: str) -> str:
    (directory / name).write_text(f"{value}\n", encoding="utf-8")
    return value


def environment(secrets_dir: Path, **changes: str) -> dict[str, str]:
    return {
        "AIS0C_GATEWAY_CONFIG_DIR": str(CONFIG_DIR),
        "AIS0C_GATEWAY_SECRETS_DIR": str(secrets_dir),
        "AIS0C_GATEWAY_UPSTREAM_URL_QRADAR_MCP_READ": MCP_URL,
    } | changes


@pytest.fixture
def secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AIS0C_DATABASE_URL", DATABASE_URL)
    write_secret(tmp_path, "mcp-token-qradar-mcp-read", new_token())
    return tmp_path


def start(secrets_dir: Path, **changes: str) -> Service:
    return build_service(
        Settings.from_env(environment(secrets_dir, **changes)), configure_logs=False
    )


async def test_only_profiles_with_a_token_are_served(secrets_dir: Path) -> None:
    triage = write_secret(secrets_dir, "gateway-token-qradar-triage-read", new_token())
    write_secret(secrets_dir, "gateway-token-qradar-verify-read", new_token())

    service = start(secrets_dir)

    assert set(service.gateway.registry.profiles) == {"qradar-triage-read", "qradar-verify-read"}
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=service.app), base_url="http://gateway.test"
    ) as client:
        served = await client.get("/v1/tools", headers=auth(triage))
        health = await client.get("/healthz")
    assert served.json()["name"] == "qradar-triage-read"
    assert health.json() == {"status": "ok"}


async def test_the_redactor_knows_every_secret(secrets_dir: Path) -> None:
    profile_token = write_secret(secrets_dir, "gateway-token-qradar-triage-read", new_token())
    mcp_token = (secrets_dir / "mcp-token-qradar-mcp-read").read_text(encoding="utf-8").strip()

    service = start(secrets_dir)

    text = f"{profile_token} {mcp_token} {DATABASE_URL}"
    redacted = service.redactor.redact(text)
    assert redacted.count(REDACTED) == 3
    for secret in (profile_token, mcp_token, DB_PASSWORD):
        assert secret not in redacted


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({}, "no profile has a token"),
        ({"gateway-token-qradar-triage-read": "short"}, "at least 32"),
        ({"gateway-token-qradar-triage-read": "has spaces " * 4}, "printable"),
        (
            {
                "gateway-token-qradar-triage-read": "same-token-for-two-profiles-0123456789",
                "gateway-token-qradar-hunt-read": "same-token-for-two-profiles-0123456789",
            },
            "two profiles share a token",
        ),
    ],
)
async def test_weak_or_shared_tokens_stop_startup(
    secrets_dir: Path, files: dict[str, str], message: str
) -> None:
    for name, value in files.items():
        write_secret(secrets_dir, name, value)

    with pytest.raises(SettingsError, match=message):
        start(secrets_dir)


async def test_the_mcp_token_must_differ_from_profile_tokens(secrets_dir: Path) -> None:
    mcp_token = (secrets_dir / "mcp-token-qradar-mcp-read").read_text(encoding="utf-8").strip()
    write_secret(secrets_dir, "gateway-token-qradar-triage-read", mcp_token)

    with pytest.raises(SettingsError, match="also a profile token"):
        start(secrets_dir)


async def test_a_missing_mcp_token_stops_startup(secrets_dir: Path) -> None:
    write_secret(secrets_dir, "gateway-token-qradar-triage-read", new_token())
    (secrets_dir / "mcp-token-qradar-mcp-read").unlink()

    with pytest.raises(SettingsError, match="mcp-token-qradar-mcp-read is missing"):
        start(secrets_dir)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("", "AIS0C_GATEWAY_UPSTREAM_URL_QRADAR_MCP_READ is not set"),
        ("ftp://qradar-mcp-read/mcp", "must be an http"),
    ],
)
async def test_the_mcp_endpoint_is_required(secrets_dir: Path, url: str, message: str) -> None:
    write_secret(secrets_dir, "gateway-token-qradar-triage-read", new_token())

    with pytest.raises(SettingsError, match=message):
        start(secrets_dir, AIS0C_GATEWAY_UPSTREAM_URL_QRADAR_MCP_READ=url)


async def test_a_missing_database_url_stops_startup(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_secret(secrets_dir, "gateway-token-qradar-triage-read", new_token())
    monkeypatch.delenv("AIS0C_DATABASE_URL")

    with pytest.raises(ConfigurationError):
        start(secrets_dir)


async def test_the_note_profile_needs_the_note_instance(secrets_dir: Path) -> None:
    # T-018: the executor's profile runs on qradar-mcp-note, with that instance's own MCP token.
    write_secret(secrets_dir, "gateway-token-qradar-note-write", new_token())

    with pytest.raises(SettingsError, match="mcp-token-qradar-mcp-note is missing"):
        start(secrets_dir)
    write_secret(secrets_dir, "mcp-token-qradar-mcp-note", new_token())
    with pytest.raises(SettingsError, match="AIS0C_GATEWAY_UPSTREAM_URL_QRADAR_MCP_NOTE"):
        start(secrets_dir)

    service = start(
        secrets_dir,
        AIS0C_GATEWAY_UPSTREAM_URL_QRADAR_MCP_NOTE="http://qradar-mcp-note.mcp:5000/mcp",
    )

    assert set(service.gateway.registry.profiles) == {"qradar-note-write"}
    assert set(service.gateway.upstreams) == {"qradar-mcp-note"}


async def test_a_broken_registry_stops_startup(secrets_dir: Path, tmp_path: Path) -> None:
    write_secret(secrets_dir, "gateway-token-qradar-triage-read", new_token())

    with pytest.raises(RegistryError):
        start(secrets_dir, AIS0C_GATEWAY_CONFIG_DIR=str(tmp_path / "nowhere"))


def test_settings_defaults() -> None:
    settings = Settings.from_env({})

    assert settings.config_dir == Path("config")
    assert settings.connectors == ("qradar",)
    assert settings.secrets_dir == Path("/run/secrets")
    assert (settings.host, settings.port) == ("0.0.0.0", 8080)  # noqa: S104
    assert settings.upstream_urls == {}


def test_upstream_urls_come_from_the_environment() -> None:
    settings = Settings.from_env(
        {
            "AIS0C_GATEWAY_UPSTREAM_URL_QRADAR_MCP_READ": " http://qradar-mcp-read:5000/mcp ",
            "AIS0C_GATEWAY_CONNECTORS": "qradar, falcon",
            "AIS0C_GATEWAY_PORT": "9000",
        }
    )

    assert settings.upstream_url("qradar-mcp-read") == "http://qradar-mcp-read:5000/mcp"
    assert settings.connectors == ("qradar", "falcon")
    assert settings.port == 9000
    with pytest.raises(SettingsError, match="PORT"):
        Settings.from_env({"AIS0C_GATEWAY_PORT": "http"})
