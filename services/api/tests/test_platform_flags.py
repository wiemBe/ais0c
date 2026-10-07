"""Criterion 11: the platform flags, the kill switch (api.md "İzleme ve yönetim"; T-23, T-63 (2)).

- `GET /admin/platform-flags` answers every flag the platform knows; a flag with no row is off and
  its `changed_by` is empty;
- `PUT /admin/platform-flags/{name}` takes `{ enabled, reason }`, `reason` is required and may not
  be blank, and `changed_by` is the session's subject;
- an unknown flag is a 404.

Switching off is one step, always: the emergency stop never waits for a second person (T-63 (2)).
"""

import pytest
from api_support import Harness

from ais0c_storage.enums import ActorKind, PlatformFlag

pytestmark = pytest.mark.anyio

WRITES = "writes_enabled"


async def test_a_flag_with_no_row_is_off(api: Harness) -> None:
    """T-23: a new database has no row, so the platform starts in shadow mode."""
    body = (await api.get("/admin/platform-flags")).json()

    assert body == [
        {"name": WRITES, "enabled": False, "reason": None, "changed_by": None, "changed_at": None}
    ]


async def test_every_flag_the_platform_knows_is_answered(api: Harness) -> None:
    body = (await api.get("/admin/platform-flags")).json()

    assert {flag["name"] for flag in body} == {flag.value for flag in PlatformFlag}


async def switch_on(api: Harness, reason: str = "Canary başlıyor.") -> None:
    """Switching on waits for a second admin (D-36, T-77): admin asks, admin 2 approves."""
    asked = await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": True, "reason": reason}, as_role="admin"
    )
    assert (await api.approve(asked)).status_code == 200


async def test_an_admin_switches_the_flag_on_and_off(api: Harness) -> None:
    asked = await api.put(
        f"/admin/platform-flags/{WRITES}",
        {"enabled": True, "reason": "Canary başlıyor."},
        as_role="admin",
    )

    # The request waits: the flag the executor reads is still off (criterion 4).
    assert asked.status_code == 202
    assert set(asked.json()) == {"change_id"}
    assert (await api.get("/admin/platform-flags")).json()[0]["enabled"] is False
    assert await api.rows("SELECT * FROM platform_flags") == []

    assert (await api.approve(asked)).status_code == 200
    state = (await api.get("/admin/platform-flags")).json()[0]
    assert state["enabled"] is True
    # The approver wrote it, with the requester's reason.
    assert (state["reason"], state["changed_by"]) == ("Canary başlıyor.", "synthetic-admin-2")
    assert state["changed_at"] is not None

    off = await api.put(
        f"/admin/platform-flags/{WRITES}",
        {"enabled": False, "reason": "Not yazımları hatalı."},
        as_role="admin",
    )

    assert off.status_code == 200
    assert off.json()["enabled"] is False
    assert off.json()["reason"] == "Not yazımları hatalı."
    assert (await api.get("/admin/platform-flags")).json()[0]["enabled"] is False


async def test_switching_off_is_one_step_and_needs_no_second_person(api: Harness) -> None:
    """T-63 (2): the emergency stop never waits."""
    await switch_on(api, "x")

    response = await api.put(
        f"/admin/platform-flags/{WRITES}",
        {"enabled": False, "reason": "Acil durdurma."},
        as_role="admin",
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert (await api.rows("SELECT enabled FROM platform_flags")) == [{"enabled": False}]


async def test_the_executor_reads_the_flag_only_after_the_approval(api: Harness) -> None:
    """Criterion 4: the row the executor reads is off before the approval and on after it."""
    from ais0c_storage.repositories import get_platform_flag

    async def executor_reads() -> bool:
        async with api.sessions() as session:
            row = await get_platform_flag(session, PlatformFlag.WRITES_ENABLED)
        return row is not None and row.enabled

    asked = await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": True, "reason": "Canary."}, as_role="admin"
    )
    assert await executor_reads() is False

    await api.approve(asked)

    assert await executor_reads() is True


async def test_switching_off_ends_a_pending_request_to_switch_on(api: Harness) -> None:
    """A close while an open request waits writes at once and makes the request stale."""
    asked = await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": True, "reason": "Canary."}, as_role="admin"
    )

    # The requester closes it: allowed, and the request ends without a decider.
    off = await api.put(
        f"/admin/platform-flags/{WRITES}",
        {"enabled": False, "reason": "Vazgeçildi."},
        as_role="admin",
    )

    assert off.status_code == 200
    ended = (await api.get(f"/changes/{asked.json()['change_id']}", as_role="admin2")).json()
    assert (ended["status"], ended["reason"], ended["decided_by"]) == ("rejected", "stale", None)
    approve = await api.approve(asked)
    assert approve.status_code == 409
    assert approve.json()["title"] == "change.already_decided"
    assert (await api.get("/admin/platform-flags")).json()[0]["enabled"] is False


async def test_a_second_request_to_switch_on_is_a_409(api: Harness) -> None:
    first = await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": True, "reason": "Canary."}, as_role="admin"
    )

    second = await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": True, "reason": "Yine."}, as_role="admin2"
    )

    assert second.status_code == 409
    assert second.json()["title"] == "change.pending_exists"
    assert second.json()["change_id"] == first.json()["change_id"]


