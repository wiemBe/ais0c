"""T-029 criterion 9: the QRadar offense link and the deterministic summary of a group.

- `AIS0C_QRADAR_OFFENSE_URL_TEMPLATE` is optional, starts with `https` and holds `{offense_id}`
  once; anything else stops the service;
- with a template every offense of `GET /cases`, `GET /cases/{id}` and `GET /groups/{id}` carries
  `qradar_offense_url`, without one the field is null;
- `GET /groups/{id}` counts the group's rows into a summary and names each offense's
  `full_analysis_reason`.
"""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest
from api_support import (
    CASE_ID,
    GROUP_ID,
    T0,
    DevUsersFile,
    Harness,
    add_group,
    add_offense,
    build_harness,
    open_case,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_api.settings import Settings, SettingsError
from ais0c_contracts import CaseSource
from ais0c_storage.enums import FullAnalysisReason, GroupValueKind, OffenseStatus
from ais0c_storage.repositories import add_group_values, set_full_analysis_reason

pytestmark = pytest.mark.anyio

# The lab console's offense page; the host is a documentation address (RFC 5737).
TEMPLATE = "https://192.0.2.10/console/ui/offenses/{offense_id}"
ENV = {"AIS0C_API_AUTH": "dev", "AIS0C_API_DEV_USERS_FILE": "x"}


@pytest.fixture
async def linked(
    sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> AsyncIterator[Harness]:
    harness = build_harness(sessions, DevUsersFile.write(tmp_path), offense_url_template=TEMPLATE)
    try:
        yield harness
    finally:
        await harness.client.aclose()


# --- the setting ------------------------------------------------------------------------------------


def test_the_template_is_optional() -> None:
    assert Settings.from_env(ENV).qradar_offense_url_template is None


def test_a_good_template_is_read() -> None:
    settings = Settings.from_env({**ENV, "AIS0C_QRADAR_OFFENSE_URL_TEMPLATE": TEMPLATE})

    assert settings.qradar_offense_url_template == TEMPLATE


@pytest.mark.parametrize(
    ("template", "message"),
    [
        ("http://192.0.2.10/offenses/{offense_id}", "must start with https://"),
        ("192.0.2.10/offenses/{offense_id}", "must start with https://"),
        ("javascript:alert({offense_id})", "must start with https://"),
        ("https://192.0.2.10/offenses/", "exactly once"),
        ("https://192.0.2.10/{offense_id}/{offense_id}", "exactly once"),
        ("https://192.0.2.10/{offense}/{offense_id}", "braces besides"),
    ],
    ids=["http", "no-scheme", "script", "no-placeholder", "twice", "other-braces"],
)
def test_a_bad_template_stops_the_service(template: str, message: str) -> None:
    with pytest.raises(SettingsError, match=message):
        Settings.from_env({**ENV, "AIS0C_QRADAR_OFFENSE_URL_TEMPLATE": template})


# --- the link on the cases ----------------------------------------------------------------------------


async def test_a_case_carries_the_link_of_its_offense(linked: Harness) -> None:
    await add_offense(linked.sessions)
    await open_case(linked.sessions)

    queue = (await linked.get("/cases")).json()["items"]
    detail = (await linked.get(f"/cases/{CASE_ID}")).json()

    expected = "https://192.0.2.10/console/ui/offenses/12345"
    assert queue[0]["qradar_offense_url"] == expected
    assert detail["case"]["qradar_offense_url"] == expected


async def test_without_a_template_the_link_is_null(api: Harness) -> None:
    await add_offense(api.sessions)
    await open_case(api.sessions)

    queue = (await api.get("/cases")).json()["items"]
    detail = (await api.get(f"/cases/{CASE_ID}")).json()

    assert queue[0]["qradar_offense_url"] is None
    assert detail["case"]["qradar_offense_url"] is None


async def test_a_case_without_an_offense_has_no_link(linked: Harness) -> None:
    await open_case(
        linked.sessions,
        case_id=GROUP_ID,
        source=CaseSource.GROUP,
        offense_id=None,
        group_id=GROUP_ID,
    )

    detail = (await linked.get(f"/cases/{GROUP_ID}")).json()

    assert detail["case"]["qradar_offense_url"] is None


# --- the group ----------------------------------------------------------------------------------------


async def test_the_offenses_of_a_group_carry_the_link_and_the_reason(linked: Harness) -> None:
    await add_group(linked.sessions, offense_ids=(5001, 5002))
    async with linked.sessions.begin() as session:
        await set_full_analysis_reason(session, 5001, FullAnalysisReason.NOVELTY)

    body = (await linked.get(f"/groups/{GROUP_ID}")).json()

    by_id = {row["offense_id"]: row for row in body["offenses"]}
    assert by_id[5001]["qradar_offense_url"] == "https://192.0.2.10/console/ui/offenses/5001"
    assert by_id[5001]["full_analysis_reason"] == "novelty"
    assert by_id[5002]["full_analysis_reason"] is None


async def test_the_summary_counts_the_rows_of_the_group(api: Harness) -> None:
    await add_group(api.sessions, offense_ids=())
    for offense_id, rules, minutes in ((5001, (100201,), 0), (5002, (100201, 100305), 4)):
        await add_offense(
            api.sessions,
            offense_id=offense_id,
            rule_ids=rules,
            group_id=GROUP_ID,
            status=OffenseStatus.GROUPED,
            first_seen_at=T0 + timedelta(minutes=minutes),
        )
    async with api.sessions.begin() as session:
        await add_group_values(
            session,
            GROUP_ID,
            5001,
            {GroupValueKind.SOURCE_IP: ["198.51.100.7", "198.51.100.8"]},
            seen_at=T0,
        )
        await add_group_values(
            session,
            GROUP_ID,
            5002,
            {GroupValueKind.SOURCE_IP: ["198.51.100.7"], GroupValueKind.USERNAME: ["svc-backup"]},
            seen_at=T0,
        )

    summary = (await api.get(f"/groups/{GROUP_ID}")).json()["summary"]

    assert summary["offense_count"] == 2
    assert summary["first_seen_at"].startswith("2026-10-02T10:00:00")
    assert summary["last_seen_at"].startswith("2026-10-02T10:04:00")
    assert summary["rule_ids"] == [100201, 100305]
    values = {item["kind"]: item for item in summary["values"]}
    assert values["source_ip"]["distinct"] == 2
    assert values["source_ip"]["top"][0] == {"value": "198.51.100.7", "offenses": 2}
    assert values["username"] == {
        "kind": "username",
        "distinct": 1,
        "top": [{"value": "svc-backup", "offenses": 1}],
    }


async def test_the_summary_of_a_group_without_offenses_is_empty(api: Harness) -> None:
    await add_group(api.sessions, offense_ids=())

    summary = (await api.get(f"/groups/{GROUP_ID}")).json()["summary"]

    assert summary == {
        "offense_count": 0,
        "first_seen_at": None,
        "last_seen_at": None,
        "rule_ids": [],
        "values": [],
    }
