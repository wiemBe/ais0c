"""Case activities on a real database: opening the case, SLA deadlines, decisions, closure."""

from collections.abc import Sequence
from datetime import timedelta

import pytest
from activity_db import (
    case,
    catalog_log_source,
    catalog_rule,
    critical_asset,
    gone_from_qradar,
    seen,
)
from activity_payloads import T0, offense, triage_result
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ais0c_activities import (
    CaseActivities,
    CaseSettings,
    FakeOffenseSource,
    IntakeActivities,
    SessionFactory,
    sample_applies,
    sample_value,
    sampled,
)
from ais0c_activities.levels import at_least
from ais0c_contracts import (
    ActionType,
    CaseReport,
    CaseVerdict,
    Confidence,
    CriticalAssetHit,
    Level,
    OffenseSnapshot,
    QAReason,
    Recommendation,
    RunStatus,
    TriageResult,
    UrgentEvent,
    Usage,
)
from ais0c_storage.enums import CaseStatus, CriticalAssetKind, OffenseStatus, QAStatus
from ais0c_storage.repositories import (
    SyncedRule,
    list_qa_items,
    list_recommendations,
    list_urgent_events,
    sync_catalog_rules,
)

pytestmark = pytest.mark.anyio

CASE_ID = "case-7"


@pytest.fixture
def source() -> FakeOffenseSource:
    return FakeOffenseSource([offense(7)])


@pytest.fixture
def activities(sessions: SessionFactory, source: FakeOffenseSource) -> CaseActivities:
    return CaseActivities(sessions=sessions, source=source, settings=CaseSettings())


async def start(
    activities: CaseActivities,
    evaluation_no: int,
    snapshot: OffenseSnapshot,
    floor: Level | None = None,
    run_id: str = "run-1",
) -> object:
    return await ActivityEnvironment().run(
        activities.start_evaluation, CASE_ID, evaluation_no, snapshot, floor, CASE_ID, run_id
    )


async def decide(
    activities: CaseActivities,
    evaluation_no: int,
    result: TriageResult,
    floor: Level | None,
    *,
    report: CaseReport | None = None,
    qa_reasons: Sequence[QAReason] = (),
    rule_ids: Sequence[int] = (100201,),
) -> Level:
    """Record `result`'s decision as the workflow does: its level is max(AI level, floor)."""
    return await ActivityEnvironment().run(
        activities.record_decision,
        CASE_ID,
        evaluation_no,
        result.verdict,
        result.confidence,
        result.ai_level,
        at_least(result.ai_level, floor),
        floor,
        report,
        list(qa_reasons),
        list(rule_ids),
        T0 + timedelta(hours=1),
    )


async def test_the_first_evaluation_opens_the_case_with_its_sla(
    sessions: SessionFactory, activities: CaseActivities
) -> None:
    due = await start(activities, 1, offense(7), Level.HIGH)

    assert due == T0 + timedelta(minutes=10)
    row = await case(sessions, CASE_ID)
    assert row is not None
    assert (row.status, row.evaluation_no, row.offense_id, row.workflow_id) == (
        CaseStatus.RUNNING,
        1,
        7,
        CASE_ID,
    )
    # A retried start changes nothing.
    assert await start(activities, 1, offense(7), None) == due


async def test_a_reevaluation_uses_the_update_time_and_the_previous_level(
    sessions: SessionFactory, activities: CaseActivities
) -> None:
    await start(activities, 1, offense(7))
    assert await decide(activities, 1, triage_result(Level.HIGH), None) is Level.HIGH

    updated = offense(7, updated=T0 + timedelta(hours=2))
    due = await start(activities, 2, updated, None, run_id="run-2")
    assert await start(activities, 2, updated, None, run_id="run-2") == due

    assert due == T0 + timedelta(hours=2, minutes=10)
    row = await case(sessions, CASE_ID)
    assert row is not None
    assert (row.evaluation_no, row.status, row.run_id) == (2, CaseStatus.RUNNING, "run-2")
    # The previous decision stays until the next one is recorded.
    assert row.notify_level is Level.HIGH


