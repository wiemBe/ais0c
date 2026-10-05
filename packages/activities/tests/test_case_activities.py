"""Case activities on a real database: opening the case, SLA deadlines, decisions, closure."""

from datetime import timedelta

import pytest
from activity_db import case, catalog_rule, critical_asset, seen
from activity_payloads import T0, offense, triage_result
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ais0c_activities import (
    CaseActivities,
    CaseSettings,
    FakeOffenseSource,
    IntakeActivities,
    SessionFactory,
)
from ais0c_contracts import (
    CaseVerdict,
    Confidence,
    CriticalAssetHit,
    Level,
    OffenseSnapshot,
    TriageResult,
)
from ais0c_storage.enums import CaseStatus, CriticalAssetKind, OffenseStatus

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
    activities: CaseActivities, evaluation_no: int, result: TriageResult, floor: Level | None
) -> Level:
    return await ActivityEnvironment().run(
        activities.record_decision, CASE_ID, evaluation_no, result, floor, T0 + timedelta(hours=1)
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


async def test_fetching_an_unknown_offense_fails_without_retries(
    activities: CaseActivities,
) -> None:
    env = ActivityEnvironment()
    assert await env.run(activities.fetch_offense, 7) == offense(7)

    with pytest.raises(ApplicationError) as error:
        await env.run(activities.fetch_offense, 404)
    assert (error.value.type, error.value.non_retryable) == ("OffenseNotFound", True)
