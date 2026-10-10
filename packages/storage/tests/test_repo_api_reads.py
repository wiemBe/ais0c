"""The storage functions T-028 added for the analyst API.

Only what the API needs: the case list's rule filter and cursor, the SLA metric, the QA queue and
its resolve, operator feedback, the group list and its offenses, the catalog's `qradar_enabled`
and `missing` filters and paging, the recipient groups' replacement and the domain check, the
routing table's replacement, the platform flag list, and the run, tool-call and evidence reads the
case detail is built from.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
import storage_payloads as payloads
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from storage_payloads import EVIDENCE_ID, T0, T1

from ais0c_contracts import (
    CaseSource,
    CaseVerdict,
    CatalogMode,
    Confidence,
    EmailKind,
    FeedbackReason,
    Level,
    OperatorFeedback,
    QAReason,
    ToolStatus,
)
from ais0c_storage.enums import (
    ActorKind,
    CaseStatus,
    CriticalAssetKind,
    GroupStatus,
    OffenseStatus,
    PlatformFlag,
    PolicyDecision,
    QAStatus,
)
from ais0c_storage.models import CaseRow, CatalogLogSourceRow, CatalogRuleRow
from ais0c_storage.repositories import (
    SlaBucket,
    SyncedLogSource,
    SyncedRule,
    add_allowed_email_domain,
    add_critical_asset,
    add_notification_recipient,
    add_offense_seen,
    add_operator_feedback,
    add_qa_items,
    begin_case_reevaluation,
    check_recipient_domains,
    create_case,
    create_offense_group,
    get_critical_asset,
    get_evidence,
    get_qa_item,
    list_agent_runs,
    list_cases,
    list_cases_by_ids,
    list_catalog_log_sources,
    list_catalog_rules,
    list_evidence_by_ids,
    list_group_offenses,
    list_notification_recipients,
    list_notification_routes,
    list_offense_groups,
    list_offenses_by_ids,
    list_operator_feedback,
    list_platform_flags,
    list_qa_items,
    list_qa_queue,
    list_tool_calls_of_runs,
    list_tools_for_evidence,
    newest_case_cursor,
    newest_group_cursor,
    record_case_decision,
    record_evidence,
    record_tool_call,
    replace_notification_recipients,
    replace_notification_routes,
    resolve_qa_item,
    set_case_status,
    set_platform_flag,
    sla_metrics,
    start_agent_run,
    sync_catalog_log_sources,
    sync_catalog_rules,
)

pytestmark = pytest.mark.anyio

MISSING_AT = datetime(2026, 10, 3, 3, 0, tzinfo=UTC)


async def open_case(
    session: AsyncSession,
    case_id: str,
    offense_id: int | None = 1,
    *,
    sla_due_at: datetime = T1,
) -> None:
    """A case of an offense, or of a hunt when `offense_id` is None."""
    await create_case(
        session,
        case_id=case_id,
        source=CaseSource.HUNT if offense_id is None else CaseSource.OFFENSE,
        offense_id=offense_id,
        hunt_id="hunt-1" if offense_id is None else None,
        sla_due_at=sla_due_at,
        workflow_id=case_id,
        run_id=f"run-{case_id}",
    )


async def decide(
    session: AsyncSession, case_id: str, *, floor: Level | None, decided_at: datetime
) -> None:
    await record_case_decision(
        session,
        case_id,
        verdict=CaseVerdict.SUSPICIOUS,
        confidence=Confidence.MEDIUM,
        ai_level=Level.HIGH,
        notify_level=Level.HIGH,
        floor_level=floor,
        decided_at=decided_at,
    )


def case_ids(rows: Sequence[CaseRow]) -> list[str]:
    return [row.case_id for row in rows]


# --- cases: the rule filter, the cursor and the SLA metric --------------------------------------


async def test_the_case_list_filters_by_the_rule_of_the_offense(session: AsyncSession) -> None:
    for offense_id, rule_ids in ((1, [100201, 100202]), (2, [100305])):
        await add_offense_seen(
            session,
            offense_id=offense_id,
            first_seen_at=T0,
            last_updated_at=T0,
            description=f"offense {offense_id}",
            rule_ids=rule_ids,
            catalog_mode=CatalogMode.ANALYZE,
            pre_priority=0,
        )
    await open_case(session, "case-1", 1)
    await open_case(session, "case-2", 2)
    await open_case(session, "case-hunt-hunt-1-1", None)

    # All three share the transaction's created_at, so case_id decides the order.
    assert case_ids(await list_cases(session, rule_ids={100201})) == ["case-1"]
    assert case_ids(await list_cases(session, rule_ids={100202})) == ["case-1"]
    assert case_ids(await list_cases(session, rule_ids={100201, 100305})) == ["case-1", "case-2"]
    # A hunt case carries no rule, so no rule filter ever keeps it.
    assert case_ids(await list_cases(session, rule_ids={100201, 100202, 100305})) == [
        "case-1",
        "case-2",
    ]


async def test_the_case_list_pages_with_a_cursor(session: AsyncSession) -> None:
    for offense_id in (1, 2, 3):
        await open_case(session, f"case-{offense_id}", offense_id)
        # One transaction per case, so created_at (transaction time) differs.
        await session.commit()

    first = await list_cases(session, limit=2)
    assert case_ids(first) == ["case-3", "case-2"]

    second = await list_cases(session, after=newest_case_cursor(first[-1]), limit=2)
    assert case_ids(second) == ["case-1"]

    assert await list_cases(session, after=newest_case_cursor(second[-1])) == []


async def test_the_case_cursor_keeps_the_order_of_cases_created_together(
    session: AsyncSession,
) -> None:
    """Cases opened in one transaction share `created_at`; the order is then `case_id`
    ascending, and a page that ends among them goes on with the next ID, skipping and repeating
    none."""
    for offense_id in (1, 2, 3, 4):
        await open_case(session, f"case-{offense_id}", offense_id)
    await session.commit()
    await open_case(session, "case-5", 5)
    await session.commit()

    everything = case_ids(await list_cases(session))
    assert everything == ["case-5", "case-1", "case-2", "case-3", "case-4"]

    seen: list[str] = []
    page = await list_cases(session, limit=2)
    while page:
        seen.extend(case_ids(page))
        page = await list_cases(session, after=newest_case_cursor(page[-1]), limit=2)
    assert seen == everything


async def test_cases_are_read_by_id(session: AsyncSession) -> None:
    await open_case(session, "case-1", 1)
    await open_case(session, "case-2", 2)

    found = await list_cases_by_ids(session, ["case-1", "case-404"])

    assert set(found) == {"case-1"}
    assert found["case-1"].offense_id == 1
    assert await list_cases_by_ids(session, []) == {}


async def test_the_sla_metric_counts_each_floor_over_the_range(session: AsyncSession) -> None:
    """Every bucket has at least one case: on time, late and still running (criterion 10)."""
    await open_case(session, "case-on-time")
    await decide(session, "case-on-time", floor=Level.HIGH, decided_at=T1 - timedelta(minutes=5))
    await open_case(session, "case-late")
    await decide(session, "case-late", floor=Level.MEDIUM, decided_at=T1 + timedelta(minutes=5))
    # No floor and no decision yet: it is still running.
    await open_case(session, "case-still-running")
    # Outside the range: its deadline is an hour after the window ends.
    await open_case(session, "case-outside", sla_due_at=T1 + timedelta(hours=1))

    buckets = await sla_metrics(session, sla_due_from=T1, sla_due_to=T1 + timedelta(minutes=30))

    assert [(bucket.floor_level, bucket.total) for bucket in buckets] == [
        (Level.HIGH, 1),
        (Level.MEDIUM, 1),
        (None, 1),
    ]
    assert [
        (bucket.on_time, bucket.late, bucket.undecided, bucket.running, bucket.closed)
        for bucket in buckets
    ] == [(1, 0, 0, 0, 0), (0, 1, 0, 0, 0), (0, 0, 0, 1, 0)]
    assert all(bucket.total == adds_up(bucket) for bucket in buckets)


def adds_up(bucket: SlaBucket) -> int:
    return bucket.on_time + bucket.late + bucket.undecided + bucket.running + bucket.closed


async def test_a_running_re_evaluation_counts_as_running_not_as_its_old_decision(
    session: AsyncSession,
) -> None:
    """A re-evaluation keeps the previous decision's `decided_at` until it records its own; the
    metric counts where the latest evaluation stands, once."""
    await open_case(session, "case-1")
    await decide(session, "case-1", floor=Level.HIGH, decided_at=T1 - timedelta(minutes=5))
    await begin_case_reevaluation(session, "case-1", sla_due_at=T1 + timedelta(minutes=10))

    (bucket,) = await sla_metrics(session, sla_due_from=T1, sla_due_to=T1 + timedelta(hours=1))

    assert (bucket.total, bucket.on_time, bucket.running) == (1, 0, 1)
    assert bucket.total == adds_up(bucket)

    # The re-evaluation misses its deadline: undecided, not on time by the old decision.
    await set_case_status(session, "case-1", CaseStatus.NO_AI_DECISION)
    (bucket,) = await sla_metrics(session, sla_due_from=T1, sla_due_to=T1 + timedelta(hours=1))
    assert (bucket.total, bucket.on_time, bucket.undecided) == (1, 0, 1)


async def test_a_closed_case_counts_by_its_decision_or_as_closed(session: AsyncSession) -> None:
    await open_case(session, "case-decided-then-closed")
    await decide(
        session, "case-decided-then-closed", floor=None, decided_at=T1 + timedelta(minutes=5)
    )
    await set_case_status(session, "case-decided-then-closed", CaseStatus.CLOSED)
    # Closed in QRadar while the AI was still on it.
    await open_case(session, "case-closed-first")
    await set_case_status(session, "case-closed-first", CaseStatus.CLOSED)

    (bucket,) = await sla_metrics(session, sla_due_from=T0, sla_due_to=T1 + timedelta(hours=1))

    assert (bucket.total, bucket.late, bucket.closed) == (2, 1, 1)
    assert bucket.total == adds_up(bucket)


async def test_the_sla_metric_counts_a_case_without_a_decision_as_undecided(
    session: AsyncSession,
) -> None:
    await open_case(session, "case-1")
    await set_case_status(session, "case-1", CaseStatus.NO_AI_DECISION)

    buckets = await sla_metrics(session, sla_due_from=T0, sla_due_to=T1 + timedelta(hours=1))

    assert [(bucket.undecided, bucket.running, bucket.total) for bucket in buckets] == [(1, 0, 1)]
    assert all(bucket.total == adds_up(bucket) for bucket in buckets)


# --- the QA queue -------------------------------------------------------------------------------


async def test_the_qa_queue_filters_and_pages(session: AsyncSession) -> None:
    await open_case(session, "case-1")
    await open_case(session, "case-2")
    first = await add_qa_items(session, "case-1", 1, [QAReason.RANDOM_SAMPLE])
    await add_qa_items(session, "case-2", 1, [QAReason.VERIFIER_CONFLICT, QAReason.LOW_CONFIDENCE])
    await session.commit()

    everything = await list_qa_queue(session)
    ids = [row.id for row in everything]
    assert ids[0] == first[0].id
    assert len(ids) == 3

    page = await list_qa_queue(session, limit=2)
    assert [row.id for row in page] == ids[:2]
    assert [row.id for row in await list_qa_queue(session, after=page[-1].id)] == ids[2:]

    # IDs made in one process strictly increase (ids.py), so the queue keeps the order the items
    # were opened in.
    assert [(row.case_id, row.reason) for row in everything] == [
        ("case-1", QAReason.RANDOM_SAMPLE),
        ("case-2", QAReason.VERIFIER_CONFLICT),
        ("case-2", QAReason.LOW_CONFIDENCE),
    ]
    low = await list_qa_queue(session, reasons={QAReason.LOW_CONFIDENCE})
    assert [(row.case_id, row.reason) for row in low] == [("case-2", QAReason.LOW_CONFIDENCE)]
    assert [row.id for row in await list_qa_queue(session, statuses={QAStatus.OPEN})] == ids
    assert await list_qa_queue(session, statuses={QAStatus.RESOLVED}) == []
    # The per-case list still reads one case.
    assert len(await list_qa_items(session, "case-1")) == 1


async def test_resolving_an_item_records_who_and_when(session: AsyncSession) -> None:
    await open_case(session, "case-1")
    item = (await add_qa_items(session, "case-1", 1, [QAReason.INJECTION_SUSPECTED]))[0]

    resolved = await resolve_qa_item(session, item.id, resolved_by="operator01", resolved_at=T1)

    assert resolved is not None
    assert (resolved.status, resolved.resolved_by, resolved.resolved_at) == (
        QAStatus.RESOLVED,
        "operator01",
        T1,
    )
    assert await get_qa_item(session, item.id) == resolved
    assert (
        await resolve_qa_item(session, uuid.uuid4(), resolved_by="operator01", resolved_at=T1)
        is None
    )


async def test_an_item_is_resolved_only_once(session: AsyncSession) -> None:
    """The second resolve changes nothing: the first operator and time stay."""
    await open_case(session, "case-1")
    item = (await add_qa_items(session, "case-1", 1, [QAReason.LOW_CONFIDENCE]))[0]
    await resolve_qa_item(session, item.id, resolved_by="operator01", resolved_at=T0)

    again = await resolve_qa_item(session, item.id, resolved_by="operator02", resolved_at=T1)

    assert again is None
    stored = await get_qa_item(session, item.id)
    assert stored is not None
    assert (stored.resolved_by, stored.resolved_at) == ("operator01", T0)


async def test_operator_feedback_is_recorded_with_its_author(session: AsyncSession) -> None:
    await open_case(session, "case-1")
    feedback = OperatorFeedback(
        case_id="case-1", verdict=CaseVerdict.TP, reason=FeedbackReason.WAS_FP_NOT_TP
    )

    row = await add_operator_feedback(session, feedback, user_subject="operator01")

    assert (row.case_id, row.user_subject, row.verdict, row.comment) == (
        "case-1",
        "operator01",
        CaseVerdict.TP,
        None,
    )
    assert row.created_at is not None
    assert await list_operator_feedback(session, "case-1") == [row]
    assert await list_operator_feedback(session, "case-2") == []
    with pytest.raises(ValueError, match="user_subject"):
        await add_operator_feedback(session, feedback, user_subject="  ")


# --- groups -------------------------------------------------------------------------------------


async def test_groups_are_listed_filtered_and_paged(session: AsyncSession) -> None:
    await create_offense_group(
        session,
        group_id="g-1",
        rule_set_hash="hash-1",
        window_start=T0,
        window_end=T0 + timedelta(minutes=10),
        offense_count=2,
    )
    await create_offense_group(
        session,
        group_id="g-2",
        rule_set_hash="hash-1",
        window_start=T1,
        window_end=T1 + timedelta(minutes=10),
        offense_count=5,
        status=GroupStatus.STORM,
    )
    await session.commit()

    assert [row.group_id for row in await list_offense_groups(session)] == ["g-2", "g-1"]
    assert [
        row.group_id for row in await list_offense_groups(session, statuses={GroupStatus.OPEN})
    ] == ["g-1"]
    assert [row.group_id for row in await list_offense_groups(session, window_from=T1)] == ["g-2"]

    page = await list_offense_groups(session, limit=1)
    assert [row.group_id for row in page] == ["g-2"]
    rest = await list_offense_groups(session, after=newest_group_cursor(page[-1]))
    assert [row.group_id for row in rest] == ["g-1"]


async def test_the_group_cursor_keeps_the_order_of_groups_with_one_window_start(
    session: AsyncSession,
) -> None:
    for group_id in ("g-a", "g-b", "g-c"):
        await create_offense_group(
            session,
            group_id=group_id,
            rule_set_hash=f"hash-{group_id}",
            window_start=T0,
            window_end=T0 + timedelta(minutes=10),
            offense_count=2,
        )
    await session.commit()

    page = await list_offense_groups(session, limit=1)
    seen: list[str] = []
    while page:
        seen.extend(row.group_id for row in page)
        page = await list_offense_groups(session, after=newest_group_cursor(page[-1]), limit=1)

    assert seen == ["g-a", "g-b", "g-c"]


async def test_the_offenses_of_a_group_are_read_by_id(session: AsyncSession) -> None:
    for offense_id in (3, 1, 2):
        await add_offense_seen(
            session,
            offense_id=offense_id,
            first_seen_at=T0,
            last_updated_at=T0,
            description=f"offense {offense_id}",
            rule_ids=[100201],
            catalog_mode=CatalogMode.ANALYZE,
            pre_priority=0,
            group_id="g-1",
            status=OffenseStatus.GROUPED,
        )
    await add_offense_seen(
        session,
        offense_id=9,
        first_seen_at=T0,
        last_updated_at=T0,
        description="alone",
        rule_ids=[100201],
        catalog_mode=CatalogMode.ANALYZE,
        pre_priority=0,
    )

    assert [row.offense_id for row in await list_group_offenses(session, "g-1")] == [1, 2, 3]
    assert await list_group_offenses(session, "g-none") == []
    assert sorted(await list_offenses_by_ids(session, [2, 9, 404])) == [2, 9]
    assert await list_offenses_by_ids(session, []) == {}


# --- the catalog's filters and paging ------------------------------------------------------------


async def test_the_catalog_filters_by_qradar_state_and_missing(session: AsyncSession) -> None:
    await sync_catalog_rules(
        session,
        [
            SyncedRule(100201, "Excessive Firewall Accepts", qradar_enabled=True),
            SyncedRule(100202, "Repeated Logon Failures", qradar_enabled=False),
            SyncedRule(100203, "DCSync", qradar_enabled=True),
        ],
        synced_by="knowledge-sync",
        synced_at=T0,
    )
    await sync_catalog_log_sources(
        session,
        [
            SyncedLogSource(2001, "SRV-0001.example.com", "Windows Security"),
            SyncedLogSource(2002, "SRV-0002.example.com", "FortiGate"),
        ],
        synced_by="knowledge-sync",
        synced_at=T0,
    )
    await session.execute(
        text("UPDATE catalog_rules SET missing_since = :at WHERE rule_id = 100203"),
        {"at": MISSING_AT},
    )
    await session.execute(
        text("UPDATE catalog_log_sources SET missing_since = :at WHERE log_source_id = 2002"),
        {"at": MISSING_AT},
    )

    def rule_ids(rows: Sequence[CatalogRuleRow]) -> list[int]:
        return [row.rule_id for row in rows]

    def source_ids(rows: Sequence[CatalogLogSourceRow]) -> list[int]:
        return [row.log_source_id for row in rows]

    assert rule_ids(await list_catalog_rules(session, qradar_enabled=False)) == [100202]
    assert rule_ids(await list_catalog_rules(session, missing=True)) == [100203]
    assert rule_ids(await list_catalog_rules(session, missing=False)) == [100201, 100202]
    assert rule_ids(await list_catalog_rules(session, qradar_enabled=True, missing=False)) == [
        100201
    ]
    assert source_ids(await list_catalog_log_sources(session, missing=True)) == [2002]
    assert source_ids(await list_catalog_log_sources(session, missing=False)) == [2001]


async def test_the_catalog_pages_by_its_ids(session: AsyncSession) -> None:
    await sync_catalog_rules(
        session,
        [SyncedRule(rule_id, f"rule {rule_id}") for rule_id in (1, 2, 3)],
        synced_by="knowledge-sync",
        synced_at=T0,
    )
    await sync_catalog_log_sources(
        session,
        [SyncedLogSource(source_id, f"source {source_id}", "FortiGate") for source_id in (4, 5)],
        synced_by="knowledge-sync",
        synced_at=T0,
    )

    page = await list_catalog_rules(session, limit=2)
    assert [row.rule_id for row in page] == [1, 2]
    assert [
        row.rule_id for row in await list_catalog_rules(session, after_rule_id=page[-1].rule_id)
    ] == [3]
    sources = await list_catalog_log_sources(session, limit=1)
    assert [row.log_source_id for row in sources] == [4]
    assert [
        row.log_source_id
        for row in await list_catalog_log_sources(
            session, after_log_source_id=sources[-1].log_source_id
        )
    ] == [5]
    # No limit means no limit: the sync reads every entry.
    assert len(await list_catalog_rules(session)) == 3


# --- recipient groups and the routing table ------------------------------------------------------


async def test_a_group_is_replaced_with_the_given_members(session: AsyncSession) -> None:
    await add_notification_recipient(session, list_name="operators", email="soc-1@example.com")
    await add_notification_recipient(session, list_name="exec", email="chief@example.com")

    # Only the domain is lowercased, the local part is kept; an address repeated after that
    # normalization is written once, so the group ends up with two members.
    rows = await replace_notification_recipients(
        session, "operators", ["soc-2@EXAMPLE.COM", "soc-1@example.com", "soc-2@example.com"]
    )

    assert sorted(row.email for row in rows) == ["soc-1@example.com", "soc-2@example.com"]
    assert [(row.list_name, row.email) for row in await list_notification_recipients(session)] == [
        ("exec", "chief@example.com"),
        ("operators", "soc-1@example.com"),
        ("operators", "soc-2@example.com"),
    ]
    assert await replace_notification_recipients(session, "operators", []) == []
    assert [(row.list_name, row.email) for row in await list_notification_recipients(session)] == [
        ("exec", "chief@example.com")
    ]


async def test_replacing_a_group_refuses_a_bad_name_or_address(session: AsyncSession) -> None:
    with pytest.raises(ValueError, match=r"\[a-z\]\[a-z0-9-\]"):
        await replace_notification_recipients(session, "Operators", ["soc-1@example.com"])
    with pytest.raises(ValueError, match="plain ASCII"):
        await replace_notification_recipients(session, "operators", ["not-an-address"])
    assert await list_notification_recipients(session) == []


async def test_the_domain_allowlist_check_normalizes_and_reports(session: AsyncSession) -> None:
    await add_allowed_email_domain(session, "example.com")

    assert await check_recipient_domains(session, ["Example.COM", "  Example.com "]) == []
    assert await check_recipient_domains(session, ["example.net", "example.com"]) == ["example.net"]
    # A name that is not a domain at all is not allowed either.
    assert await check_recipient_domains(session, ["localhost"]) == ["localhost"]
    assert await check_recipient_domains(session, []) == []


async def test_the_routing_table_is_replaced_as_a_whole(session: AsyncSession) -> None:
    """0007 seeds the routes; a replacement is the whole table, and drops what it leaves out."""
    assert len(await list_notification_routes(session)) > 0

    rows = await replace_notification_routes(
        session,
        [
            (EmailKind.CASE_ALERT, Level.HIGH, "operators"),
            (EmailKind.CASE_ALERT, Level.HIGH, "operators"),
            (EmailKind.HUNT_REPORT, None, "hunters"),
        ],
    )

    assert [(row.kind, row.level, row.list_name) for row in rows] == [
        (EmailKind.CASE_ALERT, Level.HIGH, "operators"),
        (EmailKind.HUNT_REPORT, None, "hunters"),
    ]
    assert [
        (row.kind, row.level, row.list_name) for row in await list_notification_routes(session)
    ] == [
        (EmailKind.CASE_ALERT, Level.HIGH, "operators"),
        (EmailKind.HUNT_REPORT, None, "hunters"),
    ]
    assert await replace_notification_routes(session, []) == []
    assert await list_notification_routes(session) == []


# --- flags, runs, tool calls, evidence and critical assets ----------------------------------------


async def test_every_flag_with_a_row_is_listed(session: AsyncSession) -> None:
    assert await list_platform_flags(session) == []

    await set_platform_flag(
        session,
        PlatformFlag.WRITES_ENABLED,
        enabled=True,
        reason="canary starts on the selected offenses",
        actor_kind=ActorKind.USER,
        actor_id="admin01",
    )

    flags = await list_platform_flags(session)
    assert [(row.name, row.enabled) for row in flags] == [(PlatformFlag.WRITES_ENABLED, True)]


async def test_the_tool_calls_of_several_runs_are_read_at_once(session: AsyncSession) -> None:
    """One query for the calls of a case's runs, each run's in recorded order."""
    await open_case(session, "case-1")
    for run_id in ("case-1-triage-1", "case-1-investigation-1", "case-1-reporting-1"):
        await start_agent_run(
            session,
            run_id=run_id,
            task=payloads.agent_task(case_id="case-1"),
            prompt_version="v1",
            model_alias="soc-fast",
            model_target="lab-model",
            toolset_profile="qradar-triage-read",
            started_at=T0,
        )
    for run_id, evidence_id in (
        ("case-1-triage-1", "ev_1"),
        ("case-1-investigation-1", "ev_2"),
        ("case-1-investigation-1", "ev_3"),
    ):
        await record_tool_call(
            session,
            run_id=run_id,
            intent=payloads.tool_intent(),
            policy_decision=PolicyDecision.ALLOW,
            status=ToolStatus.OK,
            latency_ms=12,
            evidence_id=evidence_id,
        )
        # One transaction per call, so `created_at` orders them.
        await session.commit()

    calls = await list_tool_calls_of_runs(
        session, ["case-1-triage-1", "case-1-investigation-1", "case-1-reporting-1"]
    )
    assert [(call.run_id, call.evidence_id) for call in calls] == [
        ("case-1-triage-1", "ev_1"),
        ("case-1-investigation-1", "ev_2"),
        ("case-1-investigation-1", "ev_3"),
    ]
    assert [
        call.evidence_id
        for call in await list_tool_calls_of_runs(session, ["case-1-investigation-1"])
    ] == ["ev_2", "ev_3"]
    assert await list_tool_calls_of_runs(session, []) == []


async def test_the_tool_of_an_evidence_is_read_from_its_call(session: AsyncSession) -> None:
    await open_case(session, "case-1")
    await start_agent_run(
        session,
        run_id="case-1-triage-1",
        task=payloads.agent_task(),
        prompt_version="v1",
        model_alias="soc-fast",
        model_target="lab-model",
        toolset_profile="qradar-triage-read",
        started_at=T0,
    )
    await record_tool_call(
        session,
        run_id="case-1-triage-1",
        intent=payloads.tool_intent(),
        policy_decision=PolicyDecision.ALLOW,
        status=ToolStatus.OK,
        latency_ms=12,
        evidence_id=EVIDENCE_ID,
    )

    assert await list_tools_for_evidence(session, [EVIDENCE_ID]) == {
        EVIDENCE_ID: "qradar.ariel_search"
    }
    assert await list_tools_for_evidence(session, ["ev_missing"]) == {}
    assert await list_tools_for_evidence(session, []) == {}


async def test_evidence_is_read_by_ids(session: AsyncSession) -> None:
    await record_evidence(session, payloads.evidence_ref("ev_b"))
    await record_evidence(session, payloads.evidence_ref("ev_a"))

    rows = await list_evidence_by_ids(session, ["ev_a", "ev_b", "ev_missing"])
    assert [row.evidence_id for row in rows] == ["ev_a", "ev_b"]
    assert await list_evidence_by_ids(session, []) == []
    stored = await get_evidence(session, "ev_a")
    assert stored is not None
    assert stored.query_hash == "sha256:5d41402abc4b2a76"


async def test_a_critical_asset_is_read_by_its_id(session: AsyncSession) -> None:
    assert await get_critical_asset(session, uuid.uuid4()) is None

    row = await add_critical_asset(
        session,
        kind=CriticalAssetKind.HOST,
        value="dc-lab-01.example.com",
        label="DC",
        level=Level.CRITICAL,
    )

    assert await get_critical_asset(session, row.id) == row


async def test_a_case_with_a_run_and_a_tool_call_reads_both(session: AsyncSession) -> None:
    """The two reads the case steps endpoint is built on, end to end."""
    await open_case(session, "case-1")
    await start_agent_run(
        session,
        run_id="case-1-triage-1",
        task=payloads.agent_task(case_id="case-1"),
        prompt_version="v1",
        model_alias="soc-fast",
        model_target="lab-model",
        toolset_profile="qradar-triage-read",
        started_at=T0,
    )
    await record_tool_call(
        session,
        run_id="case-1-triage-1",
        intent=payloads.tool_intent(),
        policy_decision=PolicyDecision.ALLOW,
        status=ToolStatus.OK,
        latency_ms=12,
        evidence_id=EVIDENCE_ID,
    )

    runs = await list_agent_runs(session, case_id="case-1")

    assert [run.run_id for run in runs] == ["case-1-triage-1"]