async def test_an_evaluation_out_of_step_with_the_case_is_rejected(
    activities: CaseActivities,
) -> None:
    await start(activities, 1, offense(7))

    with pytest.raises(ApplicationError) as error:
        await start(activities, 3, offense(7))
    assert error.value.non_retryable

    with pytest.raises(ApplicationError):
        await decide(activities, 2, triage_result(), None)


async def test_the_ai_cannot_take_a_case_below_its_floor(
    sessions: SessionFactory, activities: CaseActivities
) -> None:
    await start(activities, 1, offense(7), Level.HIGH)

    notify = await decide(activities, 1, triage_result(Level.LOW, CaseVerdict.FP), Level.HIGH)

    assert notify is Level.HIGH
    row = await case(sessions, CASE_ID)
    assert row is not None
    assert (row.status, row.verdict, row.confidence) == (
        CaseStatus.DECIDED,
        CaseVerdict.FP,
        Confidence.MEDIUM,
    )
    assert (row.ai_level, row.floor_level, row.notify_level) == (Level.LOW, Level.HIGH, Level.HIGH)
    assert row.decided_at == T0 + timedelta(hours=1)


def report(
    result: TriageResult, notify_level: Level, *, events: int = 2, recommendations: int = 1
) -> CaseReport:
    event = UrgentEvent(
        rank=1,
        time=T0,
        log_source="DC-01",
        event_name="Directory Service Access",
        reason="Replication by a non-machine account.",
        checklist=[],
        evidence_id="ev_1",
    )
    advice = Recommendation(
        action_type=ActionType.INVESTIGATE_FURTHER,
        target="svc_backup",
        rationale="Confirm the account's purpose.",
        evidence_ids=["ev_1"],
    )
    return CaseReport(
        task_id="case-7-reporting-1",
        status=RunStatus.COMPLETED,
        claims=[],
        data_gaps=[],
        injection_suspected=False,
        usage=Usage(tokens=0, tool_calls=0, seconds=0.0),
        summary_tr="Sentetik rapor.",
        verdict=result.verdict,
        confidence=result.confidence,
        notify_level=notify_level,
        urgent_events=[event.model_copy(update={"rank": n}) for n in range(1, events + 1)],
        recommendations=[advice] * recommendations,
    )


async def test_a_decision_is_recorded_with_its_report_events_and_review(
    sessions: SessionFactory, activities: CaseActivities
) -> None:
    """T-026 criteria 6 and 7: the report, its urgent events and recommendations under the
    evaluation's number, and one open QA item per reason."""
    await start(activities, 1, offense(7))
    result = triage_result(Level.HIGH)
    written = report(result, Level.HIGH)

    level = await decide(
        activities,
        1,
        result,
        None,
        report=written,
        qa_reasons=[QAReason.VERIFIER_CONFLICT, QAReason.LOW_CONFIDENCE],
    )

    assert level is Level.HIGH
    row = await case(sessions, CASE_ID)
    assert row is not None
    assert (row.status, row.report) == (CaseStatus.DECIDED, written)
    async with sessions() as session:
        events = await list_urgent_events(session, CASE_ID)
        advice = await list_recommendations(session, CASE_ID)
        items = await list_qa_items(session, CASE_ID)
    assert [(e.evaluation_no, e.rank, e.event) for e in events] == [
        (1, 1, written.urgent_events[0]),
        (1, 2, written.urgent_events[1]),
    ]
    assert [(a.evaluation_no, a.recommendation) for a in advice] == [
        (1, written.recommendations[0])
    ]
    assert sorted((item.reason, item.status) for item in items) == [
        (QAReason.LOW_CONFIDENCE, QAStatus.OPEN),
        (QAReason.VERIFIER_CONFLICT, QAStatus.OPEN),
    ]


