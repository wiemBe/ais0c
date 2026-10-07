"""Criteria 9 and 10: critical assets, the named recipient groups, the routing table and the SLA
metric (api.md "Kritik varlıklar ve alıcılar", "İzleme ve yönetim"; D-41, T-63 (4)).

Assets and recipients:
- a critical asset is added, listed and deleted; storage's normalization and validation are used,
  and a value it refuses is a 422;
- `PUT /notification-recipients/{list_name}` replaces a group's membership and a new name creates
  the group; one address outside the allowed domains refuses the whole request (422) and nothing
  changes;
- `PUT /notification-routes` replaces the whole table and a route to a group that does not exist is
  refused (422);
- addresses keep their local part and get a lower-case domain.

SLA:
- `GET /metrics/sla?from=&to=` counts, per `floor_level` (the floor-less bucket is `none`), the
  cases whose deadline is in the range: total, decided on time, decided late, undecided
  (`no_ai_decision`) and still running.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from api_support import T0, T1, Harness, decided_case, open_case

from ais0c_contracts import Level
from ais0c_storage.enums import CaseStatus

pytestmark = pytest.mark.anyio


# --- critical assets --------------------------------------------------------------------------------


async def test_a_critical_asset_is_added_listed_and_deleted(api: Harness) -> None:
    created = await api.post(
        "/critical-assets",
        {"kind": "ip", "value": "192.0.2.10", "label": "VPN", "level": "high"},
        as_role="admin",
    )

    assert created.status_code == 201
    asset = created.json()
    assert (asset["kind"], asset["value"], asset["label"], asset["level"]) == (
        "ip",
        "192.0.2.10",
        "VPN",
        "high",
    )
    listed = (await api.get("/critical-assets")).json()
    assert [item["id"] for item in listed] == [asset["id"]]

    deleted = await api.delete(f"/critical-assets/{asset['id']}", as_role="admin")

    assert deleted.status_code == 204
    assert (await api.get("/critical-assets")).json() == []


async def test_storage_normalizes_the_value_of_an_asset(api: Harness) -> None:
    """An IP is stored canonically, as storage's `_normalized` does."""
    await api.post(
        "/critical-assets",
        {"kind": "ip", "value": "2001:0DB8:0000::0001", "label": "VPN", "level": "critical"},
        as_role="admin",
    )
    await api.post(
        "/critical-assets",
        {"kind": "cidr", "value": "198.51.100.0/24", "label": "Bölge", "level": "high"},
        as_role="admin",
    )

    rows = await api.rows("SELECT kind, value FROM critical_assets ORDER BY kind")
    assert [(row["kind"], row["value"]) for row in rows] == [
        ("cidr", "198.51.100.0/24"),
        ("ip", "2001:db8::1"),
    ]


@pytest.mark.parametrize(
    ("body", "label"),
    [
        ({"kind": "ip", "value": "not-an-ip", "label": "X", "level": "high"}, "ip"),
        ({"kind": "cidr", "value": "192.0.2.10/24", "label": "X", "level": "high"}, "cidr"),
        ({"kind": "host", "value": "   ", "label": "X", "level": "high"}, "host"),
        ({"kind": "ip", "value": "192.0.2.10", "label": "X", "level": "low"}, "level"),
        ({"kind": "ip", "value": "192.0.2.10", "label": "X", "level": "medium"}, "level"),
    ],
    ids=["not-an-ip", "host-bits", "blank", "low", "medium"],
)
async def test_an_asset_storage_refuses_is_a_422(
    api: Harness, body: dict[str, str], label: str
) -> None:
    response = await api.post("/critical-assets", body, as_role="admin")

    assert response.status_code == 422, label
    assert response.json()["title"] == "critical_asset.invalid"
    assert await api.rows("SELECT * FROM critical_assets") == []


async def test_a_label_over_the_contract_length_is_a_422(api: Harness) -> None:
    response = await api.post(
        "/critical-assets",
        {"kind": "host", "value": "dc.example.com", "label": "x" * 301, "level": "high"},
        as_role="admin",
    )

    assert response.status_code == 422
    assert await api.rows("SELECT * FROM critical_assets") == []


async def test_deleting_an_asset_that_is_not_there_is_a_404(api: Harness) -> None:
    response = await api.delete(f"/critical-assets/{uuid.uuid4()}", as_role="admin")

    assert response.status_code == 404
    assert response.json()["title"] == "critical_asset.not_found"