async def test_the_flag_changed_since_the_request_makes_it_stale(api: Harness) -> None:
    """Closed and opened again by another request: the old request is not approvable."""
    asked = await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": True, "reason": "Canary."}, as_role="admin"
    )
    # Something else changes the flag directly in storage (a direct write by another tool).
    await api.seed(
        "INSERT INTO platform_flags (name, enabled, reason, changed_by, changed_at)"
        " VALUES ('writes_enabled', false, 'elle', 'other', now())"
    )

    response = await api.approve(asked)

    assert response.status_code == 409
    assert response.json()["title"] == "change.stale"
    assert (await api.get("/admin/platform-flags")).json()[0]["enabled"] is False


@pytest.mark.parametrize("reason", ["", "   ", "\n"], ids=["empty", "spaces", "newline"])
async def test_a_blank_reason_is_a_422(api: Harness, reason: str) -> None:
    response = await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": True, "reason": reason}, as_role="admin"
    )

    assert response.status_code == 422
    assert response.json()["title"] == "platform_flag.reason_required"
    assert await api.rows("SELECT * FROM platform_flags") == []


async def test_a_reason_that_is_not_in_the_body_is_a_422(api: Harness) -> None:
    response = await api.put(f"/admin/platform-flags/{WRITES}", {"enabled": True}, as_role="admin")

    assert response.status_code == 422
    assert await api.rows("SELECT * FROM platform_flags") == []


async def test_a_reason_over_the_contract_length_is_a_422(api: Harness) -> None:
    response = await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": True, "reason": "x" * 501}, as_role="admin"
    )

    assert response.status_code == 422
    assert await api.rows("SELECT * FROM platform_flags") == []


async def test_an_unknown_flag_is_a_404(api: Harness) -> None:
    response = await api.put(
        "/admin/platform-flags/writes_disabled", {"enabled": True, "reason": "x"}, as_role="admin"
    )

    assert response.status_code == 404
    assert response.json()["title"] == "platform_flag.not_found"
    assert await api.rows("SELECT * FROM platform_flags") == []


async def test_an_operator_may_read_the_flags_but_not_change_them(api: Harness) -> None:
    read = await api.get("/admin/platform-flags")

    write = await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": True, "reason": "x"}, as_role="operator"
    )

    assert read.status_code == 200
    assert write.status_code == 403
    assert write.json()["title"] == "auth.forbidden"
    assert await api.rows("SELECT * FROM platform_flags") == []


async def test_a_hunter_may_not_change_the_flags_either(api: Harness) -> None:
    response = await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": True, "reason": "x"}, as_role="hunter"
    )

    assert response.status_code == 403


async def test_the_change_records_who_and_why(api: Harness) -> None:
    """T-017: the flag row and its audit row are written in one transaction."""
    await switch_on(api, "Canary.")
    await api.put(
        f"/admin/platform-flags/{WRITES}",
        {"enabled": False, "reason": "Geri alındı."},
        as_role="admin",
    )

    rows = await api.rows(
        "SELECT actor_kind, actor_id, action, object_type, object_id, details FROM audit_log "
        "WHERE object_type = 'platform_flag' ORDER BY id"
    )
    assert len(rows) == 2
    assert rows[0]["details"]["enabled"] is True
    assert rows[0]["details"]["previous"] is None
    assert rows[0]["details"]["reason"] == "Canary."
    assert rows[0]["details"]["requested_by"] == "synthetic-admin"
    assert rows[1]["details"] == {"enabled": False, "previous": True, "reason": "Geri alındı."}
    assert {row["actor_kind"] for row in rows} == {ActorKind.USER.value}
    assert [row["actor_id"] for row in rows] == ["synthetic-admin-2", "synthetic-admin"]


async def test_setting_the_flag_to_the_value_it_has_is_still_recorded(api: Harness) -> None:
    """Storage records every change, so two identical approvals leave two rows."""
    await switch_on(api, "Canary.")
    await switch_on(api, "Canary.")

    rows = await api.rows(
        "SELECT details FROM audit_log WHERE action = 'platform_flag.update' ORDER BY id"
    )
    assert [row["details"]["previous"] for row in rows] == [None, True]


async def test_an_agent_cannot_change_a_flag_through_the_api(api: Harness) -> None:
    """The API's caller is a user session; there is no route that acts as an agent."""
    await switch_on(api, "x")

    row = await api.one(
        "SELECT changed_by FROM platform_flags WHERE name = :name", {"name": WRITES}
    )
    assert row is not None
    assert row["changed_by"] == "synthetic-admin-2"


async def test_the_executor_reads_the_same_row_the_api_wrote(api: Harness) -> None:
    """What the API writes is what `get_platform_flag` gives the executor (T-023)."""
    from ais0c_storage.repositories import get_platform_flag

    await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": False, "reason": "Shadow."}, as_role="admin"
    )

    async with api.sessions() as session:
        row = await get_platform_flag(session, PlatformFlag.WRITES_ENABLED)

    assert row is not None
    assert (row.enabled, row.reason, row.changed_by) == (
        False,
        "Shadow.",
        "synthetic-admin",
    )
