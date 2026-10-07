"""Criterion 3: every changing request writes its `audit_log` row in the same transaction
(api.md; T-63 (3)).

Each write endpoint is exercised once and its audit row checked: `actor_kind=user`,
`actor_id` the session's subject, the endpoint's `action`, the object's type and ID, and a summary
in `details`. A request the API rejects (4xx) writes neither the change nor an audit row.

The kill switch is the one exception: `set_platform_flag` appends its own audit row inside the same
transaction (T-017), so the row still appears here, with `platform_flag.update`.
"""

import uuid
from typing import Any

import pytest
from api_support import (
    CASE_ID,
    GROUP_ID,
    Harness,
    add_catalog,
    add_group,
    decided_case,
    open_qa_items,
)

from ais0c_api.temporal import KNOWLEDGE_SYNC_SCHEDULE_ID

pytestmark = pytest.mark.anyio

AUDIT_COLUMNS = "actor_kind, actor_id, action, object_type, object_id, details"


async def audit_rows(api: Harness, **where: str) -> list[dict[str, Any]]:
    """The audit rows matching every `column = value`, as dictionaries.

    The column names are the test's own literals and the values are bound parameters, so no
    request content ever reaches a statement.
    """
    clause = " AND ".join(f"{name} = :{name}" for name in where)
    statement = (
        f"SELECT {AUDIT_COLUMNS} FROM audit_log"  # noqa: S608  # the column names are ours
        + (f" WHERE {clause}" if clause else "")
    )
    return await api.rows(statement, where)


async def seed_for_every_change(api: Harness) -> dict[str, uuid.UUID]:
    """One database holding something every write endpoint can change."""
    await decided_case(api.sessions)
    await add_catalog(api.sessions)
    await add_group(api.sessions, group_id=GROUP_ID)
    await api.seed("INSERT INTO allowed_email_domains (domain) VALUES ('example.com')")
    qa_id = (await open_qa_items(api.sessions))[0]
    return {"qa": qa_id}


# --- one audit row per changing endpoint -----------------------------------------------------------


async def test_case_feedback_leaves_one_row(api: Harness) -> None:
    await decided_case(api.sessions)

    await api.post(
        f"/cases/{CASE_ID}/feedback",
        {"case_id": CASE_ID, "verdict": "tp", "reason": "was_fp_not_tp"},
    )

    rows = await audit_rows(api, action="case.feedback")
    assert len(rows) == 1
    row = rows[0]
    assert (row["actor_kind"], row["actor_id"]) == ("user", "synthetic-operator")
    assert (row["object_type"], row["object_id"]) == ("case", CASE_ID)
    assert row["details"] == {"verdict": "tp", "reason": "was_fp_not_tp", "comment": None}


async def test_resolving_a_qa_item_leaves_one_row(api: Harness) -> None:
    seeded = await seed_for_every_change(api)

    await api.post(f"/qa/{seeded['qa']}/resolve", {"verdict": "fp", "reason": "correct"})

    rows = await audit_rows(api, action="qa.resolve")
    assert len(rows) == 1
    assert (rows[0]["actor_kind"], rows[0]["actor_id"]) == ("user", "synthetic-operator")
    assert (rows[0]["object_type"], rows[0]["object_id"]) == ("qa_item", str(seeded["qa"]))
    assert rows[0]["details"] == {
        "case_id": CASE_ID,
        "evaluation_no": 1,
        "qa_reason": "low_confidence",
        "verdict": "fp",
        "feedback_reason": "correct",
    }


async def test_a_catalog_rule_edit_leaves_one_row(api: Harness) -> None:
    await seed_for_every_change(api)

    await api.put(
        "/catalog/rules/100201",
        {
            "mode": "analyze",
            "min_level": "high",
            "has_automated_action": False,
            "context_note": "Taranan sunuculardan gelir.",
            "attack_techniques": ["T1003.006"],
        },
    )

    rows = await audit_rows(api, action="catalog.rule.update")
    assert len(rows) == 1
    assert (rows[0]["object_type"], rows[0]["object_id"]) == ("catalog_rule", "100201")
    assert rows[0]["details"]["mode"] == "analyze"
    assert rows[0]["details"]["min_level"] == "high"
    assert rows[0]["details"]["attack_techniques"] == ["T1003.006"]


async def test_accepting_a_draft_leaves_one_row(api: Harness) -> None:
    await seed_for_every_change(api)
    # The batch worker stores the draft (T-037), so this is what it leaves behind.
    await api.seed("UPDATE catalog_rules SET ai_draft_note = 'Önerilen açıklama.'")

    response = await api.post("/catalog/rules/100201/accept-draft", as_role="admin")

    assert response.status_code == 200
    rows = await audit_rows(api, action="catalog.rule.accept_draft")
    assert len(rows) == 1
    assert rows[0]["details"] == {"context_note": "Önerilen açıklama."}


