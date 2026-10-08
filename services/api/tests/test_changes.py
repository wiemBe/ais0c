"""T-033: double control (D-36, T-77): `/changes` and the endpoints that now wait for approval.

- the changes of the catalog, the critical assets and the kill switch's opening are 202 and leave
  the object as it was, with a pending request that holds the object's values and the asked ones;
- a second admin approves (the change is written, audited with the approver as actor and the
  requester in the details) or rejects (nothing changes); the requester can only withdraw;
- the requester never decides their own request (403 and a database constraint), a decided request
  is not decided again (409), an object that changed since the request makes it stale (409), and an
  object has at most one pending request (409).
"""

import uuid
from typing import Any

import httpx2
import pytest
from api_support import Harness, add_catalog
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.anyio

RULE_EDIT = {
    "mode": "analyze",
    "min_level": "high",
    "has_automated_action": False,
    "context_note": "Taranan sunuculardan gelir.",
    "attack_techniques": ["T1003.006"],
}
ASSET = {"kind": "ip", "value": "192.0.2.10", "label": "VPN", "level": "high"}
WRITES = "/admin/platform-flags/writes_enabled"


async def change(api: Harness, change_id: str, *, as_role: str = "admin") -> dict[str, Any]:
    response = await api.get(f"/changes/{change_id}", as_role=as_role)
    assert response.status_code == 200, response.text
    return response.json()


async def ask_rule(api: Harness, *, as_role: str = "admin") -> httpx2.Response:
    return await api.put("/catalog/rules/100201", RULE_EDIT, as_role=as_role)


async def audit_actions(api: Harness) -> list[str]:
    rows = await api.rows("SELECT action FROM audit_log ORDER BY id")
    return [row["action"] for row in rows]


async def a_listed_asset(api: Harness) -> str:
    await api.approve(await api.post("/critical-assets", ASSET, as_role="admin"))
    return (await api.get("/critical-assets")).json()[0]["id"]


# --- what waits ---------------------------------------------------------------------------------


async def test_a_rule_edit_waits_and_the_request_holds_both_sides(api: Harness) -> None:
    await add_catalog(api.sessions)

    response = await ask_rule(api)

    assert response.status_code == 202
    request = await change(api, response.json()["change_id"])
    assert (request["object_type"], request["object_id"], request["status"]) == (
        "catalog_rule",
        "100201",
        "pending",
    )
    assert request["requested_by"] == "synthetic-admin"
    assert request["decided_by"] is None
    assert request["change"]["action"] == "update"
    assert request["change"]["before"]["mode"] == "analyze"
    assert request["change"]["before"]["context_note"] is None
    assert request["change"]["after"] == RULE_EDIT
    rule = await api.one("SELECT defined, context_note FROM catalog_rules WHERE rule_id = 100201")
    assert rule == {"defined": False, "context_note": None}
    assert await audit_actions(api) == ["change.request"]


async def test_an_accepted_draft_waits_with_the_note_it_would_become(api: Harness) -> None:
    await add_catalog(api.sessions)
    await api.seed("UPDATE catalog_rules SET ai_draft_note = 'Öneri.'")

    response = await api.post("/catalog/rules/100201/accept-draft", as_role="admin")

    request = await change(api, response.json()["change_id"])
    assert request["change"] == {
        "action": "accept_draft",
        "before": {"context_note": None, "ai_draft_note": "Öneri."},
        "after": {"context_note": "Öneri.", "ai_draft_note": None},
    }
    rule = await api.one("SELECT context_note, ai_draft_note FROM catalog_rules")
    assert rule == {"context_note": None, "ai_draft_note": "Öneri."}