async def test_a_retried_decision_writes_nothing_twice(
    sessions: SessionFactory, activities: CaseActivities
) -> None:
    """T-026 criteria 6 and 7: the same evaluation recorded again adds no rows."""
    await start(activities, 1, offense(7))
    result = triage_result(Level.HIGH)
    written = report(result, Level.HIGH)

    for _ in range(2):
        await decide(
            activities, 1, result, None, report=written, qa_reasons=[QAReason.LOW_CONFIDENCE]
        )

    async with sessions() as session:
        assert len(await list_urgent_events(session, CASE_ID)) == 2
        assert len(await list_recommendations(session, CASE_ID)) == 1
        assert len(await list_qa_items(session, CASE_ID)) == 1


async def test_each_evaluation_keeps_its_own_rows(
    sessions: SessionFactory, activities: CaseActivities
) -> None:
    await start(activities, 1, offense(7))
    first = triage_result(Level.HIGH)
    await decide(activities, 1, first, None, report=report(first, Level.HIGH))
    await start(activities, 2, offense(7, updated=T0 + timedelta(hours=2)), run_id="run-2")
    second = triage_result(Level.MEDIUM)
    await decide(
        activities,
        2,
        second,
        None,
        report=report(second, Level.MEDIUM, events=1, recommendations=0),
        qa_reasons=[QAReason.INJECTION_SUSPECTED],
    )

    async with sessions() as session:
        events = await list_urgent_events(session, CASE_ID)
        items = await list_qa_items(session, CASE_ID)
    assert [(e.evaluation_no, e.rank) for e in events] == [(1, 1), (1, 2), (2, 1)]
    assert [item.reason for item in items] == [QAReason.INJECTION_SUSPECTED]


async def test_a_decision_without_a_report_is_recorded(
    sessions: SessionFactory, activities: CaseActivities
) -> None:
    """T-026 criterion 3: Reporting gave no result."""
    await start(activities, 1, offense(7))

    await decide(activities, 1, triage_result(), None)

    row = await case(sessions, CASE_ID)
    assert row is not None
    assert (row.status, row.verdict, row.report) == (
        CaseStatus.DECIDED,
        CaseVerdict.SUSPICIOUS,
        None,
    )
    async with sessions() as session:
        assert await list_urgent_events(session, CASE_ID) == []


# The sample value of case-7's first evaluation (T-42 (5)): sha256("case-7:1"), first 8 bytes,
# modulo 10000. Inside a 30% sample (below 3000), outside a 10% one (not below 1000).
CASE_7_SAMPLE = 1523


async def test_fp_decisions_on_undefined_rules_are_sampled_more(
    sessions: SessionFactory, activities: CaseActivities
) -> None:
    """T-026 criterion 7: a low FP decision on a rule missing from the catalog falls in the 30%
    sample; on a defined rule it would need the 10% one."""
    assert sample_value(CASE_ID, 1) == CASE_7_SAMPLE
    await start(activities, 1, offense(7))

    await decide(activities, 1, triage_result(Level.LOW, CaseVerdict.FP), None)

    async with sessions() as session:
        items = await list_qa_items(session, CASE_ID)
    assert [item.reason for item in items] == [QAReason.RANDOM_SAMPLE]


async def test_fp_decisions_on_defined_rules_use_the_lower_rate(
    sessions: SessionFactory, activities: CaseActivities
) -> None:
    await catalog_rule(sessions, 100201)
    await start(activities, 1, offense(7))

    await decide(activities, 1, triage_result(Level.LOW, CaseVerdict.FP), None)

    async with sessions() as session:
        assert await list_qa_items(session, CASE_ID) == []


