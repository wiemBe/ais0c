"""Criterion 10: tokens and secrets never appear in the gateway's logs.

The gateway's logging is set up as in production (configure_logging), at DEBUG so the MCP SDK,
httpx2 and uvicorn log everything they can. Then calls that succeed and calls that fail run:
a wrong token, an MCP server that refuses the gateway's token, an unreachable MCP server and
an exception whose message holds a token.
"""

import io
import logging
import secrets
from collections.abc import Iterator

import pytest
from gateway_support import AGENT_RUN, Harness, build_harness, free_port, tool_result

from ais0c_contracts import ToolStatus
from ais0c_mcp_gateway.logs import REDACTED, RedactingFormatter, Redactor, configure_logging

pytestmark = pytest.mark.anyio

TRIAGE = "qradar-triage-read"


@pytest.fixture
def gateway_logs(harness: Harness) -> Iterator[io.StringIO]:
    stream = io.StringIO()
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    configure_logging(harness.gateway.redactor, level=logging.DEBUG, stream=stream)
    try:
        yield stream
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)


def secrets_of(harness: Harness) -> list[str]:
    return [*harness.tokens.values(), harness.fake.token]


async def test_no_secret_reaches_the_logs(harness: Harness, gateway_logs: io.StringIO) -> None:
    await harness.start_run(AGENT_RUN, profile=TRIAGE, agent_id="triage")
    intent = harness.intent(TRIAGE, "get_offense", {"offense_id": 1001}, agent_id="triage")
    triage_token = harness.tokens[TRIAGE]
    async with harness.client() as client:
        ok = tool_result(await harness.post(client, intent, run_id=AGENT_RUN))
        # A token with one character more, and another profile's token for this intent.
        wrong = await harness.post(client, intent, run_id=AGENT_RUN, token=triage_token + "x")
        other = await harness.post(
            client, intent, run_id=AGENT_RUN, token=harness.tokens["qradar-hunt-read"]
        )
        # The MCP server refuses the gateway's token.
        real_token = harness.fake.token
        harness.fake.token = "x" * 40
        try:
            refused = tool_result(await harness.post(client, intent, run_id=AGENT_RUN))
        finally:
            harness.fake.token = real_token

    unreachable = build_harness(
        registry=harness.registry,
        sessions=harness.sessions,
        fake=harness.fake,
        upstream_url=f"http://127.0.0.1:{free_port()}",
    )
    unreachable.tokens = harness.tokens
    async with unreachable.client() as client:
        failed = tool_result(await unreachable.post(client, intent, run_id=AGENT_RUN))
    try:
        raise RuntimeError(f"upstream said: bad token {harness.fake.token}")
    except RuntimeError:
        logging.getLogger("ais0c.gateway").exception("call failed for %s", triage_token)

    assert ok.status is ToolStatus.OK
    assert wrong.status_code == 401
    assert tool_result(other).status is ToolStatus.DENIED
    assert refused.status is failed.status is ToolStatus.ERROR
    logs = gateway_logs.getvalue()
    assert "tool call profile=qradar-triage-read" in logs
    assert f"call failed for {REDACTED}" in logs
    assert f"bad token {REDACTED}" in logs
    for secret in secrets_of(harness):
        assert secret not in logs


async def test_results_never_carry_a_known_secret(harness: Harness) -> None:
    # An MCP error message that repeats the gateway's token reaches the agent redacted.
    await harness.start_run(AGENT_RUN, profile=TRIAGE, agent_id="triage")
    harness.fake.responses["get_offense"] = lambda _: RuntimeError(
        f"QRadar refused token {harness.fake.token}"
    )
    intent = harness.intent(TRIAGE, "get_offense", {"offense_id": 1001}, agent_id="triage")

    async with harness.client() as client:
        response = await harness.post(client, intent, run_id=AGENT_RUN)

    result = tool_result(response)
    assert result.deny_reason == f"upstream_error: QRadar refused token {REDACTED}"
    assert harness.fake.token not in response.text
    [row] = await harness.tool_calls(AGENT_RUN)
    assert row.deny_reason is not None
    assert harness.fake.token not in row.deny_reason


def test_the_formatter_redacts_messages_arguments_and_tracebacks() -> None:
    secret = secrets.token_hex(12)
    formatter = RedactingFormatter(Redactor([secret, "short"]))
    try:
        raise ValueError(f"failed with {secret}")
    except ValueError as error:
        record = logging.LogRecord(
            "ais0c.gateway", logging.ERROR, __file__, 1, f"token {secret} and %s", (secret,), None
        )
        record.exc_info = (type(error), error, error.__traceback__)

    text = formatter.format(record)

    assert secret not in text
    assert text.count(REDACTED) == 3
    # Values too short to be secrets are left alone, so ordinary words survive.
    assert Redactor(["short"]).redact("a short text") == "a short text"


async def test_reprs_hide_tokens(harness: Harness) -> None:
    for upstream in harness.gateway.upstreams.values():
        assert harness.fake.token not in repr(upstream)
    assert all(token not in repr(harness.gateway) for token in harness.tokens.values())