async def test_a_log_source_edit_waits(api: Harness) -> None:
    await add_catalog(api.sessions)

    response = await api.put(
        "/catalog/log-sources/2001",
        {"description": "Güvenlik duvarı.", "in_scope": False, "criticality": "medium"},
        as_role="admin",
    )

    assert response.status_code == 202
    request = await change(api, response.json()["change_id"])
    assert request["object_type"] == "catalog_log_source"
    assert request["change"]["before"]["in_scope"] is True
    assert request["change"]["after"]["in_scope"] is False
    source = await api.one("SELECT defined, in_scope FROM catalog_log_sources")
    assert source == {"defined": False, "in_scope": True}


async def source_classes(api: Harness) -> dict[str, Any]:
    row = await api.one("SELECT telemetry_classes FROM catalog_log_sources")
    assert row is not None
    return row


async def test_assigning_classes_waits_for_a_second_admin(api: Harness) -> None:
    await add_catalog(api.sessions)

    response = await api.put(
        "/catalog/log-sources/2001",
        {"in_scope": True, "telemetry_classes": ["email-security"]},
        as_role="admin",
    )

    assert response.status_code == 202
    assert (await source_classes(api))["telemetry_classes"] is None
    assert (await api.approve(response)).status_code == 200
    assert (await source_classes(api))["telemetry_classes"] == ["email-security"]
    row = (await api.get("/catalog/log-sources/2001")).json()
    assert row["effective_telemetry_classes"] == ["email-security"]
    audit_row = await api.one(
        "SELECT details FROM audit_log WHERE action = 'catalog.log_source.update'"
    )
    assert audit_row is not None
    assert audit_row["details"]["telemetry_classes"] == ["email-security"]


async def test_put_without_the_field_keeps_the_classes(api: Harness) -> None:
    await add_catalog(api.sessions)
    await api.seed("UPDATE catalog_log_sources SET telemetry_classes = ARRAY['windows', 'vpn']")

    response = await api.put(
        "/catalog/log-sources/2001",
        {"description": "Alan denetleyicisi.", "in_scope": True},
        as_role="admin",
    )

    assert response.status_code == 202
    request = await change(api, response.json()["change_id"])
    assert request["change"]["after"]["telemetry_classes"] == ["vpn", "windows"]
    assert (await api.approve(response)).status_code == 200
    assert (await source_classes(api))["telemetry_classes"] == ["vpn", "windows"]
    assert (await api.get("/catalog/log-sources/2001")).json()["description"] == (
        "Alan denetleyicisi."
    )


async def test_null_returns_to_the_default(api: Harness) -> None:
    await add_catalog(api.sessions)
    await api.seed(
        "UPDATE catalog_log_sources SET telemetry_classes = ARRAY['vpn'], "
        "default_telemetry_classes = ARRAY['windows']"
    )

    response = await api.put(
        "/catalog/log-sources/2001",
        {"in_scope": True, "telemetry_classes": None},
        as_role="admin",
    )

    assert (await api.approve(response)).status_code == 200
    assert (await source_classes(api))["telemetry_classes"] is None
    row = (await api.get("/catalog/log-sources/2001")).json()
    assert row["effective_telemetry_classes"] == ["windows"]


async def test_an_empty_list_is_an_assignment_of_no_class(api: Harness) -> None:
    await add_catalog(api.sessions)
    await api.seed("UPDATE catalog_log_sources SET default_telemetry_classes = ARRAY['windows']")

    response = await api.put(
        "/catalog/log-sources/2001",
        {"in_scope": True, "telemetry_classes": []},
        as_role="admin",
    )

    assert (await api.approve(response)).status_code == 200
    assert (await source_classes(api))["telemetry_classes"] == []
    row = (await api.get("/catalog/log-sources/2001")).json()
    assert (row["telemetry_classes"], row["effective_telemetry_classes"]) == ([], [])


async def test_a_class_assigned_since_the_request_makes_it_stale(api: Harness) -> None:
    await add_catalog(api.sessions)
    asked = await api.put("/catalog/log-sources/2001", {"in_scope": True}, as_role="admin")
    await api.seed("UPDATE catalog_log_sources SET telemetry_classes = ARRAY['dns']")

    assert (await api.approve(asked)).status_code == 409


