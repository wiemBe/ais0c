"""T-043 criterion 4: the AQL an agent suggests passes the AQL Guard (decision T-39).

The rules are those of the gateway profile `qradar-investigate-read` in
config/policies/qradar.yaml. A rejected query goes back to the model with the Guard's reasons
and "fix the query or leave aql empty", never with the query itself.
"""

# ruff: noqa: S608 - AQL test queries, not SQL built from input

from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel
from pydantic_ai import ModelRetry

from ais0c_agents import (
    SUGGESTED_AQL_PROFILE,
    AqlRules,
    AqlRulesError,
    SuggestedAqlCheck,
    load_aql_rules,
)
from ais0c_contracts import RunStatus, UrgentEvent
from ais0c_policy import AqlRejectReason

from .helpers import (
    CONTEXT_EVIDENCE,
    REPO_ROOT,
    ScriptedModel,
    answer,
    context_evidence,
    retry_prompts,
    run_summary,
    summary_output,
)

POLICY = REPO_ROOT / "config/policies/qradar.yaml"
MARKER = "svc_marker_5521"
VALID = (
    f"SELECT username, sourceip FROM events WHERE username = '{MARKER}' "
    "LIMIT 100 START '2026-10-02 13:00' STOP '2026-10-02 14:00'"
)
REJECTED = {
    "no LIMIT": (
        f"SELECT username FROM events WHERE username = '{MARKER}' LAST 2 HOURS",
        [AqlRejectReason.MISSING_LIMIT],
    ),
    "window over 7 days": (
        f"SELECT username FROM events WHERE username = '{MARKER}' LIMIT 100 LAST 8 DAYS",
        [AqlRejectReason.WINDOW_EXCEEDS_PROFILE],
    ),
    "table other than events": (
        f"SELECT sourceip FROM flows WHERE sourceip = '{MARKER}' LIMIT 100 LAST 2 HOURS",
        [AqlRejectReason.TABLE_NOT_ALLOWED],
    ),
    "two statements": (
        f"SELECT username FROM events WHERE username = '{MARKER}' LIMIT 10 LAST 2 HOURS; "
        "SELECT username FROM events LIMIT 10 LAST 2 HOURS",
        [AqlRejectReason.MULTIPLE_STATEMENTS],
    ),
    "LIMIT after LAST": (
        f"SELECT username FROM events WHERE username = '{MARKER}' LAST 2 HOURS LIMIT 100",
        [AqlRejectReason.LIMIT_AFTER_TIME_BOUND],
    ),
}


def rules() -> AqlRules:
    return load_aql_rules(POLICY)


def urgent(aql: str | None, *, rank: int = 1) -> dict[str, object]:
    return {
        "rank": rank,
        "time": "2026-10-02T13:05:00Z",
        "log_source": "DC-01",
        "event_name": "4662",
        "reason": "Replication by a user account.",
        "checklist": [],
        "aql": aql,
        "evidence_id": "ev_c1",
    }


# --- the rules ------------------------------------------------------------------------------------


def test_the_rules_are_the_investigate_profiles() -> None:
    loaded = rules()

    assert loaded.profile == SUGGESTED_AQL_PROFILE == "qradar-investigate-read"
    assert loaded.aql.max_window == timedelta(days=7)
    assert loaded.aql.max_limit == 1000
    assert loaded.aql.allowed_tables == {"events"}
    assert loaded.aql.wide_window_threshold == timedelta(hours=24)
    assert "username" in loaded.indexed_fields
    assert "sourceip" in loaded.indexed_fields


def test_a_missing_policy_file_stops_the_loader(tmp_path: Path) -> None:
    with pytest.raises(AqlRulesError, match="cannot read the gateway policy"):
        load_aql_rules(tmp_path / "qradar.yaml")


def test_a_missing_profile_stops_the_loader() -> None:
    with pytest.raises(AqlRulesError, match="has no profile 'qradar-investigate-write'"):
        load_aql_rules(POLICY, profile="qradar-investigate-write")


