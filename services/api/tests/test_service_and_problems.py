"""Criterion 1: the service, its settings and the shape of its answers (T-028, T-63).

- `python -m ais0c_api` starts the service; the settings are the environment variables
  `AIS0C_API_AUTH` and the rest of the table in `ais0c_api.settings`;
- without `AIS0C_API_AUTH`, or with a value this build does not know, the process exits with
  status 2 without binding a port;
- errors are RFC 9457 problems whose `title` is a machine-readable code;
- lists are cursor-paged: two pages, `next_cursor` null on the last one, a bad cursor a 400;
- times are ISO 8601 and UTC.
"""

import logging
from datetime import timedelta
from pathlib import Path

import pytest
from api_support import (
    CASE_ID,
    T0,
    T1,
    DevUsersFile,
    Harness,
    decided_case,
    open_case,
)
from sqlalchemy import URL

from ais0c_api.__main__ import EXIT_CONFIG_ERROR, main
from ais0c_api.problems import PROBLEM_MEDIA_TYPE
from ais0c_api.service import ServiceError, build_service
from ais0c_api.settings import AuthMode, Settings, SettingsError

pytestmark = pytest.mark.anyio

PROBLEM_JSON = "application/problem+json"

# --- settings and start-up (criterion 1 and 2) ----------------------------------------------------


def test_the_settings_come_from_the_environment(tmp_path: Path) -> None:
    users = DevUsersFile.write(tmp_path)

    settings = Settings.from_env(
        {
            "AIS0C_API_AUTH": "dev",
            "AIS0C_API_DEV_USERS_FILE": str(users.path),
        }
    )

    assert settings.auth_mode is AuthMode.DEV
    assert settings.dev_users_file == users.path
    # The defaults of the table in the module docstring.
    assert (settings.host, settings.port) == ("127.0.0.1", 8000)
    assert settings.temporal_target() == ("127.0.0.1:7233", "default")


def test_the_host_and_port_and_temporal_are_read_from_the_environment(tmp_path: Path) -> None:
    users = DevUsersFile.write(tmp_path)

    settings = Settings.from_env(
        {
            "AIS0C_API_AUTH": "dev",
            "AIS0C_API_DEV_USERS_FILE": str(users.path),
            "AIS0C_API_HOST": "0.0.0.0",  # noqa: S104  # the value under test, not a default
            "AIS0C_API_PORT": "9001",
            "TEMPORAL_ADDRESS": "temporal.test:7233",
            "TEMPORAL_NAMESPACE": "soc",
        }
    )

    assert (settings.host, settings.port) == ("0.0.0.0", 9001)  # noqa: S104
    assert settings.temporal_target() == ("temporal.test:7233", "soc")


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({}, "AIS0C_API_AUTH is not set"),
        ({"AIS0C_API_AUTH": ""}, "is not set"),
        ({"AIS0C_API_AUTH": "oidc"}, "not a known authentication mode"),
        ({"AIS0C_API_AUTH": "dev"}, "needs AIS0C_API_DEV_USERS_FILE"),
        (
            {"AIS0C_API_AUTH": "dev", "AIS0C_API_DEV_USERS_FILE": "x", "AIS0C_API_PORT": "http"},
            "must be a port number",
        ),
        (
            {"AIS0C_API_AUTH": "dev", "AIS0C_API_DEV_USERS_FILE": "x", "AIS0C_API_PORT": "70000"},
            "between 1 and 65535",
        ),
    ],
    ids=["unset", "blank", "unknown", "no-users-file", "port", "port-range"],
)
def test_an_unusable_setting_stops_the_service(environ: dict[str, str], message: str) -> None:
    with pytest.raises(SettingsError, match=message):
        Settings.from_env(environ)


def test_the_service_refuses_to_start_without_an_auth_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 2: `AIS0C_API_AUTH` yokken başlatma hatası. Nothing binds a port."""
    monkeypatch.delenv("AIS0C_API_AUTH", raising=False)

    assert main([]) == EXIT_CONFIG_ERROR
    assert "AIS0C_API_AUTH is not set" in capsys.readouterr().err


def test_the_service_refuses_an_unknown_auth_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIS0C_API_AUTH", "password")

    with pytest.raises(ServiceError, match="not a known authentication mode"):
        build_service()


def test_the_service_refuses_a_users_file_it_cannot_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AIS0C_API_AUTH", "dev")
    monkeypatch.setenv("AIS0C_API_DEV_USERS_FILE", str(tmp_path / "missing.json"))

    with pytest.raises(ServiceError, match="is missing"):
        build_service()


def test_dev_mode_is_logged_as_a_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    database_url: URL,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Criterion 2: API açılışta `dev` modunda olduğunu uyarı olarak loglar."""
    monkeypatch.setenv("AIS0C_API_AUTH", "dev")
    monkeypatch.setenv("AIS0C_API_DEV_USERS_FILE", str(DevUsersFile.write(tmp_path).path))
    monkeypatch.setenv("AIS0C_DATABASE_URL", database_url.render_as_string(hide_password=False))

    with caplog.at_level(logging.WARNING, logger="ais0c.api"):
        service = build_service()

    assert service.settings.auth_mode is AuthMode.DEV
    assert any(
        record.levelno == logging.WARNING and "development only" in record.getMessage()
        for record in caplog.records
    )


# --- errors, pagination and times (criterion 1) ---------------------------------------------------


async def test_an_unknown_path_and_method_are_problems(api: Harness) -> None:
    missing = await api.raw("GET", "/api/v1/does-not-exist")
    wrong_method = await api.raw("DELETE", "/api/v1/cases")

    for response in (missing, wrong_method):
        assert response.headers["content-type"].startswith(PROBLEM_JSON)
        assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert missing.json()["title"] == "request.not_found"
    assert wrong_method.json()["title"] == "request.method_not_allowed"