async def test_a_rule_only_synced_from_qradar_is_undefined(
    sessions: SessionFactory, activities: CaseActivities
) -> None:
    await catalog_rule(sessions, 100201)
    async with sessions.begin() as session:
        synced = SyncedRule(rule_id=100305, rule_name="Rule 100305")
        await sync_catalog_rules(session, [synced], synced_by="sync", synced_at=T0)
    await start(activities, 1, offense(7, rule_ids=(100201, 100305)))

    await decide(
        activities, 1, triage_result(Level.LOW, CaseVerdict.FP), None, rule_ids=(100201, 100305)
    )

    async with sessions() as session:
        items = await list_qa_items(session, CASE_ID)
    assert [item.reason for item in items] == [QAReason.RANDOM_SAMPLE]


@pytest.mark.parametrize(
    ("verdict", "ai_level", "floor"),
    [
        pytest.param(CaseVerdict.SUSPICIOUS, Level.LOW, None, id="not-fp"),
        pytest.param(CaseVerdict.FP, Level.HIGH, None, id="high"),
        pytest.param(CaseVerdict.FP, Level.LOW, Level.CRITICAL, id="critical-floor"),
    ],
)
async def test_only_low_and_medium_fp_decisions_are_sampled(
    sessions: SessionFactory,
    activities: CaseActivities,
    verdict: CaseVerdict,
    ai_level: Level,
    floor: Level | None,
) -> None:
    await start(activities, 1, offense(7))

    await decide(activities, 1, triage_result(ai_level, verdict), floor)

    async with sessions() as session:
        assert await list_qa_items(session, CASE_ID) == []


def test_the_sample_follows_the_rate() -> None:
    assert sampled(CASE_ID, 1, 30)
    assert not sampled(CASE_ID, 1, 10)
    assert sampled("case-4", 1, 10)  # sample value 38
    assert not sampled("case-4", 1, 0)
    assert all(sampled(f"case-{n}", 1, 100) for n in range(50))
    assert sample_applies(CaseVerdict.FP, Level.MEDIUM)
    assert not sample_applies(CaseVerdict.FP, Level.HIGH)
    assert not sample_applies(CaseVerdict.TP, Level.LOW)


async def test_no_ai_decision_applies_only_to_a_running_evaluation(
    sessions: SessionFactory, activities: CaseActivities
) -> None:
    env = ActivityEnvironment()
    await start(activities, 1, offense(7))
    await env.run(activities.mark_no_ai_decision, CASE_ID, 1)
    row = await case(sessions, CASE_ID)
    assert row is not None
    assert row.status is CaseStatus.NO_AI_DECISION

    # A late decision replaces it; marking again does not undo the decision.
    await decide(activities, 1, triage_result(), None)
    await env.run(activities.mark_no_ai_decision, CASE_ID, 1)
    row = await case(sessions, CASE_ID)
    assert row is not None
    assert row.status is CaseStatus.DECIDED


async def test_closing_marks_the_case_closed_and_the_offense_done(
    sessions: SessionFactory, source: FakeOffenseSource, activities: CaseActivities
) -> None:
    intake = IntakeActivities(sessions=sessions, source=source, settings=CaseSettings())
    env = ActivityEnvironment()
    await env.run(intake.admit_offenses, [offense(7)], T0)
    await start(activities, 1, offense(7))

    await env.run(activities.close_case, CASE_ID, 7)
    await env.run(activities.close_case, "case-404", 404)  # nothing to close: no error

    row = await case(sessions, CASE_ID)
    offense_row = await seen(sessions, 7)
    assert row is not None
    assert offense_row is not None
    assert (row.status, offense_row.status) == (CaseStatus.CLOSED, OffenseStatus.DONE)


async def test_enrichment_reads_the_catalog_and_the_critical_assets(
    sessions: SessionFactory, activities: CaseActivities
) -> None:
    await catalog_rule(sessions, 100201, min_level=Level.MEDIUM)
    await critical_asset(sessions, CriticalAssetKind.IP, "198.51.100.15", "SWIFT", Level.CRITICAL)

    enrichment = await ActivityEnvironment().run(activities.enrich_offense, offense(7))

    assert [(rule.rule_id, rule.min_level) for rule in enrichment.catalog.rules] == [
        (100201, Level.MEDIUM)
    ]
    assert enrichment.critical_asset_hits == [
        CriticalAssetHit(value="198.51.100.15", label="SWIFT", level=Level.CRITICAL)
    ]
    assert (enrichment.ioc_hits, enrichment.entity_resolutions) == ([], [])
    assert enrichment.floor_level is Level.CRITICAL


