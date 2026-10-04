"""The Action Executor's profile qradar-note-write (T-018 criteria 3, 4 and 5).

3. The profile is in config/connectors/qradar.yaml and config/policies/qradar.yaml with the
   fork's two note tools, and its calls go to the note instance, qradar-mcp-note.
4. Only the executor's token reaches the note tools. With an agent profile's token a note call
   is denied whatever the intent claims, and the note token serves only runs of the pseudo agent
   action-executor.
5. On top of the schema, the gateway checks the note text: at most 2000 characters, and no
   control or invisible character except the line feed.

A denied call is recorded like any other, and the MCP server never sees it. Special characters
are written as chr() calls, so this file holds none of them.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest
from gateway_support import (
    AGENT_RUN,
    FakeQRadar,
    Harness,
    auth,
    build_harness,
    default_response,
    load_config,
    tool_result,
)
from pydantic import JsonValue, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_agents import ToolsetProfile
from ais0c_contracts import ToolResult, ToolStatus
from ais0c_mcp_gateway.registry import Registry
from ais0c_mcp_gateway.text_rules import TextRule, text_problem
from ais0c_mcp_gateway.upstream import UpstreamOutcome
from ais0c_storage import PolicyDecision

pytestmark = pytest.mark.anyio

NOTE_PROFILE = "qradar-note-write"
EXECUTOR = "action-executor"
NOTE_RUN = "run-case-1001-note-1"
OFFENSE_ID = 1001
NOTE_TOOLS = {"add_offense_note": "write", "get_offense_notes": "read"}
AGENT_PROFILES = [
    "qradar-triage-read",
    "qradar-investigate-read",
    "qradar-verify-read",
    "qradar-hunt-read",
    "qradar-inventory-read",
    "qradar-tuning-read",
]
MAX_NOTE_LENGTH = 2000
# The first lines of a note as the executor's template writes them (architecture §9).
NOTE = "\n".join(
    [
        f"[AI-SOC] Değerlendirme #1 {chr(0xB7)} 2026-10-04 00:00 {chr(0xB7)} run:7f3a9c",
        "Karar: Şüpheli, Güven: orta, Bildirim seviyesi: high",
        "Özet: svc_backup_7731 hesabı DC-LAB-01 üzerinde dizin çoğaltma izni kullandı.",
    ]
)


@dataclass
class RecordingUpstream:
    """An MCP instance that answers like the fork and records what it was asked."""

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def call_tool(self, name: str, arguments: Mapping[str, JsonValue]) -> UpstreamOutcome:
        self.calls.append((name, dict(arguments)))
        return UpstreamOutcome.ok(default_response(name, dict(arguments)))


async def start_note_run(harness: Harness, *, agent_id: str = EXECUTOR) -> str:
    return await harness.start_run(NOTE_RUN, profile=NOTE_PROFILE, agent_id=agent_id)


def note_intent(
    harness: Harness,
    tool_id: str = "add_offense_note",
    arguments: dict[str, Any] | None = None,
    **changes: object,
) -> dict[str, Any]:
    """An executor's intent of the note profile in NOTE_RUN."""
    if arguments is None:
        arguments = {"offense_id": OFFENSE_ID, "note_text": NOTE}
    fields: dict[str, object] = {"agent_id": EXECUTOR, "run_id": NOTE_RUN} | changes
    return harness.intent(NOTE_PROFILE, tool_id, arguments, **fields)


async def post(harness: Harness, intent: dict[str, Any], *, token: str | None = None) -> ToolResult:
    async with harness.client() as client:
        return tool_result(await harness.post(client, intent, token=token))


async def assert_denied(harness: Harness, run: str, result: ToolResult, reason: str) -> None:
    """Denied with `reason`, recorded as a denial, and never sent to an MCP server."""
    assert result.status is ToolStatus.DENIED
    assert result.deny_reason == reason
    assert (result.evidence_id, result.data) == (None, [])
    assert harness.fake.calls == []
    [row] = await harness.tool_calls(run)
    assert (row.policy_decision, row.status, row.deny_reason) == (
        PolicyDecision.DENY,
        ToolStatus.DENIED,
        reason,
    )


# --- criterion 3: the profile and its tools ------------------------------------------------


def test_the_note_profile_has_the_forks_note_tools(registry: Registry) -> None:
    manifest, policy = load_config()
    profile = registry.profiles[NOTE_PROFILE]
    fork_tools = {
        tool["id"]: tool["risk"] for tool in manifest["server_profiles"]["qradar-note"]["tools"]
    }

    assert NOTE_PROFILE in manifest["profiles"]
    assert NOTE_PROFILE in policy["profiles"]
    assert {tool.id: tool.risk for tool in profile.tools.values()} == fork_tools == NOTE_TOOLS
    assert (profile.instance, profile.caller) == ("qradar-mcp-note", EXECUTOR)
    assert profile.text_rules == {"note_text": TextRule(max_length=MAX_NOTE_LENGTH, multiline=True)}