async def test_adding_an_asset_waits_and_the_stored_form_is_asked_for(api: Harness) -> None:
    response = await api.post(
        "/critical-assets",
        {"kind": "ip", "value": "2001:0DB8:0000::0001", "label": "VPN", "level": "critical"},
        as_role="admin",
    )

    assert response.status_code == 202
    request = await change(api, response.json()["change_id"])
    assert request["object_type"] == "critical_asset"
    assert request["change"]["before"] is None
    assert request["change"]["after"]["value"] == "2001:db8::1"
    assert await api.rows("SELECT * FROM critical_assets") == []


async def test_deleting_an_asset_waits_and_the_asset_stays(api: Harness) -> None:
    asset_id = await a_listed_asset(api)

    response = await api.delete(f"/critical-assets/{asset_id}", as_role="admin")

    assert response.status_code == 202
    request = await change(api, response.json()["change_id"])
    assert (request["object_type"], request["object_id"]) == ("critical_asset", asset_id)
    assert request["change"]["before"]["value"] == "192.0.2.10"
    assert request["change"]["after"] is None
    assert len(await api.rows("SELECT * FROM critical_assets")) == 1


async def test_adding_an_asset_that_is_listed_is_a_409_and_nothing_waits(api: Harness) -> None:
    await a_listed_asset(api)

    response = await api.post("/critical-assets", ASSET, as_role="admin2")

    assert response.status_code == 409
    assert response.json()["title"] == "critical_asset.exists"
    assert (await api.get("/changes", status="pending", as_role="admin")).json()["items"] == []


async def test_opening_the_kill_switch_waits_with_its_reason(api: Harness) -> None:
    response = await api.put(
        WRITES, {"enabled": True, "reason": "  Canary başlıyor. "}, as_role="admin"
    )

    request = await change(api, response.json()["change_id"])
    assert (request["object_type"], request["object_id"]) == ("platform_flag", "writes_enabled")
    assert request["change"]["reason"] == "Canary başlıyor."
    assert request["change"]["before"] == {"enabled": False}
    assert request["change"]["after"] == {"enabled": True}


# --- deciding -----------------------------------------------------------------------------------


async def test_approving_applies_the_change_and_audits_both(api: Harness) -> None:
    await add_catalog(api.sessions)
    asked = await ask_rule(api)

    approved = await api.approve(asked)

    assert approved.status_code == 200
    body = approved.json()
    assert (body["status"], body["decided_by"], body["reason"]) == (
        "approved",
        "synthetic-admin-2",
        None,
    )
    assert body["decided_at"] is not None
    rule = (await api.get("/catalog/rules/100201")).json()
    assert (rule["defined"], rule["mode"], rule["min_level"]) == (True, "analyze", "high")
    assert rule["attack_techniques"] == ["T1003.006"]
    rows = await api.rows(
        "SELECT actor_id, action, object_type, object_id, details FROM audit_log ORDER BY id"
    )
    assert [row["action"] for row in rows] == [
        "change.request",
        "catalog.rule.update",
        "change.approve",
    ]
    assert [row["actor_id"] for row in rows] == [
        "synthetic-admin",
        "synthetic-admin-2",
        "synthetic-admin-2",
    ]
    assert rows[1]["details"]["requested_by"] == "synthetic-admin"
    approval = rows[2]
    assert (approval["object_type"], approval["object_id"]) == (
        "change_approval",
        asked.json()["change_id"],
    )
    assert approval["details"]["requested_by"] == "synthetic-admin"


async def test_rejecting_changes_nothing_and_keeps_the_comment(api: Harness) -> None:
    await add_catalog(api.sessions)
    asked = await ask_rule(api)

    response = await api.post(
        f"/changes/{asked.json()['change_id']}/reject",
        {"comment": "  Kural yanlış.  "},
        as_role="admin2",
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["reason"], body["decided_by"], body["comment"]) == (
        "rejected",
        "rejected_by_admin",
        "synthetic-admin-2",
        "Kural yanlış.",
    )
    assert (await api.get("/catalog/rules/100201")).json()["defined"] is False
    assert await audit_actions(api) == ["change.request", "change.reject"]
    # The object is free for a new request.
    assert (await ask_rule(api)).status_code == 202


