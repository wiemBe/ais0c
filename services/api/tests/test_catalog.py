"""Criterion 7: the Analysis Catalog and the sync trigger (api.md "Analiz Kataloğu"; D-25, T-37).

- `GET /catalog/rules` filters on `defined`, `mode`, `qradar_enabled`, `missing` and `q`;
- `GET /catalog/log-sources` filters on `defined`, `in_scope`, `missing` and `q`;
- `PUT /catalog/rules/{rule_id}` and `POST /catalog/rules/{rule_id}/accept-draft` use the bodies
  api.md names, write through the existing repository functions, and a missing record is a 404;
- `POST /catalog/sync` triggers Temporal's `knowledge-sync` Schedule and answers 202; when Temporal
  cannot be reached it is a 503.

The `missing` filters are new reads in storage, added with T-028.
"""

import pytest
from api_support import Harness, add_catalog
from temporalio.service import RPCError, RPCStatusCode

from ais0c_api.temporal import (
    KNOWLEDGE_SYNC_SCHEDULE_ID,
    ScheduleNotFound,
    TemporalScheduleTrigger,
    TemporalUnavailable,
)

pytestmark = pytest.mark.anyio

MISSING_AT = "2026-10-03 03:00:00+00"


def rule_ids(response: object) -> list[int]:
    return [item["rule_id"] for item in response.json()["items"]]  # type: ignore[attr-defined]


def source_ids(response: object) -> list[int]:
    return [
        item["log_source_id"]
        for item in response.json()["items"]  # type: ignore[attr-defined]
    ]


async def mark_missing(api: Harness, table: str, key: str, value: int) -> None:
    """What the batch worker does when QRadar stops listing an entry (T-037).

    The table and column names are this test's own and every value is bound.
    """
    await api.rows(
        f"UPDATE {table} SET missing_since = :at WHERE {key} = :value RETURNING {key}",  # noqa: S608
        {"at": MISSING_AT, "value": value},
    )


# --- the rules ------------------------------------------------------------------------------------


async def test_the_rules_are_listed_as_the_sync_left_them(api: Harness) -> None:
    await add_catalog(
        api.sessions,
        rules=[(100201, "Excessive Firewall Accepts"), (100305, "DCSync")],
        log_sources=[(2001, "SRV-0001.example.com")],
    )

    response = await api.get("/catalog/rules")

    assert rule_ids(response) == [100201, 100305]
    row = response.json()["items"][0]
    assert row["rule_name"] == "Excessive Firewall Accepts"
    # A rule the sync has not seen an operator define yet is undefined and has no floor.
    assert row["defined"] is False
    assert row["mode"] == "analyze"
    assert row["min_level"] is None
    assert row["has_automated_action"] is False
    assert row["context_note"] is None
    assert row["ai_draft_note"] is None
    assert row["attack_techniques"] == []
    assert row["qradar_enabled"] is True
    assert row["missing_since"] is None
    assert row["updated_by"] == "knowledge-sync"


async def test_the_rules_filter_on_defined_and_mode(api: Harness) -> None:
    await add_catalog(api.sessions, rules=[(100201, "A"), (100305, "B")])
    await api.put(
        "/catalog/rules/100305", {"mode": "skip", "has_automated_action": False}, as_role="admin"
    )

    assert rule_ids(await api.get("/catalog/rules", defined=True)) == [100305]
    assert rule_ids(await api.get("/catalog/rules", defined=False)) == [100201]
    assert rule_ids(await api.get("/catalog/rules", mode="skip")) == [100305]
    assert rule_ids(await api.get("/catalog/rules", mode="analyze")) == [100201]


async def test_the_rules_filter_on_the_qradar_state(api: Harness) -> None:
    """Criterion 7: `qradar_enabled=false` keeps the rules QRadar has disabled (T-37)."""
    await add_catalog(api.sessions, rules=[(100201, "A"), (100305, "B")])
    await api.rows(
        "UPDATE catalog_rules SET qradar_enabled = false WHERE rule_id = 100305 RETURNING rule_id"
    )

    assert rule_ids(await api.get("/catalog/rules", qradar_enabled=False)) == [100305]
    assert rule_ids(await api.get("/catalog/rules", qradar_enabled=True)) == [100201]
    assert len(rule_ids(await api.get("/catalog/rules"))) == 2