async def test_the_assets_are_listed_by_kind_then_value(api: Harness) -> None:
    for kind, value in (("host", "dc.example.com"), ("ip", "192.0.2.2"), ("ip", "192.0.2.1")):
        await api.post(
            "/critical-assets",
            {"kind": kind, "value": value, "label": "X", "level": "high"},
            as_role="admin",
        )

    listed = (await api.get("/critical-assets")).json()

    assert [(item["kind"], item["value"]) for item in listed] == [
        ("host", "dc.example.com"),
        ("ip", "192.0.2.1"),
        ("ip", "192.0.2.2"),
    ]


# --- the recipient groups --------------------------------------------------------------------------


async def test_a_new_group_name_creates_the_group(api: Harness) -> None:
    await api.seed("INSERT INTO allowed_email_domains (domain) VALUES ('example.com')")

    response = await api.put(
        "/notification-recipients/soc-on-call",
        {"emails": ["soc-1@example.com", "soc-2@example.com"]},
        as_role="admin",
    )

    assert response.status_code == 200
    assert response.json() == {
        "list_name": "soc-on-call",
        "emails": ["soc-1@example.com", "soc-2@example.com"],
    }


async def test_a_group_is_replaced_as_a_whole(api: Harness) -> None:
    await api.seed("INSERT INTO allowed_email_domains (domain) VALUES ('example.com')")
    await api.put(
        "/notification-recipients/operators",
        {"emails": ["soc-1@example.com", "soc-2@example.com"]},
        as_role="admin",
    )

    replaced = await api.put(
        "/notification-recipients/operators", {"emails": ["soc-3@example.com"]}, as_role="admin"
    )

    assert replaced.json()["emails"] == ["soc-3@example.com"]
    rows = await api.rows("SELECT list_name, email FROM notification_recipients ORDER BY email")
    assert rows == [{"list_name": "operators", "email": "soc-3@example.com"}]
    # An empty list empties a group no route names.
    await api.put(
        "/notification-recipients/soc-night", {"emails": ["soc-9@example.com"]}, as_role="admin"
    )
    empty = await api.put("/notification-recipients/soc-night", {"emails": []}, as_role="admin")
    assert empty.json()["emails"] == []
    assert await api.rows("SELECT list_name FROM notification_recipients") == [
        {"list_name": "operators"}
    ]


async def test_a_group_a_route_names_cannot_be_emptied(api: Harness) -> None:
    """0007 routes high alerts to `operators`: emptying it would leave the route with nobody."""
    await api.seed("INSERT INTO allowed_email_domains (domain) VALUES ('example.com')")
    await api.put(
        "/notification-recipients/operators", {"emails": ["soc-1@example.com"]}, as_role="admin"
    )

    response = await api.put("/notification-recipients/operators", {"emails": []}, as_role="admin")

    assert response.status_code == 409
    assert response.json()["title"] == "notification_recipients.group_in_use"
    assert await api.rows("SELECT email FROM notification_recipients") == [
        {"email": "soc-1@example.com"}
    ]
    # Only the fill left an audit row; the refused change left none.
    assert len(await api.rows("SELECT id FROM audit_log")) == 1
    # Once no route names it, it can go.
    await api.put("/notification-routes", {"routes": []}, as_role="admin")
    emptied = await api.put("/notification-recipients/operators", {"emails": []}, as_role="admin")
    assert emptied.status_code == 200


async def test_the_local_part_is_kept_and_the_domain_is_lower_cased(api: Harness) -> None:
    """Criterion 9: büyük/küçük harf normalleştirmesi."""
    await api.seed("INSERT INTO allowed_email_domains (domain) VALUES ('example.com')")

    response = await api.put(
        "/notification-recipients/operators", {"emails": ["Soc-1@EXAMPLE.COM"]}, as_role="admin"
    )

    assert response.json()["emails"] == ["Soc-1@example.com"]


async def test_one_address_outside_the_allowed_domains_refuses_the_whole_request(
    api: Harness,
) -> None:
    await api.seed("INSERT INTO allowed_email_domains (domain) VALUES ('example.com')")
    await api.put(
        "/notification-recipients/operators", {"emails": ["soc-1@example.com"]}, as_role="admin"
    )

    response = await api.put(
        "/notification-recipients/operators",
        {"emails": ["soc-2@example.com", "soc-3@example.net"]},
        as_role="admin",
    )

    assert response.status_code == 422
    assert response.json()["title"] == "notification_recipients.domain_not_allowed"
    assert response.json()["domains"] == ["example.net"]
    # Nothing changed: the group keeps its one member.
    rows = await api.rows("SELECT email FROM notification_recipients")
    assert [row["email"] for row in rows] == ["soc-1@example.com"]
    # The earlier change's audit row is still the only one; the rejected request wrote none.
    assert (
        len(
            await api.rows(
                "SELECT * FROM audit_log WHERE action = 'notification_recipients.replace'"
            )
        )
        == 1
    )