async def test_a_catalog_log_source_edit_leaves_one_row(api: Harness) -> None:
    await seed_for_every_change(api)

    await api.put(
        "/catalog/log-sources/2001",
        {"description": "Güvenlik duvarı.", "in_scope": True, "criticality": "medium"},
    )

    rows = await audit_rows(api, action="catalog.log_source.update")
    assert len(rows) == 1
    assert (rows[0]["object_type"], rows[0]["object_id"]) == ("catalog_log_source", "2001")
    assert rows[0]["details"]["criticality"] == "medium"


async def test_the_sync_leaves_one_row(api: Harness) -> None:
    await seed_for_every_change(api)

    await api.post("/catalog/sync", as_role="admin")

    rows = await audit_rows(api, action="catalog.sync")
    assert len(rows) == 1
    assert (rows[0]["object_type"], rows[0]["object_id"]) == (
        "schedule",
        KNOWLEDGE_SYNC_SCHEDULE_ID,
    )


async def test_adding_a_critical_asset_leaves_one_row(api: Harness) -> None:
    await seed_for_every_change(api)

    response = await api.post(
        "/critical-assets",
        {"kind": "cidr", "value": "192.0.2.0/24", "label": "Ofis", "level": "critical"},
        as_role="admin",
    )

    rows = await audit_rows(api, action="critical_asset.add")
    assert len(rows) == 1
    assert rows[0]["object_id"] == response.json()["id"]
    assert rows[0]["details"] == {
        "kind": "cidr",
        "value": "192.0.2.0/24",
        "label": "Ofis",
        "level": "critical",
    }


async def test_deleting_a_critical_asset_leaves_one_row(api: Harness) -> None:
    await seed_for_every_change(api)
    # Added through the API, so the delete's audit row is read by its own action.
    created = await api.post(
        "/critical-assets",
        {"kind": "user", "value": "svc_backup", "label": "Backup", "level": "high"},
        as_role="admin",
    )
    asset_id = created.json()["id"]

    await api.delete(f"/critical-assets/{asset_id}", as_role="admin")

    rows = await audit_rows(api, action="critical_asset.delete")
    assert len(rows) == 1
    assert rows[0]["object_id"] == asset_id
    assert rows[0]["details"]["value"] == "svc_backup"


async def test_replacing_a_recipient_group_leaves_one_row(api: Harness) -> None:
    await seed_for_every_change(api)

    await api.put(
        "/notification-recipients/operators", {"emails": ["soc-1@example.com"]}, as_role="admin"
    )

    rows = await audit_rows(api, action="notification_recipients.replace")
    assert len(rows) == 1
    assert (rows[0]["object_type"], rows[0]["object_id"]) == (
        "notification_recipient_group",
        "operators",
    )
    assert rows[0]["details"] == {"members": 1, "added": ["soc-1@example.com"], "removed": []}


async def test_the_recipient_audit_row_names_who_was_added_and_removed(api: Harness) -> None:
    """Who receives the alert e-mails is what an admin's change is reviewed for."""
    await seed_for_every_change(api)
    await api.put(
        "/notification-recipients/operators",
        {"emails": ["soc-1@example.com", "soc-2@example.com"]},
        as_role="admin",
    )

    await api.put(
        "/notification-recipients/operators",
        {"emails": ["soc-2@example.com", "soc-3@example.com"]},
        as_role="admin",
    )

    rows = await audit_rows(api, action="notification_recipients.replace")
    assert len(rows) == 2
    assert {
        "members": 2,
        "added": ["soc-3@example.com"],
        "removed": ["soc-1@example.com"],
    } in [row["details"] for row in rows]


async def test_replacing_the_routes_leaves_one_row(api: Harness) -> None:
    await seed_for_every_change(api)
    await api.put(
        "/notification-recipients/operators", {"emails": ["soc-1@example.com"]}, as_role="admin"
    )

    await api.put(
        "/notification-routes",
        {"routes": [{"kind": "case_alert", "level": "high", "list_name": "operators"}]},
        as_role="admin",
    )

    rows = await audit_rows(api, action="notification_routes.replace")
    assert len(rows) == 1
    assert rows[0]["object_type"] == "notification_routes"
    details = rows[0]["details"]
    assert details["routes"] == 1
    # The table before (0007's seed) and after, so the change can be read back.
    assert {"kind": "hunt_report", "level": None, "list_name": "hunters"} in details["before"]
    assert len(details["before"]) == 9
    assert details["after"] == [{"kind": "case_alert", "level": "high", "list_name": "operators"}]