def test_a_profile_without_aql_rules_stops_the_loader() -> None:
    with pytest.raises(AqlRulesError, match="profile 'qradar-triage-read' has no aql rules"):
        load_aql_rules(POLICY, profile="qradar-triage-read")


@pytest.mark.parametrize(
    "text",
    [
        "indexed_fields: [username]\n",
        "profiles: {qradar-investigate-read: {aql: {max_window: P7D}}}\nindexed_fields: []\n",
        "indexed_fields: []\nindexed_fields: [username]\nprofiles: {}\n",
    ],
    ids=["no profiles", "incomplete aql rules", "duplicate key"],
)
def test_an_invalid_policy_stops_the_loader(tmp_path: Path, text: str) -> None:
    path = tmp_path / "qradar.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(AqlRulesError, match=str(path)):
        load_aql_rules(path)


# --- the check --------------------------------------------------------------------------------


class Candidates(BaseModel):
    urgent_event_candidates: list[UrgentEvent]


def candidates(*events: dict[str, object]) -> Candidates:
    return Candidates.model_validate({"urgent_event_candidates": list(events)})


def test_a_valid_query_is_accepted() -> None:
    output = candidates(urgent(VALID), urgent(None, rank=2))

    assert SuggestedAqlCheck(rules())(output) is output


@pytest.mark.parametrize(("query", "reasons"), REJECTED.values(), ids=REJECTED.keys())
def test_a_query_the_guard_rejects_goes_back_with_its_reasons(
    query: str, reasons: list[AqlRejectReason]
) -> None:
    with pytest.raises(ModelRetry) as rejected:
        SuggestedAqlCheck(rules())(candidates(urgent(VALID), urgent(query, rank=2)))

    message = rejected.value.message
    assert message.startswith("The AQL Guard rejected the aql of urgent event 2: ")
    assert message.endswith("Fix the query or leave aql empty.")
    for reason in reasons:
        assert reason.value in message
    assert MARKER not in message
    assert "FROM events WHERE" not in message


def test_the_reasons_say_what_the_profile_allows() -> None:
    with pytest.raises(ModelRetry) as rejected:
        SuggestedAqlCheck(rules())(candidates(urgent(REJECTED["window over 7 days"][0])))

    assert "window_exceeds_profile (the window is at most 7 days)" in rejected.value.message


@pytest.mark.parametrize("reason", list(AqlRejectReason))
def test_every_reject_reason_is_explained(reason: AqlRejectReason) -> None:
    assert rules().explain(reason).startswith(f"{reason.value} (")


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_a_blank_query_counts_as_none(blank: str) -> None:
    checked = SuggestedAqlCheck(rules())(candidates(urgent(blank)))

    assert checked.urgent_event_candidates[0].aql is None


def test_the_model_fixes_a_rejected_query_in_a_run() -> None:
    reversed_order = REJECTED["LIMIT after LAST"][0]
    script = ScriptedModel(
        answer(summary_output("ev_c1", urgent_events=[urgent(reversed_order)])),
        answer(summary_output("ev_c1", urgent_events=[urgent(VALID)])),
    )

    run = run_summary(script.model, evidence=context_evidence(), aql=rules())

    [retry] = retry_prompts(run.messages)
    assert "limit_after_time_bound" in retry.model_response()
    assert "Fix the query or leave aql empty." in retry.model_response()
    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    [event] = run.result.urgent_events
    assert (event.aql, event.evidence_id) == (VALID, CONTEXT_EVIDENCE[0])


def test_an_agent_without_the_check_takes_any_query() -> None:
    # Reporting copies checked queries and builds without the rules (T-025).
    script = ScriptedModel(answer(summary_output(urgent_events=[urgent("not aql")])))

    run = run_summary(script.model, evidence=context_evidence())

    assert run.status is RunStatus.COMPLETED