async def test_the_domain_check_is_case_insensitive(api: Harness) -> None:
    await api.seed("INSERT INTO allowed_email_domains (domain) VALUES ('example.com')")

    response = await api.put(
        "/notification-recipients/operators", {"emails": ["soc-1@Example.COM"]}, as_role="admin"
    )

    assert response.status_code == 200


async def test_a_group_name_outside_the_pattern_is_a_422(api: Harness) -> None:
    await api.seed("INSERT INTO allowed_email_domains (domain) VALUES ('example.com')")

    response = await api.put(
        "/notification-recipients/Operators", {"emails": ["soc-1@example.com"]}, as_role="admin"
    )

    assert response.status_code == 422
    assert response.json()["title"] == "notification_recipients.invalid"
    assert await api.rows("SELECT * FROM notification_recipients") == []


async def test_an_address_that_is_not_a_plain_address_is_a_422(api: Harness) -> None:
    await api.seed("INSERT INTO allowed_email_domains (domain) VALUES ('example.com')")

    response = await api.put(
        "/notification-recipients/operators",
        {"emails": ["soc-1@example.com, soc-2@example.com"]},
        as_role="admin",
    )

    assert response.status_code == 422
    assert await api.rows("SELECT * FROM notification_recipients") == []


async def test_the_groups_come_with_the_allowlist(api: Harness) -> None:
    await api.seed(
        "INSERT INTO allowed_email_domains (domain) VALUES ('example.com'), ('example.net')"
    )
    await api.put(
        "/notification-recipients/exec", {"emails": ["chief@example.com"]}, as_role="admin"
    )
    await api.put(
        "/notification-recipients/operators", {"emails": ["soc-1@example.net"]}, as_role="admin"
    )

    body = (await api.get("/notification-recipients", as_role="admin")).json()

    assert [group["list_name"] for group in body["groups"]] == ["exec", "operators"]
    assert body["allowed_domains"] == ["example.com", "example.net"]


# --- the routing table ------------------------------------------------------------------------------


async def test_the_seeded_routes_are_what_the_table_holds(api: Harness) -> None:
    """0007 seeds the starting routing D-41 names; the API returns it as it is stored."""
    body = (await api.get("/notification-routes", as_role="admin")).json()

    # D-41's starting routing, as 0007 seeds it: a case and a group alert at high to `operators`
    # and at critical to `operators`, `exec` and `analyst-eng`, and a hunt report to `hunters`.
    assert [(route["kind"], route["level"], route["list_name"]) for route in body] == [
        ("case_alert", "critical", "analyst-eng"),
        ("case_alert", "critical", "exec"),
        ("case_alert", "critical", "operators"),
        ("case_alert", "high", "operators"),
        ("group_alert", "critical", "analyst-eng"),
        ("group_alert", "critical", "exec"),
        ("group_alert", "critical", "operators"),
        ("group_alert", "high", "operators"),
        # The hunt report has no level of its own, and it sorts last.
        ("hunt_report", None, "hunters"),
    ]


