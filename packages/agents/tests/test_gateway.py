"""Acceptance criterion 5: the gateway client interface and the fake gateway client."""

import asyncio
import inspect

import pytest
from pydantic import ValidationError

from ais0c_agents import (
    FakeGatewayClient,
    GatewayClient,
    GatewayError,
    GatewayUnavailableError,
)
from ais0c_contracts import CostClass, RunStatus, TimeWindow, ToolIntent, ToolResult

from .helpers import (
    END,
    OFFENSE_EVIDENCE,
    RULE_EVIDENCE,
    START,
    ScriptedModel,
    answer,
    build,
    call,
    denied,
    gateway,
    ok,
    raw_call,
    retry_prompts,
    run_triage,
    triage_output,
)


def intent(tool_id: str = "get_offense", **changes: object) -> ToolIntent:
    fields: dict[str, object] = {
        "case_id": "case-4711",
        "agent_id": "triage",
        "toolset_profile": "qradar-triage-read",
        "tool_id": tool_id,
        "tool_schema_version": "1",
        "arguments": {"offense_id": 4711},
        "reason": "Read the offense.",
        "expected_evidence": "The offense record.",
        "time_window": TimeWindow(start=START, end=END),
        "cost_class": CostClass.LOW,
    }
    return ToolIntent.model_validate(fields | changes)


def send(fake: FakeGatewayClient, sent: ToolIntent) -> ToolResult:
    return asyncio.run(fake.call(sent))


# --- the interface ------------------------------------------------------------------------------


def test_interface_has_one_method_call() -> None:
    public = [name for name in vars(GatewayClient) if not name.startswith("_")]
    signature = inspect.signature(GatewayClient.call)

    assert public == ["call"]
    assert GatewayClient.__abstractmethods__ == frozenset({"call"})
    assert inspect.iscoroutinefunction(GatewayClient.call)
    assert list(signature.parameters) == ["self", "intent"]
    assert signature.parameters["intent"].annotation is ToolIntent
    assert signature.return_annotation is ToolResult


def test_unreachable_gateway_is_a_gateway_error() -> None:
    assert issubclass(GatewayUnavailableError, GatewayError)


# --- the fake -----------------------------------------------------------------------------------


def test_fake_returns_canned_results_and_records_intents() -> None:
    result = ok(OFFENSE_EVIDENCE, {"id": 4711})
    fake = FakeGatewayClient({"get_offense": result})

    assert send(fake, intent()) == result
    assert send(fake, intent()) == result
    assert fake.intents == [intent(), intent()]


def test_fake_plays_a_sequence_in_order_then_stops() -> None:
    first, second = ok(OFFENSE_EVIDENCE), denied("quota")
    fake = FakeGatewayClient({"get_offense": [first, second]})

    assert [send(fake, intent()), send(fake, intent())] == [first, second]
    with pytest.raises(LookupError, match="no canned response left"):
        send(fake, intent())


def test_fake_without_a_response_for_the_tool_fails_loudly() -> None:
    with pytest.raises(LookupError, match="get_rule"):
        send(FakeGatewayClient({"get_offense": ok(OFFENSE_EVIDENCE)}), intent("get_rule"))


def test_fake_raises_a_canned_gateway_error_after_recording_the_intent() -> None:
    fake = FakeGatewayClient({"get_offense": GatewayUnavailableError("connection refused")})

    with pytest.raises(GatewayUnavailableError):
        send(fake, intent())
    assert fake.intents == [intent()]


@pytest.mark.parametrize("field", ["reason", "expected_evidence"])
@pytest.mark.parametrize("value", ["", "  \n"])
def test_fake_rejects_an_intent_without_reason_or_expected_evidence(field: str, value: str) -> None:
    fake = FakeGatewayClient({"get_offense": ok(OFFENSE_EVIDENCE)})

    with pytest.raises(ValueError, match=field):
        send(fake, intent(**{field: value}))
    assert fake.intents == []


def test_fake_rejects_an_intent_that_breaks_the_contract() -> None:
    broken = intent().model_copy(update={"time_window": None, "case_id": None})
    fake = FakeGatewayClient({"get_offense": ok(OFFENSE_EVIDENCE)})

    with pytest.raises(ValidationError):
        send(fake, broken)
    assert fake.intents == []


# --- intents the agent sends --------------------------------------------------------------------


def test_every_intent_the_agent_sends_conforms_to_the_contract() -> None:
    fake = gateway()
    script = ScriptedModel(
        call("get_offense", offense_id=4711),
        call(
            "get_rule",
            reason="Check what the rule matches.",
            expected_evidence="The rule's tests and thresholds.",
            rule_id=100234,
        ),
        answer(triage_output(OFFENSE_EVIDENCE, RULE_EVIDENCE)),
    )

    run = run_triage(build(script, fake))

    assert run.status is RunStatus.COMPLETED
    assert [sent.tool_id for sent in fake.intents] == ["get_offense", "get_rule"]
    for sent in fake.intents:
        assert ToolIntent.model_validate(sent.model_dump()) == sent
        assert sent.reason.strip()
        assert sent.expected_evidence.strip()
        assert sent.time_window == TimeWindow(start=START, end=END)
        assert (sent.case_id, sent.hunt_id) == ("case-4711", None)
        assert (sent.agent_id, sent.toolset_profile) == ("triage", "qradar-triage-read")
        assert (sent.tool_schema_version, sent.cost_class) == ("1", CostClass.LOW)
    assert fake.intents[1].arguments == {"rule_id": 100234}
    assert fake.intents[1].reason == "Check what the rule matches."


@pytest.mark.parametrize(
    "args",
    [
        {"expected_evidence": "The offense record.", "arguments": {"offense_id": 4711}},
        {"reason": "  ", "expected_evidence": "The offense.", "arguments": {}},
        {"reason": "Read it.", "expected_evidence": "", "arguments": {}},
        {"reason": "Read it.", "expected_evidence": "The offense.", "arguments": {}, "x": 1},
        {"reason": "Read it.", "expected_evidence": "The offense."},
        {"reason": "r" * 301, "expected_evidence": "The offense.", "arguments": {}},
    ],
)
def test_call_without_a_valid_reason_and_expected_evidence_never_reaches_the_gateway(
    args: dict[str, object],
) -> None:
    fake = gateway()
    script = ScriptedModel(raw_call("get_offense", args), answer(triage_output()))

    run = run_triage(build(script, fake))

    assert run.status is RunStatus.COMPLETED
    assert fake.intents == []
    [retry] = retry_prompts(run.messages)
    assert retry.tool_name == "get_offense"
    assert "Invalid call to get_offense" in retry.model_response()


def test_unreachable_gateway_ends_the_run_as_failed() -> None:
    fake = FakeGatewayClient({"get_offense": GatewayUnavailableError("connection refused")})
    script = ScriptedModel(call("get_offense", offense_id=4711), answer(triage_output()))

    run = run_triage(build(script, fake))

    assert run.status is RunStatus.FAILED
    assert run.result is None
    assert run.error == "GatewayUnavailableError: connection refused"
    assert len(fake.intents) == 1