async def test_the_rules_filter_on_what_qradar_no_longer_lists(api: Harness) -> None:
    """Criterion 7: `missing=true`. The entry stays; the analysis keeps using it (T-37)."""
    await add_catalog(api.sessions, rules=[(100201, "A"), (100305, "B")])
    await mark_missing(api, "catalog_rules", "rule_id", 100305)

    assert rule_ids(await api.get("/catalog/rules", missing=True)) == [100305]
    assert rule_ids(await api.get("/catalog/rules", missing=False)) == [100201]
    row = (await api.get("/catalog/rules/100305")).json()
    assert row["missing_since"] == "2026-10-03T03:00:00Z"
    assert row["rule_name"] == "B"


async def test_the_rules_are_searched_by_name(api: Harness) -> None:
    await add_catalog(
        api.sessions,
        rules=[(100201, "Excessive Firewall Accepts"), (100305, "DCSync indicator")],
    )

    assert rule_ids(await api.get("/catalog/rules", q="firewall")) == [100201]
    assert rule_ids(await api.get("/catalog/rules", q="DCSYNC")) == [100305]
    assert rule_ids(await api.get("/catalog/rules", q="nothing here")) == []


async def test_one_rule_is_read_on_its_own(api: Harness) -> None:
    await add_catalog(api.sessions, rules=[(100201, "Excessive Firewall Accepts")])

    assert (await api.get("/catalog/rules/100201")).json()["rule_id"] == 100201
    missing = await api.get("/catalog/rules/999999")
    assert missing.status_code == 404
    assert missing.json()["title"] == "catalog.rule_not_found"


async def test_the_catalog_pages_by_rule_id(api: Harness) -> None:
    await add_catalog(api.sessions, rules=[(100201, "A"), (100202, "B"), (100203, "C")])

    first = await api.get("/catalog/rules", limit=2)
    assert rule_ids(first) == [100201, 100202]
    assert first.json()["next_cursor"] is not None

    second = await api.get("/catalog/rules", limit=2, cursor=first.json()["next_cursor"])
    assert rule_ids(second) == [100203]
    assert second.json()["next_cursor"] is None


# --- the rule edits -------------------------------------------------------------------------------


async def test_an_admin_edits_a_rule_and_it_becomes_defined(api: Harness) -> None:
    await add_catalog(api.sessions, rules=[(100201, "Excessive Firewall Accepts")])

    response = await api.put(
        "/catalog/rules/100201",
        {
            "mode": "analyze",
            "min_level": "high",
            "has_automated_action": False,
            "context_note": "Taranan kaynaklardan gelen trafik.",
            "attack_techniques": ["T1003.006", "T1078"],
        },
        as_role="admin",
    )

    assert response.status_code == 200
    row = response.json()
    assert row["defined"] is True
    assert row["mode"] == "analyze"
    assert row["min_level"] == "high"
    assert row["context_note"] == "Taranan kaynaklardan gelen trafik."
    # Stored sorted and once each, as `update_catalog_rule` does.
    assert row["attack_techniques"] == ["T1003.006", "T1078"]
    assert row["updated_by"] == "synthetic-admin"


async def test_a_rule_can_be_set_to_skip(api: Harness) -> None:
    await add_catalog(api.sessions, rules=[(100201, "A")])

    row = (
        await api.put(
            "/catalog/rules/100201",
            {"mode": "skip", "has_automated_action": True, "context_note": None},
            as_role="admin",
        )
    ).json()

    assert (row["mode"], row["has_automated_action"]) == ("skip", True)


async def test_editing_an_unknown_rule_is_a_404(api: Harness) -> None:
    response = await api.put(
        "/catalog/rules/999999", {"mode": "analyze", "has_automated_action": False}, as_role="admin"
    )

    assert response.status_code == 404
    assert response.json()["title"] == "catalog.rule_not_found"