async def test_the_table_is_replaced_as_a_whole(api: Harness) -> None:
    await api.seed("INSERT INTO allowed_email_domains (domain) VALUES ('example.com')")
    await api.put(
        "/notification-recipients/operators", {"emails": ["soc-1@example.com"]}, as_role="admin"
    )
    await api.put(
        "/notification-recipients/exec", {"emails": ["chief@example.com"]}, as_role="admin"
    )

    response = await api.put(
        "/notification-routes",
        {
            "routes": [
                {"kind": "case_alert", "level": "critical", "list_name": "operators"},
                {"kind": "case_alert", "level": "critical", "list_name": "exec"},
                {"kind": "hunt_report", "list_name": "operators"},
            ]
        },
        as_role="admin",
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3
    # Everything the seeded table held and was left out is gone.
    assert not any(route["level"] == "high" for route in body)
    rows = await api.rows("SELECT kind, level, list_name FROM notification_routes")
    assert len(rows) == 3


async def test_a_route_to_a_group_that_does_not_exist_is_a_422(api: Harness) -> None:
    await api.seed("INSERT INTO allowed_email_domains (domain) VALUES ('example.com')")
    await api.put(
        "/notification-recipients/operators", {"emails": ["soc-1@example.com"]}, as_role="admin"
    )
    before = (await api.get("/notification-routes", as_role="admin")).json()

    response = await api.put(
        "/notification-routes",
        {"routes": [{"kind": "case_alert", "level": "high", "list_name": "nobody"}]},
        as_role="admin",
    )

    assert response.status_code == 422
    assert response.json()["title"] == "notification_routes.unknown_group"
    assert response.json()["list_names"] == ["nobody"]
    # The table keeps what it had.
    assert (await api.get("/notification-routes", as_role="admin")).json() == before


async def test_an_empty_table_is_allowed(api: Harness) -> None:
    """An admin may route nothing: the e-mail is then refused by the executor, not the API."""
    response = await api.put("/notification-routes", {"routes": []}, as_role="admin")

    assert response.status_code == 200
    assert response.json() == []
    assert await api.rows("SELECT * FROM notification_routes") == []


async def test_a_repeated_route_is_written_once(api: Harness) -> None:
    await api.seed("INSERT INTO allowed_email_domains (domain) VALUES ('example.com')")
    await api.put(
        "/notification-recipients/operators", {"emails": ["soc-1@example.com"]}, as_role="admin"
    )

    response = await api.put(
        "/notification-routes",
        {
            "routes": [
                {"kind": "case_alert", "level": "high", "list_name": "operators"},
                {"kind": "case_alert", "level": "high", "list_name": "operators"},
            ]
        },
        as_role="admin",
    )

    assert len(response.json()) == 1


@pytest.mark.parametrize(
    "route",
    [
        {"kind": "case_alert", "level": None, "list_name": "operators"},
        {"kind": "group_alert", "list_name": "operators"},
        {"kind": "hunt_report", "level": "high", "list_name": "operators"},
    ],
    ids=["case-alert-without-level", "group-alert-without-level", "hunt-report-with-level"],
)
async def test_a_route_that_would_never_match_an_e_mail_is_a_422(
    api: Harness, route: dict[str, object]
) -> None:
    """The executor looks a case or group alert up by its level and a hunt report without one."""
    await api.seed("INSERT INTO allowed_email_domains (domain) VALUES ('example.com')")
    await api.put(
        "/notification-recipients/operators", {"emails": ["soc-1@example.com"]}, as_role="admin"
    )

    response = await api.put("/notification-routes", {"routes": [route]}, as_role="admin")

    assert response.status_code == 422
    assert response.json()["title"] == "request.invalid"
    assert len(await api.rows("SELECT * FROM notification_routes")) == 9


async def test_a_route_with_an_unknown_kind_is_a_422(api: Harness) -> None:
    response = await api.put(
        "/notification-routes",
        {"routes": [{"kind": "pager", "level": "high", "list_name": "operators"}]},
        as_role="admin",
    )

    assert response.status_code == 422
    assert response.json()["title"] == "request.invalid"


# --- the SLA metric ---------------------------------------------------------------------------------


async def test_the_metric_counts_every_bucket_per_floor(api: Harness) -> None:
    """Criterion 10: her kova için en az bir vaka."""
    # Decided before the deadline, with a high floor.
    await decided_case(api.sessions, case_id="case-on-time", floor_level=Level.HIGH)
    # Decided after it, with a medium floor.
    await decided_case(
        api.sessions,
        case_id="case-late",
        floor_level=Level.MEDIUM,
        decided_at=T1 + timedelta(minutes=5),
    )
    # No floor, no decision yet: still running.
    await open_case(api.sessions, case_id="case-running", floor_level=None, verdict=None)
    # No decision and no AI decision: undecided.
    await open_case(api.sessions, case_id="case-undecided", verdict=None)
    await api.rows(
        "UPDATE cases SET status = :status WHERE case_id = :case_id RETURNING case_id",
        {"status": CaseStatus.NO_AI_DECISION.value, "case_id": "case-undecided"},
    )
    # Closed in QRadar before the AI decided.
    await open_case(api.sessions, case_id="case-closed", floor_level=None, verdict=None)
    await api.rows(
        "UPDATE cases SET status = :status WHERE case_id = :case_id RETURNING case_id",
        {"status": CaseStatus.CLOSED.value, "case_id": "case-closed"},
    )
    # Its deadline is outside the range.
    await decided_case(api.sessions, case_id="case-outside", sla_due_at=T1 + timedelta(days=1))

    body = (
        await api.get(
            "/metrics/sla",
            **{"from": "2026-10-02T09:00:00Z", "to": "2026-10-02T23:00:00Z"},
        )
    ).json()

    buckets = {bucket["floor_level"]: bucket for bucket in body["buckets"]}
    assert set(buckets) == {"high", "medium", "none"}
    assert buckets["high"] == {
        "floor_level": "high",
        "total": 1,
        "on_time": 1,
        "late": 0,
        "undecided": 0,
        "running": 0,
        "closed": 0,
    }
    assert buckets["medium"] == {
        "floor_level": "medium",
        "total": 1,
        "on_time": 0,
        "late": 1,
        "undecided": 0,
        "running": 0,
        "closed": 0,
    }
    assert buckets["none"] == {
        "floor_level": "none",
        "total": 3,
        "on_time": 0,
        "late": 0,
        "undecided": 1,
        "running": 1,
        "closed": 1,
    }
    # Every bucket's numbers add up to its total.
    for bucket in body["buckets"]:
        assert bucket["total"] == (
            bucket["on_time"]
            + bucket["late"]
            + bucket["undecided"]
            + bucket["running"]
            + bucket["closed"]
        )


async def test_the_metric_covers_the_last_day_without_a_range(api: Harness) -> None:
    """No range means the last day, measured back from the clock at the time of the request."""
    # The window is the day that just ended, so the deadline is in it.
    recent = datetime.now(UTC) - timedelta(hours=1)
    await decided_case(api.sessions, sla_due_at=recent)
    await decided_case(
        api.sessions, case_id="case-far", sla_due_at=datetime.now(UTC) + timedelta(days=1)
    )

    body = (await api.get("/metrics/sla")).json()

    assert sum(bucket["total"] for bucket in body["buckets"]) == 1
    assert body["to"] > body["from"]


async def test_a_range_that_holds_no_deadline_answers_no_bucket(api: Harness) -> None:
    """`from` is inclusive and `to` exclusive, so a range between two deadlines is empty."""
    await decided_case(api.sessions, sla_due_at=T0)
    await decided_case(api.sessions, case_id="case-2", sla_due_at=T1)

    body = (
        await api.get(
            "/metrics/sla", **{"from": "2026-10-02T10:00:01Z", "to": "2026-10-02T10:59:59Z"}
        )
    ).json()

    assert body["buckets"] == []
    assert body["from"] == "2026-10-02T10:00:01Z"


async def test_the_metric_rejects_a_range_that_ends_before_it_starts(api: Harness) -> None:
    response = await api.get(
        "/metrics/sla", **{"from": "2026-10-03T00:00:00Z", "to": "2026-10-02T00:00:00Z"}
    )

    assert response.status_code == 422
    assert response.json()["title"] == "request.invalid_range"


async def test_the_metric_echoes_the_range_it_used(api: Harness) -> None:
    body = (
        await api.get(
            "/metrics/sla",
            **{"from": "2026-10-02T09:00:00Z", "to": "2026-10-02T23:00:00+03:00"},
        )
    ).json()

    assert body["from"] == "2026-10-02T09:00:00Z"
    # A time in another offset is normalized to UTC.
    assert body["to"] == "2026-10-02T20:00:00Z"


async def test_the_metric_counts_only_the_latest_evaluation(api: Harness) -> None:
    """T-63 (4): a case counts once, on the decision its own row holds."""
    await decided_case(api.sessions, case_id="case-1")
    await api.rows("UPDATE cases SET evaluation_no = 3 WHERE case_id = 'case-1' RETURNING case_id")

    body = (
        await api.get(
            "/metrics/sla", **{"from": "2026-10-01T00:00:00Z", "to": "2026-11-01T00:00:00Z"}
        )
    ).json()

    assert sum(bucket["total"] for bucket in body["buckets"]) == 1


async def test_the_metric_reports_the_floor_severity_order(api: Harness) -> None:
    await decided_case(api.sessions, case_id="c-1", floor_level=Level.LOW)
    await decided_case(api.sessions, case_id="c-2", floor_level=Level.CRITICAL)
    await decided_case(api.sessions, case_id="c-3", floor_level=None, verdict=None)

    body = (
        await api.get(
            "/metrics/sla", **{"from": "2026-10-01T00:00:00Z", "to": "2026-11-01T00:00:00Z"}
        )
    ).json()

    assert [bucket["floor_level"] for bucket in body["buckets"]] == ["critical", "low", "none"]


async def test_an_offense_of_a_case_is_not_needed_for_the_metric(api: Harness) -> None:
    """The metric reads `cases`, so a case whose offense row is gone still counts."""
    await decided_case(api.sessions)
    await api.rows("DELETE FROM offenses_seen RETURNING offense_id")

    body = (
        await api.get(
            "/metrics/sla", **{"from": "2026-10-01T00:00:00Z", "to": "2026-11-01T00:00:00Z"}
        )
    ).json()

    assert sum(bucket["total"] for bucket in body["buckets"]) == 1
