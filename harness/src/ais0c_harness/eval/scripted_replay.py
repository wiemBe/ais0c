# ruff: noqa: S608 - AQL, not SQL: a fixed template filled from the recorded offense
"""Scripted models that play an Investigation or a Verification scenario against its recording,
without a real model (T-052 criterion 8: every scenario passes with k=2).

A scripted model runs one Ariel search the way the prompts teach, with a query that fits the
recording, then answers as the scenario expects:

1. `create_ariel_search` for the offense's user, bounded by `starttime BETWEEN` and START/STOP in
   epoch milliseconds;
2. `get_ariel_search_status` and `get_ariel_search_results` for the search it made (the ID is in
   the first result the model reads);
3. the answer, citing the results' evidence by its alias (`ev_3`: the third call of the run) and
   the handed-over evidence by `ev_c<n>`.

The model is stateless: it reads what it has done from the run's messages.
"""

import re
from collections.abc import Mapping
from typing import Final

from pydantic import JsonValue
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from ais0c_harness.eval.investigation import InvestigationScenario
from ais0c_harness.eval.scripted import SCRIPTED_MODEL_NAME, tool_calls_made
from ais0c_harness.eval.verification import VerificationScenario
from ais0c_harness.replay.recording import Recording

SEARCH_ID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
REASON: Final = "Look at the offense's events in the recorded window."
EXPECTED: Final = "The events of the offense's user."
STEPS: Final = ("create_ariel_search", "get_ariel_search_status", "get_ariel_search_results")
RESULTS_ALIAS: Final = "ev_3"


def search_query(recording: Recording, *, columns: str) -> str:
    """The query a scripted model runs: the offense's source within a second of the offense.

    A Source IP offense is searched by `sourceip`, any other type by `username`."""
    offense = recording.offense
    low = int(offense.start_time.timestamp() * 1000) - 1000
    high = int(offense.last_updated_time.timestamp() * 1000) + 1000
    source = offense.offense_source.replace("'", "''")
    field = "sourceip" if offense.offense_type == "Source IP" else "username"
    return (
        f"SELECT {columns} FROM events WHERE {field} = '{source}' "
        f"AND starttime BETWEEN {low} AND {high} ORDER BY starttime ASC LIMIT 50 "
        f"START {low} STOP {high}"
    )


def _search_id(messages: list[ModelMessage]) -> str:
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                match = SEARCH_ID.search(str(part.content))
                if match is not None:
                    return match.group()
    raise RuntimeError("no search ID in the run's tool results")


def _call(step: int, messages: list[ModelMessage], query: str) -> ToolCallPart:
    tool_id = STEPS[step]
    arguments: dict[str, JsonValue] = (
        {"query_expression": query} if step == 0 else {"search_id": _search_id(messages)}
    )
    return ToolCallPart(
        tool_id,
        {"reason": REASON, "expected_evidence": EXPECTED, "arguments": arguments},
    )


def investigation_answer(
    scenario: InvestigationScenario, recording: Recording
) -> dict[str, JsonValue]:
    """An InvestigationResult answer that meets the scenario's expectation."""
    expect = scenario.expect
    verdict = (
        "suspicious"
        if any(item.value == "suspicious" for item in expect.verdict_in)
        else sorted(item.value for item in expect.verdict_in)[0]
    )
    start = recording.offense.start_time.isoformat()
    gap_reason = next(
        iter(sorted(reason.value for reason in expect.data_gap_reason_in)),
        None,
    )
    events: list[JsonValue] = [
        {
            "rank": rank,
            "time": start,
            "log_source": "Recorded events",
            "event_name": "The expected event",
            "qid": None,
            "source": event.address,
            "destination": None,
            "username": event.username,
            "reason": "The event the scenario expects, in the search results.",
            "checklist": ["Check the source host."],
            "aql": None,
            "evidence_id": RESULTS_ALIAS,
        }
        for rank, event in enumerate(expect.find_events, start=1)
    ]
    if gap_reason is not None:
        events = []
    return {
        "verdict": verdict,
        "confidence": "medium",
        "ai_level": "high",
        "timeline": []
        if gap_reason is not None
        else [
            {
                "time": start,
                "description": "The replication requests.",
                "evidence_ids": [RESULTS_ALIAS],
            }
        ],
        "hypotheses": [
            {
                "text": "Required replication telemetry is absent."
                if gap_reason is not None
                else "The offense's activity is malicious.",
                "status": "open" if gap_reason is not None else "supported",
            }
        ],
        "urgent_event_candidates": events,
        "claims": []
        if gap_reason is not None
        else [
            {
                "text": "The account's events are in the recorded window.",
                "evidence_ids": [RESULTS_ALIAS],
            }
        ],
        "data_gaps": []
        if gap_reason is None
        else [
            {
                "source": "qradar",
                "period_start": recording.manifest.window.start.isoformat(),
                "period_end": recording.manifest.window.end.isoformat(),
                "reason": gap_reason,
            }
        ],
        "injection_suspected": bool(expect.injection_suspected),
    }


def verification_answer(scenario: VerificationScenario) -> dict[str, JsonValue]:
    """A VerificationResult answer that meets the scenario's expectation."""
    expect = scenario.expect
    claims = scenario.input.claims
    disagreements: list[JsonValue] = [
        {"claim_text": claims[position].text, "reason": "The events do not show it."}
        for position in sorted(set(expect.disputed_claims))
    ]
    reviewed = scenario.input.reviewed.verdict
    verdict = (
        reviewed
        if not expect.verdict_in or reviewed in expect.verdict_in
        else sorted(expect.verdict_in, key=lambda item: item.value)[0]
    )
    return {
        "agrees": expect.agrees,
        "verdict": verdict.value,
        "confidence": scenario.input.reviewed.confidence.value,
        "disagreements": disagreements,
        "checked_evidence_ids": [f"ev_c{n}" for n in range(1, len(scenario.input.evidence) + 1)],
        "claims": [],
        "data_gaps": [],
        "injection_suspected": False,
    }


def scripted_replay_model(
    scenario: InvestigationScenario | VerificationScenario,
    recording: Recording,
    *,
    answer: Mapping[str, JsonValue] | None = None,
) -> FunctionModel:
    """A model that makes one search and then answers; `answer` overrides fields of the answer."""
    if isinstance(scenario, InvestigationScenario):
        query = search_query(
            recording,
            columns="starttime, username, sourceip, destinationip, QIDNAME(qid) AS event_name",
        )
        final = {**investigation_answer(scenario, recording), **(answer or {})}
    else:
        query = search_query(recording, columns="starttime, username, sourceip, qid")
        final = {**verification_answer(scenario), **(answer or {})}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        made = tool_calls_made(messages)
        if made < len(STEPS) and info.function_tools:
            return ModelResponse(parts=[_call(made, messages, query)])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, final)])

    return FunctionModel(respond, model_name=SCRIPTED_MODEL_NAME)