@pytest.mark.parametrize(
    "techniques",
    [["T100"], ["X1003"], ["T1003.06"], ["T1003.0067"], ["t1003"], ["T1003.abc"], ["1003"]],
    ids=["short", "letter", "two-digit-suffix", "four-digit-suffix", "lower", "letters", "digits"],
)
async def test_a_malformed_attack_technique_is_a_422(api: Harness, techniques: list[str]) -> None:
    await add_catalog(api.sessions, rules=[(100201, "A")])

    response = await api.put(
        "/catalog/rules/100201",
        {"mode": "analyze", "has_automated_action": False, "attack_techniques": techniques},
        as_role="admin",
    )

    assert response.status_code == 422
    # The body is checked against contracts' `AttackTechnique`, which the schema carries too.
    assert response.json()["title"] == "request.invalid"
    assert [error["field"] for error in response.json()["errors"]] == ["body.attack_techniques.0"]
    stored = await api.one("SELECT defined FROM catalog_rules WHERE rule_id = 100201")
    assert stored == {"defined": False}


async def test_accepting_a_draft_makes_it_the_rule_note(api: Harness) -> None:
    await add_catalog(api.sessions, rules=[(100201, "A")])
    await api.rows(
        "UPDATE catalog_rules SET ai_draft_note = :note RETURNING rule_id", {"note": "Öneri."}
    )

    response = await api.post("/catalog/rules/100201/accept-draft", as_role="admin")

    assert response.status_code == 200
    row = response.json()
    assert row["context_note"] == "Öneri."
    assert row["ai_draft_note"] is None


async def test_accepting_a_draft_twice_is_a_404(api: Harness) -> None:
    await add_catalog(api.sessions, rules=[(100201, "A")])
    await api.rows(
        "UPDATE catalog_rules SET ai_draft_note = :note RETURNING rule_id", {"note": "Öneri."}
    )
    await api.post("/catalog/rules/100201/accept-draft", as_role="admin")

    second = await api.post("/catalog/rules/100201/accept-draft", as_role="admin")

    assert second.status_code == 404
    assert second.json()["title"] == "catalog.rule_draft_not_found"


async def test_accepting_a_draft_that_does_not_exist_is_a_404(api: Harness) -> None:
    await add_catalog(api.sessions, rules=[(100201, "A")])

    response = await api.post("/catalog/rules/100201/accept-draft", as_role="admin")

    assert response.status_code == 404
    assert response.json()["title"] == "catalog.rule_draft_not_found"


# --- the log sources ------------------------------------------------------------------------------


async def test_the_log_sources_are_listed_and_filtered(api: Harness) -> None:
    await add_catalog(
        api.sessions,
        rules=[],
        log_sources=[
            (2001, "SRV-0001.example.com"),
            (2002, "FW-EDGE-01.example.com"),
            (2003, "SRV-0003.example.com"),
        ],
    )
    await api.rows(
        "UPDATE catalog_log_sources SET in_scope = false, criticality = 'low' "
        "WHERE log_source_id = 2002 RETURNING log_source_id"
    )
    await mark_missing(api, "catalog_log_sources", "log_source_id", 2003)

    assert source_ids(await api.get("/catalog/log-sources")) == [2001, 2002, 2003]
    assert source_ids(await api.get("/catalog/log-sources", in_scope=True)) == [2001, 2003]
    assert source_ids(await api.get("/catalog/log-sources", in_scope=False)) == [2002]
    assert source_ids(await api.get("/catalog/log-sources", missing=True)) == [2003]
    assert source_ids(await api.get("/catalog/log-sources", missing=False)) == [2001, 2002]
    # `q` matches the name or the type name.
    assert source_ids(await api.get("/catalog/log-sources", q="fw-edge")) == [2002]
    assert source_ids(await api.get("/catalog/log-sources", q="windows security")) == [
        2001,
        2002,
        2003,
    ]
    assert source_ids(await api.get("/catalog/log-sources", defined=False)) == [2001, 2002, 2003]


async def test_an_admin_edits_a_log_source_and_it_becomes_defined(api: Harness) -> None:
    await add_catalog(api.sessions, rules=[], log_sources=[(2001, "SRV-0001.example.com")])

    response = await api.put(
        "/catalog/log-sources/2001",
        {
            "description": "Windows güvenlik günlükleri.",
            "owner": "soc-ekip",
            "criticality": "medium",
            "in_scope": False,
            "context_note": "Sunucu günlükleri.",
        },
        as_role="admin",
    )

    assert response.status_code == 200
    row = response.json()
    assert row["defined"] is True
    assert row["description"] == "Windows güvenlik günlükleri."
    assert row["owner"] == "soc-ekip"
    assert row["criticality"] == "medium"
    assert row["in_scope"] is False
    assert row["context_note"] == "Sunucu günlükleri."
    assert row["updated_by"] == "synthetic-admin"