async def test_a_rejection_needs_no_comment(api: Harness) -> None:
    await add_catalog(api.sessions)
    asked = await ask_rule(api)

    response = await api.post(f"/changes/{asked.json()['change_id']}/reject", {}, as_role="admin2")

    assert response.status_code == 200
    assert response.json()["comment"] is None


async def test_the_requester_cannot_approve_or_reject_their_own_request(api: Harness) -> None:
    await add_catalog(api.sessions)
    asked = await ask_rule(api)
    change_id = asked.json()["change_id"]

    approve = await api.post(f"/changes/{change_id}/approve", as_role="admin")
    reject = await api.post(f"/changes/{change_id}/reject", {}, as_role="admin")

    assert (approve.status_code, reject.status_code) == (403, 403)
    assert approve.json()["title"] == reject.json()["title"] == "change.self_approval"
    assert (await change(api, change_id))["status"] == "pending"
    assert (await api.get("/catalog/rules/100201")).json()["defined"] is False
    assert await audit_actions(api) == ["change.request"]


async def test_the_database_refuses_the_requester_as_decider_even_past_the_api(
    api: Harness,
) -> None:
    await add_catalog(api.sessions)
    asked = await ask_rule(api)

    with pytest.raises(IntegrityError):
        await api.rows(
            "UPDATE change_approvals SET status = 'approved', decided_by = requested_by,"
            " decided_at = now() WHERE id = :id RETURNING id",
            {"id": uuid.UUID(asked.json()["change_id"])},
        )


async def test_an_operator_cannot_decide(api: Harness) -> None:
    await add_catalog(api.sessions)
    change_id = (await ask_rule(api)).json()["change_id"]

    for action in ("approve", "reject", "withdraw"):
        response = await api.post(f"/changes/{change_id}/{action}", {}, as_role="operator")
        assert response.status_code == 403, action
    assert (await api.get("/changes", as_role="operator")).status_code == 403
    assert (await change(api, change_id))["status"] == "pending"


async def test_a_decided_request_is_not_decided_again(api: Harness) -> None:
    await add_catalog(api.sessions)
    asked = await ask_rule(api)
    await api.approve(asked)
    change_id = asked.json()["change_id"]

    again = await api.post(f"/changes/{change_id}/approve", as_role="admin2")
    reject = await api.post(f"/changes/{change_id}/reject", {}, as_role="admin2")
    withdraw = await api.post(f"/changes/{change_id}/withdraw", as_role="admin")

    for response in (again, reject, withdraw):
        assert response.status_code == 409
        assert response.json()["title"] == "change.already_decided"
    assert (await api.get("/catalog/rules/100201")).json()["updated_by"] == "synthetic-admin-2"
    assert await audit_actions(api) == ["change.request", "catalog.rule.update", "change.approve"]


async def test_the_requester_withdraws_and_a_withdrawn_request_cannot_be_approved(
    api: Harness,
) -> None:
    await add_catalog(api.sessions)
    change_id = (await ask_rule(api)).json()["change_id"]

    withdrawn = await api.post(f"/changes/{change_id}/withdraw", as_role="admin")

    assert withdrawn.status_code == 200
    body = withdrawn.json()
    # Nobody decided it, so the requester is not its `decided_by`.
    assert (body["status"], body["reason"], body["decided_by"]) == ("rejected", "withdrawn", None)
    assert body["decided_at"] is not None
    approve = await api.post(f"/changes/{change_id}/approve", as_role="admin2")
    assert approve.status_code == 409
    assert approve.json()["title"] == "change.already_decided"
    assert (await api.get("/catalog/rules/100201")).json()["defined"] is False
    assert await audit_actions(api) == ["change.request", "change.withdraw"]


