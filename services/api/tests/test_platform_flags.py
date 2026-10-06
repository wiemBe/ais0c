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


async def test_an_admin_switches_the_flag_on_and_off(api: Harness) -> None:
    on = await api.put(
        f"/admin/platform-flags/{WRITES}",
        {"enabled": True, "reason": "Canary başlıyor."},
        as_role="admin",
    )

    assert on.status_code == 200
    assert on.json()["enabled"] is True
    assert on.json()["reason"] == "Canary başlıyor."
    assert on.json()["changed_by"] == "synthetic-admin"
    assert on.json()["changed_at"] is not None
    assert (await api.get("/admin/platform-flags")).json()[0]["enabled"] is True

    off = await api.put(
        f"/admin/platform-flags/{WRITES}",
        {"enabled": False, "reason": "Not yazımları hatalı."},
        as_role="admin",
    )

    assert off.json()["enabled"] is False
    assert off.json()["reason"] == "Not yazımları hatalı."
    assert (await api.get("/admin/platform-flags")).json()[0]["enabled"] is False


async def test_switching_off_is_one_step_and_needs_no_second_person(api: Harness) -> None:
    """T-63 (2): the emergency stop never waits (D-36 comes with T-033)."""
    await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": True, "reason": "x"}, as_role="admin"
    )

    response = await api.put(
        f"/admin/platform-flags/{WRITES}",
        {"enabled": False, "reason": "Acil durdurma."},
        as_role="admin",
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is False


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
    await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": True, "reason": "Canary."}, as_role="admin"
    )
    await api.put(
        f"/admin/platform-flags/{WRITES}",
        {"enabled": False, "reason": "Geri alındı."},
        as_role="admin",
    )

    rows = await api.rows(
        "SELECT actor_kind, actor_id, action, object_type, object_id, details FROM audit_log "
        "ORDER BY id"
    )
    assert len(rows) == 2
    assert rows[0]["details"] == {"enabled": True, "previous": None, "reason": "Canary."}
    assert rows[1]["details"] == {"enabled": False, "previous": True, "reason": "Geri alındı."}
    assert {row["actor_kind"] for row in rows} == {ActorKind.USER.value}
    assert {row["actor_id"] for row in rows} == {"synthetic-admin"}
    assert {row["object_type"] for row in rows} == {"platform_flag"}


async def test_setting_the_flag_to_the_value_it_has_is_still_recorded(api: Harness) -> None:
    """Storage records every change, so two identical calls leave two rows."""
    await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": True, "reason": "Canary."}, as_role="admin"
    )
    await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": True, "reason": "Canary."}, as_role="admin"
    )

    rows = await api.rows("SELECT details FROM audit_log ORDER BY id")
    assert [row["details"]["previous"] for row in rows] == [None, True]


async def test_an_agent_cannot_change_a_flag_through_the_api(api: Harness) -> None:
    """The API's caller is a user session; there is no route that acts as an agent."""
    response = await api.put(
        f"/admin/platform-flags/{WRITES}", {"enabled": True, "reason": "x"}, as_role="admin"
    )

    assert response.status_code == 200
    row = await api.one(
        "SELECT changed_by FROM platform_flags WHERE name = :name", {"name": WRITES}
    )
    assert row is not None
    assert row["changed_by"] == "synthetic-admin"


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