def test_no_agent_profile_has_a_note_tool_or_the_note_instance(registry: Registry) -> None:
    for name in AGENT_PROFILES:
        profile = registry.profiles[name]
        assert profile.caller is None
        assert profile.instance == "qradar-mcp-read"
        assert not NOTE_TOOLS.keys() & profile.tools.keys()


async def test_the_executor_writes_and_reads_notes_on_the_note_instance(
    registry: Registry,
    sessions: async_sessionmaker[AsyncSession],
    fake: FakeQRadar,
    fake_server: tuple[FakeQRadar, str],
) -> None:
    read, note = RecordingUpstream(), RecordingUpstream()
    harness = build_harness(
        registry=registry,
        sessions=sessions,
        fake=fake,
        upstream_url=fake_server[1],
        upstreams={"qradar-mcp-read": read, "qradar-mcp-note": note},
    )
    run = await start_note_run(harness)
    notes_query = {"offense_id": OFFENSE_ID, "limit": 10}

    written = await post(harness, note_intent(harness))
    listed = await post(harness, note_intent(harness, "get_offense_notes", notes_query))

    assert written.status is ToolStatus.OK
    assert written.data[0]["note_text"] == NOTE
    assert listed.status is ToolStatus.OK
    assert [row["id"] for row in listed.data] == [9001]
    assert note.calls == [
        ("add_offense_note", {"offense_id": OFFENSE_ID, "note_text": NOTE}),
        ("get_offense_notes", notes_query),
    ]
    assert read.calls == []
    rows = await harness.tool_calls(run)
    assert [(row.policy_decision, row.status) for row in rows] == [
        (PolicyDecision.ALLOW, ToolStatus.OK),
        (PolicyDecision.ALLOW, ToolStatus.OK),
    ]


async def test_the_note_token_lists_the_tools_as_write_and_read(harness: Harness) -> None:
    async with harness.client() as client:
        response = await client.get("/v1/tools", headers=auth(harness.tokens[NOTE_PROFILE]))

    listed = response.json()
    assert listed["name"] == NOTE_PROFILE
    assert {tool["id"]: tool["risk"] for tool in listed["tools"]} == NOTE_TOOLS
    # An agent given this token by mistake cannot even load the profile (AGENTS.md hard rule 2).
    with pytest.raises(ValidationError):
        ToolsetProfile.model_validate(listed)


# --- criterion 4: only the executor's token ------------------------------------------------


@pytest.mark.parametrize("tool_id", list(NOTE_TOOLS))
@pytest.mark.parametrize("profile", AGENT_PROFILES)
async def test_an_agent_token_cannot_call_a_note_tool(
    harness: Harness, profile: str, tool_id: str
) -> None:
    run = await harness.start_run(AGENT_RUN, profile=profile)
    arguments = {"offense_id": OFFENSE_ID, "note_text": NOTE}
    if tool_id == "get_offense_notes":
        arguments = {"offense_id": OFFENSE_ID}

    result = await post(harness, harness.intent(profile, tool_id, arguments))

    await assert_denied(
        harness, run, result, f"tool_not_in_profile: {tool_id} is not a tool of {profile}"
    )


@pytest.mark.parametrize("profile", AGENT_PROFILES)
async def test_an_agent_token_cannot_borrow_the_note_profile(
    harness: Harness, profile: str
) -> None:
    # The intent and the run are the executor's; only the token is an agent's.
    run = await start_note_run(harness)

    result = await post(harness, note_intent(harness), token=harness.tokens[profile])

    await assert_denied(
        harness,
        run,
        result,
        f"profile_mismatch: the token is for {profile}, the intent names {NOTE_PROFILE}",
    )


@pytest.mark.parametrize("agent_id", ["triage", "investigation", "reporting", "offense-source"])
async def test_the_note_token_serves_only_the_executors_runs(
    harness: Harness, agent_id: str
) -> None:
    run = await start_note_run(harness, agent_id=agent_id)

    result = await post(harness, note_intent(harness, agent_id=agent_id))

    await assert_denied(
        harness, run, result, f"caller_not_allowed: {NOTE_PROFILE} serves only {EXECUTOR}"
    )


async def test_the_note_token_has_none_of_the_agents_tools(harness: Harness) -> None:
    run = await start_note_run(harness)

    result = await post(harness, note_intent(harness, "get_offense", {"offense_id": OFFENSE_ID}))

    await assert_denied(
        harness, run, result, f"tool_not_in_profile: get_offense is not a tool of {NOTE_PROFILE}"
    )


# --- criterion 5: the note text -------------------------------------------------------------