async def test_a_body_that_is_not_the_contract_is_a_422_with_field_errors(api: Harness) -> None:
    await open_case(api.sessions)

    response = await api.post(f"/cases/{CASE_ID}/feedback", {"case_id": CASE_ID, "verdict": "tp"})

    body = response.json()
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert body["title"] == "request.invalid"
    # The rejected values are not echoed back.
    assert all("input" not in error for error in body["errors"])
    assert any(error["field"] == "body.reason" for error in body["errors"])


async def test_a_query_value_that_is_not_an_enum_is_a_422(api: Harness) -> None:
    response = await api.get("/cases", status="not-a-status")

    assert response.status_code == 422
    assert response.json()["title"] == "request.invalid"


async def test_a_page_hands_out_a_cursor_and_the_last_page_does_not(api: Harness) -> None:
    for index in (1, 2, 3):
        await open_case(
            api.sessions,
            case_id=f"case-{index}",
            sla_due_at=T0 + timedelta(minutes=index),
            with_report=False,
        )

    first = await api.get("/cases", limit=2)
    second = await api.get("/cases", limit=2, cursor=first.json()["next_cursor"])
    # The last page says there is no more; following its (absent) cursor lists nothing.
    last = await api.get("/cases", limit=2, cursor=second.json()["next_cursor"])

    assert first.status_code == 200
    assert [item["case_id"] for item in first.json()["items"]] == ["case-3", "case-2"]
    assert first.json()["next_cursor"] is not None
    assert [item["case_id"] for item in second.json()["items"]] == ["case-1"]
    assert second.json()["next_cursor"] is None
    # A request that names no cursor starts from the beginning, so the page repeats.
    assert [item["case_id"] for item in last.json()["items"]] == ["case-3", "case-2"]


async def test_the_default_page_size_is_fifty(api: Harness) -> None:
    for index in range(51):
        await open_case(
            api.sessions,
            case_id=f"case-{index}",
            sla_due_at=T0 + timedelta(minutes=index),
            with_report=False,
        )

    response = await api.get("/cases")

    assert len(response.json()["items"]) == 50
    assert response.json()["next_cursor"] is not None


@pytest.mark.parametrize("limit", ["0", "-1", "201", "abc"])
async def test_a_limit_outside_its_range_is_a_400(api: Harness, limit: str) -> None:
    response = await api.get("/cases", limit=limit)

    assert response.status_code in (400, 422)
    assert response.json()["title"] in ("pagination.invalid_limit", "request.invalid")


async def test_a_cursor_the_api_did_not_write_is_a_400(api: Harness) -> None:
    for cursor in ("not-base64!!", "eyJ2Ijo5OSwiaiI6WyJ4Il19", "MTIz"):
        response = await api.get("/cases", cursor=cursor)
        assert response.status_code == 400, cursor
        assert response.json()["title"] == "pagination.invalid_cursor"


async def test_times_are_iso_8601_and_utc(api: Harness) -> None:
    await decided_case(api.sessions)

    response = await api.get(f"/cases/{CASE_ID}")
    row = response.json()["case"]

    assert row["sla_due_at"] == "2026-10-02T11:00:00Z"
    assert row["decided_at"] == "2026-10-02T11:00:00Z"
    assert row["created_at"].endswith("Z")


async def test_the_from_and_to_filters_bound_created_at(api: Harness) -> None:
    """`created_at` is the database's transaction time, so the range is read off the rows."""
    await decided_case(api.sessions, case_id="case-1", sla_due_at=T1)
    await decided_case(api.sessions, case_id="case-2", sla_due_at=T1)
    newest = await api.get("/cases", limit=1)
    created_at = newest.json()["items"][0]["created_at"]

    inside = await api.get(
        "/cases", **{"from": "2026-01-01T00:00:00Z", "to": "2027-01-01T00:00:00Z"}
    )
    # `to` is exclusive, so a range that ends at the newest row keeps the other one.
    before = await api.get("/cases", **{"from": "2026-01-01T00:00:00Z", "to": created_at})
    after = await api.get("/cases", **{"from": created_at, "to": "2027-01-01T00:00:00Z"})

    assert len(inside.json()["items"]) == 2
    # The newest row's own timestamp: `to` is exclusive so it drops that row, `from` is inclusive
    # so it keeps exactly that row.
    assert [item["case_id"] for item in before.json()["items"]] == ["case-1"]
    assert [item["case_id"] for item in after.json()["items"]] == ["case-2"]


async def test_a_time_filter_without_a_zone_is_a_400(api: Harness) -> None:
    response = await api.get("/cases", **{"from": "2026-10-02T09:00:00"})

    assert response.status_code == 400
    assert response.json()["title"] == "request.invalid_time"


async def test_the_database_being_unreachable_is_a_503(tmp_path: Path) -> None:
    """The service answers 503, not 500, when PostgreSQL cannot be reached."""
    from api_support import DevUsersFile, build_harness  # sys.path is set above
    from sqlalchemy.ext.asyncio import create_async_engine  # sys.path is set above

    from ais0c_storage import create_session_factory

    # A port nothing listens on, so the first query cannot connect.
    url = "postgresql+psycopg://ais0c_app:x@127.0.0.1:1/nowhere"
    engine = create_async_engine(url)
    harness = build_harness(create_session_factory(engine), DevUsersFile.write(tmp_path))
    try:
        response = await harness.get("/cases")
        assert response.status_code == 503
        assert response.json()["title"] == "storage.unavailable"
    finally:
        await harness.client.aclose()
        await engine.dispose()