async def test_only_the_requester_may_withdraw(api: Harness) -> None:
    await add_catalog(api.sessions)
    change_id = (await ask_rule(api)).json()["change_id"]

    response = await api.post(f"/changes/{change_id}/withdraw", as_role="admin2")

    assert response.status_code == 403
    assert response.json()["title"] == "change.not_requester"
    assert (await change(api, change_id))["status"] == "pending"


async def test_an_unknown_request_is_a_404(api: Harness) -> None:
    unknown = uuid.uuid4()

    for response in (
        await api.get(f"/changes/{unknown}", as_role="admin"),
        await api.post(f"/changes/{unknown}/approve", as_role="admin"),
        await api.post(f"/changes/{unknown}/reject", {}, as_role="admin"),
        await api.post(f"/changes/{unknown}/withdraw", as_role="admin"),
    ):
        assert response.status_code == 404
        assert response.json()["title"] == "change.not_found"


# --- one request per object, and what makes one stale -------------------------------------------


async def test_an_object_has_one_pending_request(api: Harness) -> None:
    await add_catalog(api.sessions)
    await api.seed("UPDATE catalog_rules SET ai_draft_note = 'Öneri.'")
    first = await ask_rule(api)

    second = await ask_rule(api, as_role="admin2")
    draft = await api.post("/catalog/rules/100201/accept-draft", as_role="admin2")

    assert second.status_code == 409
    assert second.json()["title"] == "change.pending_exists"
    assert second.json()["change_id"] == first.json()["change_id"]
    # Accepting the draft is another change of the same rule: it meets the same pending request.
    assert draft.status_code == 409
    assert draft.json()["change_id"] == first.json()["change_id"]
    assert len((await api.get("/changes", as_role="admin")).json()["items"]) == 1
    assert await audit_actions(api) == ["change.request"]


async def test_a_second_request_is_allowed_once_the_first_is_decided(api: Harness) -> None:
    await add_catalog(api.sessions)
    await api.approve(await ask_rule(api))

    assert (await ask_rule(api, as_role="admin2")).status_code == 202


async def test_a_rule_that_changed_since_the_request_makes_it_stale(api: Harness) -> None:
    await add_catalog(api.sessions)
    asked = await ask_rule(api)
    # Another path changes what the request is about (a draft from the batch worker).
    await api.seed("UPDATE catalog_rules SET ai_draft_note = 'Yeni öneri.'")

    response = await api.approve(asked)

    assert response.status_code == 409
    assert response.json()["title"] == "change.stale"
    ended = await change(api, asked.json()["change_id"])
    assert (ended["status"], ended["reason"], ended["decided_by"]) == ("rejected", "stale", None)
    assert (await api.get("/catalog/rules/100201")).json()["defined"] is False
    assert await audit_actions(api) == ["change.request", "change.stale"]
    # The object is free again.
    assert (await ask_rule(api)).status_code == 202


async def test_what_the_sync_writes_does_not_make_a_request_stale(api: Harness) -> None:
    await add_catalog(api.sessions)
    asked = await ask_rule(api)
    await api.seed(
        "UPDATE catalog_rules SET qradar_enabled = false, missing_since = now(),"
        " rule_name = 'Renamed'"
    )

    assert (await api.approve(asked)).status_code == 200


async def test_a_log_source_that_changed_since_the_request_makes_it_stale(api: Harness) -> None:
    await add_catalog(api.sessions)
    asked = await api.put("/catalog/log-sources/2001", {"in_scope": False}, as_role="admin")
    await api.seed("UPDATE catalog_log_sources SET owner = 'soc'")

    response = await api.approve(asked)

    assert response.status_code == 409
    assert response.json()["title"] == "change.stale"
    assert (await api.get("/catalog/log-sources/2001")).json()["in_scope"] is True