def padded_note(length: int) -> str:
    """NOTE and one more line, `length` characters in all."""
    filler = "Acil bakılması gereken event yok. "
    text = NOTE + "\n" + filler * (length // len(filler) + 1)
    return text[:length]


@pytest.mark.parametrize("length", [1, len(NOTE), MAX_NOTE_LENGTH])
async def test_a_note_within_the_limit_is_written(harness: Harness, length: int) -> None:
    await start_note_run(harness)
    text = padded_note(length)

    result = await post(
        harness, note_intent(harness, arguments={"offense_id": OFFENSE_ID, "note_text": text})
    )

    assert result.status is ToolStatus.OK
    assert harness.fake.tool_calls("add_offense_note") == [
        {"offense_id": OFFENSE_ID, "note_text": text}
    ]


async def test_a_longer_note_is_denied(harness: Harness) -> None:
    run = await start_note_run(harness)
    text = padded_note(MAX_NOTE_LENGTH + 1)

    result = await post(
        harness, note_intent(harness, arguments={"offense_id": OFFENSE_ID, "note_text": text})
    )

    await assert_denied(
        harness, run, result, "invalid_text: note_text is longer than 2000 characters"
    )


HIDDEN_CHARACTERS = {
    "carriage-return": 0x0D,
    "tab": 0x09,
    "bell": 0x07,
    "escape": 0x1B,
    "delete": 0x7F,
    "next-line": 0x85,
    "line-separator": 0x2028,
    "paragraph-separator": 0x2029,
    "right-to-left-override": 0x202E,
    "left-to-right-isolate": 0x2066,
    "zero-width-space": 0x200B,
    "byte-order-mark": 0xFEFF,
    "tag-letter": 0xE0041,
}


@pytest.mark.parametrize("code_point", HIDDEN_CHARACTERS.values(), ids=HIDDEN_CHARACTERS.keys())
async def test_a_control_or_invisible_character_is_denied(
    harness: Harness, code_point: int
) -> None:
    run = await start_note_run(harness)
    text = NOTE.replace("Karar:", f"Karar:{chr(code_point)}", 1)

    result = await post(
        harness, note_intent(harness, arguments={"offense_id": OFFENSE_ID, "note_text": text})
    )

    await assert_denied(
        harness,
        run,
        result,
        f"invalid_text: note_text holds a control or invisible character (U+{code_point:04X})",
    )
    # The reason names the character, never the text.
    assert "svc_backup_7731" not in (result.deny_reason or "")


NUL_REASON = "invalid_intent: the intent holds a NUL character (U+0000)"


async def test_a_nul_character_in_the_note_is_denied_and_recorded(harness: Harness) -> None:
    # PostgreSQL cannot store U+0000, so the record holds U+FFFD in its place.
    run = await start_note_run(harness)
    text = NOTE.replace("Karar:", f"Karar:{chr(0)}", 1)

    result = await post(
        harness, note_intent(harness, arguments={"offense_id": OFFENSE_ID, "note_text": text})
    )

    await assert_denied(harness, run, result, NUL_REASON)
    [row] = await harness.tool_calls(run)
    assert row.intent.arguments["note_text"] == text.replace(chr(0), chr(0xFFFD))


@pytest.mark.parametrize("where", ["argument", "argument-name", "reason"])
async def test_a_nul_character_anywhere_in_an_intent_is_denied(
    harness: Harness, where: str
) -> None:
    # Agents' intents too: a model could write U+0000 into a filter.
    run = await harness.start_run(AGENT_RUN, profile="qradar-triage-read", agent_id="triage")
    arguments: dict[str, Any] = {"filter": "status = 'OPEN'"}
    changes: dict[str, object] = {"agent_id": "triage"}
    if where == "argument":
        arguments["filter"] = f"status = 'OPEN'{chr(0)}"
    elif where == "argument-name":
        arguments = {f"filter{chr(0)}": "status = 'OPEN'"}
    else:
        changes["reason"] = f"List the open offenses.{chr(0)}"

    result = await post(
        harness, harness.intent("qradar-triage-read", "list_offenses", arguments, **changes)
    )

    await assert_denied(harness, run, result, NUL_REASON)


async def test_a_fake_header_line_cannot_hide_behind_a_carriage_return(harness: Harness) -> None:
    # "\r" would let a field start what looks like a new note line in some viewers.
    run = await start_note_run(harness)
    text = NOTE + chr(0x0D) + "[AI-SOC] Değerlendirme #9 run:000000"

    result = await post(
        harness, note_intent(harness, arguments={"offense_id": OFFENSE_ID, "note_text": text})
    )

    await assert_denied(
        harness,
        run,
        result,
        "invalid_text: note_text holds a control or invisible character (U+000D)",
    )


def test_text_rules_allow_line_feeds_only_when_multiline() -> None:
    one_line = TextRule(max_length=100)
    lines = TextRule(max_length=100, multiline=True)

    assert text_problem("note_text", "a\nb", lines) is None
    assert text_problem("note_text", "a\nb", one_line) == (
        "note_text holds a control or invisible character (U+000A)"
    )
    assert text_problem("note_text", "a\r\nb", lines) == (
        "note_text holds a control or invisible character (U+000D)"
    )
    # A lone surrogate cannot come through JSON intact, but a caller in the process could pass one.
    assert text_problem("note_text", "a" + chr(0xD800), lines) == (
        "note_text holds a control or invisible character (U+D800)"
    )
    assert text_problem("note_text", "Şüpheli → 203.0.113.7", one_line) is None