async def test_editing_an_unknown_log_source_is_a_404(api: Harness) -> None:
    response = await api.put("/catalog/log-sources/999999", {"in_scope": True}, as_role="admin")

    assert response.status_code == 404
    assert response.json()["title"] == "catalog.log_source_not_found"


async def test_the_log_sources_page_by_id(api: Harness) -> None:
    await add_catalog(api.sessions, rules=[], log_sources=[(2001, "A"), (2002, "B")])

    first = await api.get("/catalog/log-sources", limit=1)

    assert source_ids(first) == [2001]
    second = await api.get("/catalog/log-sources", limit=1, cursor=first.json()["next_cursor"])
    assert source_ids(second) == [2002]
    assert second.json()["next_cursor"] is None


# --- the sync trigger -----------------------------------------------------------------------------


async def test_the_sync_triggers_the_schedule_and_answers_202(api: Harness) -> None:
    response = await api.post("/catalog/sync", as_role="admin")

    assert response.status_code == 202
    assert response.json() == {"schedule_id": KNOWLEDGE_SYNC_SCHEDULE_ID, "triggered": True}
    assert api.trigger.triggered == [KNOWLEDGE_SYNC_SCHEDULE_ID]


async def test_the_sync_touches_no_catalog_entry(api: Harness) -> None:
    """The API asks Temporal to run; the batch worker does the sync itself (api.md)."""
    await add_catalog(api.sessions, rules=[(100201, "A")])

    await api.post("/catalog/sync", as_role="admin")

    row = await api.one("SELECT rule_name, defined FROM catalog_rules WHERE rule_id = 100201")
    assert row == {"rule_name": "A", "defined": False}


async def test_a_temporal_that_cannot_be_reached_is_a_503(api: Harness) -> None:
    api.trigger.unavailable = True

    response = await api.post("/catalog/sync", as_role="admin")

    assert response.status_code == 503
    assert response.json()["title"] == "temporal.unavailable"
    # Where Temporal runs is not the client's business.
    assert "127.0.0.1" not in response.text
    # The sync was not started, so no audit row says it was.
    assert await api.rows("SELECT * FROM audit_log WHERE action = 'catalog.sync'") == []


async def test_a_schedule_the_batch_worker_never_created_is_a_409(api: Harness) -> None:
    """Temporal answered; retrying will not help until the batch worker has run (T-037)."""
    api.trigger.missing = True

    response = await api.post("/catalog/sync", as_role="admin")

    assert response.status_code == 409
    assert response.json()["title"] == "catalog.sync_not_scheduled"
    assert await api.rows("SELECT * FROM audit_log WHERE action = 'catalog.sync'") == []


class _Handle:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def trigger(self) -> None:
        raise self._error


class _Client:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def get_schedule_handle(self, schedule_id: str) -> _Handle:
        return _Handle(self._error)


@pytest.mark.parametrize(
    ("status", "expected"),
    [(RPCStatusCode.NOT_FOUND, ScheduleNotFound), (RPCStatusCode.UNAVAILABLE, TemporalUnavailable)],
    ids=["not-found", "unavailable"],
)
async def test_the_temporal_trigger_tells_a_missing_schedule_apart(
    status: RPCStatusCode, expected: type[Exception]
) -> None:
    trigger = TemporalScheduleTrigger("127.0.0.1:7233", "default")
    trigger._client = _Client(RPCError("no", status, b""))  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]

    with pytest.raises(expected) as raised:
        await trigger.trigger(KNOWLEDGE_SYNC_SCHEDULE_ID)

    assert type(raised.value) is expected


async def test_the_sync_can_be_triggered_more_than_once(api: Harness) -> None:
    await api.post("/catalog/sync", as_role="admin")
    await api.post("/catalog/sync", as_role="admin")

    assert api.trigger.triggered == [KNOWLEDGE_SYNC_SCHEDULE_ID, KNOWLEDGE_SYNC_SCHEDULE_ID]
