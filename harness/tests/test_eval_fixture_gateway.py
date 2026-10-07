"""The fixture gateway (T-030 criterion 2): it answers from the scenario, in order, and checks
every intent as the MCP Policy Gateway does."""

import asyncio

import pytest
from pydantic import JsonValue

from ais0c_contracts import ToolCoverage, ToolIntent, ToolResult, ToolStatus
from ais0c_harness.eval import UNSCRIPTED_RESULT, FixtureGateway

from .eval_helpers import gateway_profile_of, intent, scenario, triage_config

PLAYED = scenario("tl-01-catalog-note-fp")
OFFENSE: dict[str, JsonValue] = {"offense_id": PLAYED.input.offense.offense_id}


def ok(evidence_id: str, row: int) -> ToolResult:
    return ToolResult(
        status=ToolStatus.OK,
        evidence_id=evidence_id,
        data=[{"id": row}],
        truncated=False,
        coverage=ToolCoverage(complete=True, gaps=[]),
    )


def gateway(results: dict[str, list[ToolResult]] | None = None) -> FixtureGateway:
    return FixtureGateway(
        gateway_profile_of(triage_config()),
        PLAYED.input.tool_results if results is None else results,
        now=PLAYED.evaluated_at,
    )


def call(fixture: FixtureGateway, sent: ToolIntent) -> ToolResult:
    return asyncio.run(fixture.call(sent))


def test_results_answer_the_calls_in_order() -> None:
    fixture = gateway({"get_offense": [ok("ev_first", 1), ok("ev_second", 2)]})

    first = call(fixture, intent("get_offense", OFFENSE, played=PLAYED))
    second = call(fixture, intent("get_offense", OFFENSE, played=PLAYED))

    assert [first.evidence_id, second.evidence_id] == ["ev_first", "ev_second"]
    assert [first.data, second.data] == [[{"id": 1}], [{"id": 2}]]
    assert [exchange.outcome for exchange in fixture.exchanges] == ["scripted", "scripted"]


def test_the_last_result_repeats_under_a_derived_evidence_id() -> None:
    fixture = gateway({"get_offense": [ok("ev_first", 1), ok("ev_last", 2)]})

    answers = [call(fixture, intent("get_offense", OFFENSE, played=PLAYED)) for _ in range(4)]

    assert [answer.evidence_id for answer in answers] == [
        "ev_first",
        "ev_last",
        "ev_last-r1",
        "ev_last-r2",
    ]
    assert answers[3].data == [{"id": 2}]
    assert [exchange.outcome for exchange in fixture.exchanges] == [
        "scripted",
        "scripted",
        "repeated",
        "repeated",
    ]


def test_a_profile_tool_without_results_gets_a_fixed_error_and_the_run_goes_on() -> None:
    fixture = gateway()

    answer = call(fixture, intent("list_rules", {}, played=PLAYED))
    after = call(fixture, intent("get_offense", OFFENSE, played=PLAYED))

    assert answer == UNSCRIPTED_RESULT
    assert (answer.status, answer.data, answer.coverage.complete) == (ToolStatus.ERROR, [], False)
    assert after.status is ToolStatus.OK
    assert [exchange.outcome for exchange in fixture.exchanges] == ["unscripted", "scripted"]


@pytest.mark.parametrize(
    ("arguments", "problem"),
    [
        ({"offense_id": "9101"}, "offense_id does not match the schema (type)"),
        ({}, "missing offense_id"),
        ({"offense_id": 9101, "verbose": True}, "unknown argument"),
        ({"offense_id": -1}, "offense_id does not match the schema (minimum)"),
    ],
)
def test_arguments_that_break_the_schema_are_denied_as_the_gateway_does(
    arguments: dict[str, JsonValue], problem: str
) -> None:
    fixture = gateway()

    answer = call(fixture, intent("get_offense", arguments, played=PLAYED))

    assert answer.status is ToolStatus.DENIED
    assert answer.deny_reason is not None
    assert answer.deny_reason.startswith("invalid_arguments: ")
    assert problem in answer.deny_reason
    assert (answer.data, answer.coverage.complete) == ([], False)
    assert fixture.exchanges[0].outcome == "schema_invalid"
    # A denied call takes no scripted result: the next valid call gets the first.
    assert call(fixture, intent("get_offense", OFFENSE, played=PLAYED)).evidence_id == (
        "ev_tl01_offense"
    )


@pytest.mark.parametrize(
    ("field", "reason"),
    [("reason", "reason_missing"), ("expected_evidence", "expected_evidence_missing")],
)
def test_a_blank_reason_or_expected_evidence_is_denied(field: str, reason: str) -> None:
    fixture = gateway()

    answer = call(fixture, intent("get_offense", OFFENSE, played=PLAYED, **{field: "  "}))

    assert answer.status is ToolStatus.DENIED
    assert answer.deny_reason == f"invalid_intent: {reason}"
    assert fixture.exchanges[0].outcome == "schema_invalid"


def test_an_old_schema_version_is_denied() -> None:
    fixture = gateway()

    answer = call(fixture, intent("get_offense", OFFENSE, played=PLAYED, schema_version="old"))

    assert answer.deny_reason is not None
    assert answer.deny_reason.startswith("schema_version_mismatch")
    assert fixture.exchanges[0].outcome == "schema_invalid"


@pytest.mark.parametrize("tool_id", ["add_offense_note", "create_ariel_search", "no_such_tool"])
def test_a_tool_outside_the_profile_is_never_run(tool_id: str) -> None:
    results = dict(PLAYED.input.tool_results)
    results[tool_id] = results["get_offense"]
    fixture = gateway(results)

    answer = call(fixture, intent(tool_id, {"offense_id": 9101}, played=PLAYED))

    assert answer.status is ToolStatus.DENIED
    assert (
        answer.deny_reason == f"tool_not_in_profile: {tool_id} is not a tool of qradar-triage-read"
    )
    assert answer.data == []
    exchange = fixture.exchanges[0]
    assert (exchange.outcome, exchange.executed) == ("outside_profile", False)


def test_an_intent_for_another_profile_is_never_run() -> None:
    sent = intent("get_offense", OFFENSE, played=PLAYED).model_copy(
        update={"toolset_profile": "qradar-investigate-read"}
    )
    fixture = gateway()

    answer = call(fixture, sent)

    assert answer.status is ToolStatus.DENIED
    assert fixture.exchanges[0].outcome == "outside_profile"


def test_every_intent_and_its_answer_is_kept() -> None:
    fixture = gateway()
    sent = [
        intent("get_offense", OFFENSE, played=PLAYED),
        intent("get_offense", {}, played=PLAYED),
        intent("list_offenses", {}, played=PLAYED),
        intent("add_offense_note", {}, played=PLAYED),
    ]

    answers = [call(fixture, item) for item in sent]

    assert [exchange.intent for exchange in fixture.exchanges] == sent
    assert [exchange.result for exchange in fixture.exchanges] == answers
    assert [exchange.outcome for exchange in fixture.exchanges] == [
        "scripted",
        "schema_invalid",
        "unscripted",
        "outside_profile",
    ]