async def test_changing_a_platform_flag_leaves_one_row(api: Harness) -> None:
    """`set_platform_flag` writes its own audit row, in the same transaction (T-017)."""
    await seed_for_every_change(api)

    await api.put(
        "/admin/platform-flags/writes_enabled",
        {"enabled": True, "reason": "Canary başlıyor."},
        as_role="admin",
    )

    rows = await audit_rows(api, object_type="platform_flag", object_id="writes_enabled")
    assert len(rows) == 1
    row = rows[0]
    assert (row["actor_kind"], row["actor_id"]) == ("user", "synthetic-admin")
    assert row["action"] == "platform_flag.update"
    assert row["details"] == {"enabled": True, "previous": None, "reason": "Canary başlıyor."}


# --- a rejected request writes neither a change nor an audit row ----------------------------------


async def test_a_rejected_change_writes_nothing(api: Harness) -> None:
    await seed_for_every_change(api)

    rejected = [
        await api.post(
            f"/cases/{CASE_ID}/feedback",
            {"case_id": "case-other", "verdict": "tp", "reason": "was_fp_not_tp"},
        ),
        await api.put(
            "/catalog/rules/999999",
            {"mode": "analyze", "has_automated_action": False},
            as_role="admin",
        ),
        await api.post("/catalog/rules/100201/accept-draft", as_role="admin"),  # no draft yet
        await api.put("/catalog/log-sources/999999", {"in_scope": True}, as_role="admin"),
        await api.post(
            "/critical-assets",
            {"kind": "ip", "value": "not-an-ip", "label": "X", "level": "high"},
            as_role="admin",
        ),
        await api.delete(f"/critical-assets/{uuid.uuid4()}", as_role="admin"),
        await api.put(
            "/notification-recipients/operators",
            {"emails": ["soc-1@example.net"]},
            as_role="admin",
        ),
        await api.put(
            "/notification-routes",
            {"routes": [{"kind": "case_alert", "level": "high", "list_name": "nope"}]},
            as_role="admin",
        ),
        await api.put(
            "/admin/platform-flags/writes_enabled",
            {"enabled": True, "reason": "  "},
            as_role="admin",
        ),
        await api.put(
            "/admin/platform-flags/not-a-flag", {"enabled": True, "reason": "x"}, as_role="admin"
        ),
    ]

    assert [response.status_code for response in rejected] == [
        422,
        404,
        404,
        404,
        422,
        404,
        422,
        422,
        422,
        404,
    ]
    assert await audit_rows(api) == []
    # None of the changes landed either.
    assert await api.rows("SELECT * FROM operator_feedback") == []
    assert await api.rows("SELECT * FROM notification_routes WHERE list_name = 'nope'") == []
    assert (
        await api.rows("SELECT * FROM notification_recipients WHERE list_name = 'operators'") == []
    )
    assert await api.rows("SELECT * FROM platform_flags") == []
    assert await api.rows("SELECT * FROM critical_assets") == []
    assert await api.rows("SELECT * FROM catalog_rules WHERE rule_id = 999999") == []
    assert (
        await api.rows("SELECT * FROM catalog_rules WHERE defined AND context_note IS NOT NULL")
        == []
    )


async def test_a_change_that_fails_leaves_no_audit_row_either(api: Harness) -> None:
    """The item's own transaction rolls back, so its audit row goes with the change."""
    seeded = await seed_for_every_change(api)
    item = seeded["qa"]

    await api.post(f"/qa/{item}/resolve", {"verdict": "fp", "reason": "correct"})
    assert len(await audit_rows(api, action="qa.resolve")) == 1

    # A second resolve is a 409 and writes nothing more.
    second = await api.post(f"/qa/{item}/resolve", {"verdict": "fp", "reason": "correct"})

    assert second.status_code == 409
    assert second.json()["title"] == "qa.already_resolved"
    assert len(await audit_rows(api, action="qa.resolve")) == 1
    assert len(await api.rows("SELECT * FROM operator_feedback")) == 1


async def test_the_actor_is_the_session_that_asked(api: Harness) -> None:
    await decided_case(api.sessions)

    await api.post(
        f"/cases/{CASE_ID}/feedback",
        {"case_id": CASE_ID, "verdict": "fp", "reason": "correct"},
        as_role="hunter",
    )

    rows = await audit_rows(api, action="case.feedback")
    assert [row["actor_id"] for row in rows] == ["synthetic-hunter"]


async def test_a_read_leaves_no_audit_row(api: Harness) -> None:
    await seed_for_every_change(api)

    await api.get("/cases")
    await api.get(f"/cases/{CASE_ID}")
    await api.get("/qa")
    await api.get("/catalog/rules")
    await api.get("/admin/platform-flags")

    assert await audit_rows(api) == []
