"""T-023 criteria 4 and 6: output evidence, AQL, ranks and injection signal."""

import pytest
from pydantic_ai import ModelRetry

from ais0c_agents import InvestigationOutput, check_urgent_event_ranks
from ais0c_contracts import RunStatus

from .helpers import CONTEXT_EVIDENCE, ScriptedModel, answer, retry_prompts
from .investigation_helpers import (
    VALID_AQL,
    build_investigation,
    investigation_output,
    run_investigation,
)


def test_claim_timeline_and_urgent_event_evidence_are_all_mapped() -> None:
    script = ScriptedModel(answer(investigation_output("ev_c1", ranks=(1,))))

    run = run_investigation(build_investigation(script))

    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.claims[0].evidence_ids == [CONTEXT_EVIDENCE[0]]
    assert run.result.timeline[0].evidence_ids == [CONTEXT_EVIDENCE[0]]
    assert run.result.urgent_event_candidates[0].evidence_id == CONTEXT_EVIDENCE[0]
    assert run.result.urgent_event_candidates[0].aql == VALID_AQL
    assert run.result.injection_suspected is True


def test_unknown_evidence_alias_is_retried() -> None:
    script = ScriptedModel(
        answer(investigation_output("ev_c31", ranks=(1,))),
        answer(investigation_output("ev_c1", ranks=(1,))),
    )

    run = run_investigation(build_investigation(script))

    assert run.status is RunStatus.COMPLETED
    [retry] = retry_prompts(run.messages)
    assert "ev_c31" in retry.model_response()
    assert "You can cite only ev_c1, ev_c2" in retry.model_response()


def test_rejected_suggested_aql_is_retried_without_echoing_the_query() -> None:
    marker = "bad_marker_7731"
    invalid = "SELECT username FROM events WHERE username = 'bad_marker_7731' LAST 1 HOURS LIMIT 10"
    script = ScriptedModel(
        answer(investigation_output("ev_c1", ranks=(1,), aql=invalid)),
        answer(investigation_output("ev_c1", ranks=(1,), aql=VALID_AQL)),
    )

    run = run_investigation(build_investigation(script))

    assert run.status is RunStatus.COMPLETED
    [retry] = retry_prompts(run.messages)
    assert "limit_after_time_bound" in retry.model_response()
    assert marker not in retry.model_response()


@pytest.mark.parametrize("ranks", [(2,), (1, 1), (1, 3)])
def test_nonconsecutive_or_duplicate_ranks_get_a_model_retry(ranks: tuple[int, ...]) -> None:
    script = ScriptedModel(
        answer(investigation_output("ev_c1", ranks=ranks)),
        answer(investigation_output("ev_c1", ranks=tuple(range(1, len(ranks) + 1)))),
    )

    run = run_investigation(build_investigation(script))

    assert run.status is RunStatus.COMPLETED
    [retry] = retry_prompts(run.messages)
    assert "unique consecutive ranks 1, 2, 3" in retry.model_response()


def test_rank_validator_raises_model_retry_directly() -> None:
    output = investigation_output("ev_c1", ranks=(1, 1))

    with pytest.raises(ModelRetry, match="unique consecutive"):
        check_urgent_event_ranks(None, InvestigationOutput.model_validate(output))  # type: ignore[arg-type]


def test_false_injection_signal_is_also_preserved() -> None:
    script = ScriptedModel(answer(investigation_output(injection_suspected=False)))

    run = run_investigation(build_investigation(script))

    assert run.result is not None
    assert run.result.injection_suspected is False