async def test_an_asset_removed_since_the_delete_request_makes_it_stale(api: Harness) -> None:
    asset_id = await a_listed_asset(api)
    asked = await api.delete(f"/critical-assets/{asset_id}", as_role="admin")
    await api.seed("DELETE FROM critical_assets")

    response = await api.approve(asked)

    assert response.status_code == 409
    assert response.json()["title"] == "change.stale"


async def test_an_asset_edited_since_the_delete_request_makes_it_stale(api: Harness) -> None:
    asset_id = await a_listed_asset(api)
    asked = await api.delete(f"/critical-assets/{asset_id}", as_role="admin")
    await api.seed("UPDATE critical_assets SET label = 'Başka'")

    assert (await api.approve(asked)).status_code == 409
    assert len(await api.rows("SELECT * FROM critical_assets")) == 1


async def test_an_asset_listed_since_the_add_request_makes_it_stale(api: Harness) -> None:
    asked = await api.post("/critical-assets", ASSET, as_role="admin")
    await api.seed(
        "INSERT INTO critical_assets (id, kind, value, label, level)"
        " VALUES (gen_random_uuid(), 'ip', '192.0.2.10', 'Elle', 'high')"
    )

    response = await api.approve(asked)

    assert response.status_code == 409
    assert response.json()["title"] == "change.stale"
    assert len(await api.rows("SELECT * FROM critical_assets")) == 1


async def test_an_asset_has_one_pending_add_and_the_delete_is_a_different_object(
    api: Harness,
) -> None:
    first = await api.post("/critical-assets", ASSET, as_role="admin")
    other_label = dict(ASSET, label="Başka etiket")

    again = await api.post("/critical-assets", other_label, as_role="admin2")

    assert first.status_code == 202
    assert again.status_code == 409
    assert again.json()["title"] == "change.pending_exists"


# --- listing ------------------------------------------------------------------------------------


async def test_the_requests_are_listed_newest_first_and_filtered(api: Harness) -> None:
    await add_catalog(api.sessions, rules=[(100201, "A"), (100305, "B")])
    first = await ask_rule(api)
    await api.approve(first)
    second = await api.put("/catalog/rules/100305", RULE_EDIT, as_role="admin")
    asset = await api.post("/critical-assets", ASSET, as_role="admin")

    everything = (await api.get("/changes", as_role="admin")).json()["items"]
    pending = (await api.get("/changes", status="pending", as_role="admin")).json()["items"]
    approved = (await api.get("/changes", status="approved", as_role="admin")).json()["items"]
    assets = (await api.get("/changes", object_type="critical_asset", as_role="admin")).json()[
        "items"
    ]

    assert {item["id"] for item in everything} == {
        first.json()["change_id"],
        second.json()["change_id"],
        asset.json()["change_id"],
    }
    assert {item["id"] for item in pending} == {
        second.json()["change_id"],
        asset.json()["change_id"],
    }
    assert [item["id"] for item in approved] == [first.json()["change_id"]]
    assert [item["id"] for item in assets] == [asset.json()["change_id"]]


async def test_the_list_pages_by_cursor(api: Harness) -> None:
    await add_catalog(api.sessions, rules=[(1, "A"), (2, "B"), (3, "C")])
    for rule_id in (1, 2, 3):
        await api.put(f"/catalog/rules/{rule_id}", RULE_EDIT, as_role="admin")

    page = (await api.get("/changes", limit=2, as_role="admin")).json()
    rest = (await api.get("/changes", limit=2, cursor=page["next_cursor"], as_role="admin")).json()

    assert (len(page["items"]), len(rest["items"])) == (2, 1)
    assert rest["next_cursor"] is None
    ids = [item["id"] for item in page["items"] + rest["items"]]
    assert len(set(ids)) == 3


async def test_an_unreadable_cursor_is_a_400(api: Harness) -> None:
    response = await api.get("/changes", cursor="not-a-cursor", as_role="admin")

    assert response.status_code == 400
    assert response.json()["title"] == "pagination.invalid_cursor"