async def test_enrichment_carries_what_the_skill_router_reads(
    sessions: SessionFactory, activities: CaseActivities
) -> None:
    """The rules' ATT&CK techniques and the log sources' types (decision T-26)."""
    await catalog_rule(sessions, 100201, attack_techniques=["T1110.003", "T1110"])
    await catalog_log_source(sessions, 112, "Microsoft Windows Security Event Log")

    enrichment = await ActivityEnvironment().run(activities.enrich_offense, offense(7))

    assert [rule.attack_techniques for rule in enrichment.catalog.rules] == [["T1110", "T1110.003"]]
    assert [source.type_name for source in enrichment.catalog.log_sources] == [
        "Microsoft Windows Security Event Log"
    ]


async def test_enrichment_uses_entries_qradar_disabled_or_no_longer_lists(
    sessions: SessionFactory, activities: CaseActivities
) -> None:
    """T-37: old offenses still refer to them, so the floor and the context still count."""
    await catalog_rule(sessions, 100201, min_level=Level.HIGH, attack_techniques=["T1110"])
    await catalog_log_source(sessions, 112, "Microsoft Windows Security Event Log")
    await gone_from_qradar(sessions, rule_ids=[100201], log_source_ids=[112])

    enrichment = await ActivityEnvironment().run(activities.enrich_offense, offense(7))

    assert [
        (rule.rule_id, rule.min_level, rule.attack_techniques) for rule in enrichment.catalog.rules
    ] == [(100201, Level.HIGH, ["T1110"])]
    assert [source.log_source_id for source in enrichment.catalog.log_sources] == [112]
    assert enrichment.floor_level is Level.HIGH


async def test_fetching_an_unknown_offense_fails_without_retries(
    activities: CaseActivities,
) -> None:
    env = ActivityEnvironment()
    assert await env.run(activities.fetch_offense, 7) == offense(7)

    with pytest.raises(ApplicationError) as error:
        await env.run(activities.fetch_offense, 404)
    assert (error.value.type, error.value.non_retryable) == ("OffenseNotFound", True)


async def test_an_update_is_recorded_when_it_is_newer(
    sessions: SessionFactory, source: FakeOffenseSource, activities: CaseActivities
) -> None:
    """T-014 criterion 2: the case records the offense's latest state, evaluated or not."""
    intake = IntakeActivities(sessions=sessions, source=source, settings=CaseSettings())
    env = ActivityEnvironment()
    await env.run(intake.admit_offenses, [offense(7)], T0)
    v2 = T0 + timedelta(minutes=10)
    newer = offense(7, updated=v2, rule_ids=[100201, 100305])

    await env.run(activities.record_offense_update, newer)
    await env.run(activities.record_offense_update, offense(7, updated=T0 + timedelta(minutes=5)))
    await env.run(activities.record_offense_update, offense(404))  # not recorded: no error

    row = await seen(sessions, 7)
    assert row is not None
    assert (row.last_updated_at, row.rule_ids) == (v2, [100201, 100305])
    assert row.status is OffenseStatus.PENDING


async def test_the_workflow_gets_its_timings_from_the_settings(sessions: SessionFactory) -> None:
    activities = CaseActivities(
        sessions=sessions,
        source=FakeOffenseSource(),
        settings=CaseSettings(
            reevaluation_interval=timedelta(minutes=45), triage_retry_delay=timedelta(minutes=2)
        ),
    )
    env = ActivityEnvironment()

    assert await env.run(activities.reevaluation_interval) == timedelta(minutes=45)
    assert await env.run(activities.triage_retry_delay) == timedelta(minutes=2)
