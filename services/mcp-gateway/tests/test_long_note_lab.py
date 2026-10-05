"""T-040 criterion 5: a note of 2000 Turkish letters reaches the lab QRadar through the gateway.

`@pytest.mark.lab`: skipped unless QRADAR_LAB_URL and QRADAR_LAB_TOKEN are set (see
ais0c_harness.pytest_plugin). Before T-040 the fork sent note_text in the URL. There, 2000 Turkish
letters are 12,000 characters once percent-encoded, and the web server in front of QRadar refuses
a request line longer than 8190 bytes with 414 (T-019). The fork now sends a form body.

- `test_the_note_in_the_url_gets_414` sends the text the old way, in the query string, straight
  to the lab QRadar and to an offense that does not exist: the web server refuses the request
  line before QRadar reads it, and QRadar would answer 404 if it got that far, so nothing can be
  written.
- `test_the_note_is_written_through_the_gateway` writes the note through the dev stack's gateway
  as the Action Executor does, with its token and in a run of its pseudo agent, and reads it back
  through the gateway. QRadar cannot delete a note, so each run leaves one on the offense. It
  needs:

| Variable | Meaning |
|---|---|
| `QRADAR_LAB_OFFENSE_ID` | The lab offense the note is written to |
| `AIS0C_DATABASE_URL` | The dev stack's application database |
| `AIS0C_GATEWAY_URL` | The dev stack's gateway, started with the `qradar` compose profile and the lab override (deploy/compose/README.md) |
| `AIS0C_EXECUTOR_SECRETS_DIR` | The directory of `gateway-token-qradar-note-write` (`deploy/compose/secrets/executor`) |

    set -a; . ~/.config/ais0c/lab.env; set +a
    uv run pytest services/mcp-gateway/tests/test_long_note_lab.py -m lab -s
"""

import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx2
import pytest

from ais0c_agents.gateway_http import HttpGatewayClient
from ais0c_contracts import (
    AgentTask,
    Budget,
    RunStatus,
    TimeWindow,
    ToolIntent,
    ToolResult,
    ToolStatus,
)
from ais0c_mcp_gateway.text_rules import utf16_length
from ais0c_storage import create_engine, create_session_factory
from ais0c_storage.repositories import finish_agent_run, start_agent_run

pytestmark = [pytest.mark.lab, pytest.mark.anyio]

PROFILE = "qradar-note-write"
EXECUTOR = "action-executor"
API_VERSION = "29.0"
TURKISH_LETTERS = "çğıöşüÇĞİÖŞÜ"
NOTE = (TURKISH_LETTERS * 167)[:2000]
# Apache's default LimitRequestLine, which the lab QRadar's web server applies (T-019).
MAX_REQUEST_LINE = 8190
# No offense has this ID, so a note sent to it cannot be written.
NO_SUCH_OFFENSE = 2_147_483_647
STACK = (
    "QRADAR_LAB_OFFENSE_ID",
    "AIS0C_DATABASE_URL",
    "AIS0C_GATEWAY_URL",
    "AIS0C_EXECUTOR_SECRETS_DIR",
)


def test_the_note_in_the_url_gets_414() -> None:
    host = os.environ["QRADAR_LAB_URL"].strip().removeprefix("https://").rstrip("/")
    verify = os.environ.get("QRADAR_LAB_VERIFY_SSL", "true").strip().lower() != "false"
    with httpx2.Client(verify=verify, timeout=60) as client:
        response = client.post(
            f"https://{host}/api/siem/offenses/{NO_SUCH_OFFENSE}/notes",
            params={"note_text": NOTE},
            headers={
                "SEC": os.environ["QRADAR_LAB_TOKEN"].strip(),
                "Version": API_VERSION,
                "Accept": "application/json",
            },
        )
    request_line = f"POST {response.request.url.raw_path.decode('ascii')} HTTP/1.1"

    assert len(request_line) > MAX_REQUEST_LINE
    assert response.status_code == 414


@pytest.fixture
def stack() -> dict[str, str]:
    missing = [name for name in STACK if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip(f"set {', '.join(missing)} to write a note to the lab QRadar")
    return {name: os.environ[name].strip() for name in STACK}


async def test_the_note_is_written_through_the_gateway(stack: dict[str, str]) -> None:
    offense_id = int(stack["QRADAR_LAB_OFFENSE_ID"])
    token_file = Path(stack["AIS0C_EXECUTOR_SECRETS_DIR"]) / f"gateway-token-{PROFILE}"
    token = token_file.read_text(encoding="utf-8").strip()
    gateway = HttpGatewayClient(stack["AIS0C_GATEWAY_URL"], token, timeout_seconds=120)
    calls = ExecutorCalls(
        tools=await tool_list(stack["AIS0C_GATEWAY_URL"], token),
        run_id=f"t040-lab-{secrets.token_hex(4)}",
        case_id=f"case-{offense_id}",
        now=datetime.now(UTC),
    )
    engine = create_engine(stack["AIS0C_DATABASE_URL"])
    sessions = create_session_factory(engine)
    async with sessions.begin() as session:
        await start_agent_run(
            session,
            run_id=calls.run_id,
            task=calls.task(),
            prompt_version="none",
            model_alias="none",
            model_target="none",
            toolset_profile=PROFILE,
            started_at=calls.now,
        )
    status = RunStatus.FAILED
    try:
        written = await gateway.call(
            calls.intent("add_offense_note", {"offense_id": offense_id, "note_text": NOTE})
        )
        assert written.status is ToolStatus.OK, written.deny_reason
        [note] = written.data
        read = await gateway.call(
            calls.intent(
                "get_offense_notes", {"offense_id": offense_id, "filter": f"id = {note['id']}"}
            )
        )
        status = RunStatus.COMPLETED
    finally:
        async with sessions.begin() as session:
            await finish_agent_run(
                session,
                calls.run_id,
                status=status,
                result=None,
                tokens=0,
                tool_calls=calls.count,
                ended_at=datetime.now(UTC),
            )
        await engine.dispose()

    print(json.dumps(summary(offense_id, note, read), indent=2))
    assert (len(NOTE), utf16_length(NOTE)) == (2000, 2000)
    assert note["note_text"] == NOTE
    assert [row["note_text"] for row in read.data] == [NOTE]


class ExecutorCalls:
    """Intents of one run of the pseudo agent action-executor, with the note profile's tools."""

    def __init__(
        self, *, tools: dict[str, dict[str, Any]], run_id: str, case_id: str, now: datetime
    ) -> None:
        self.tools = tools
        self.run_id = run_id
        self.case_id = case_id
        self.now = now
        self.count = 0

    def window(self) -> TimeWindow:
        return TimeWindow(start=self.now - timedelta(days=1), end=self.now)

    def task(self) -> AgentTask:
        return AgentTask(
            task_id=self.run_id,
            parent_run_id=self.run_id,
            case_id=self.case_id,
            agent_id=EXECUTOR,
            agent_version="1.0.0",
            objective="T-040 lab test: write a note of 2000 Turkish letters through the gateway.",
            context_refs=[],
            time_window=self.window(),
            budget=Budget(tokens=0, tool_calls=2, seconds=300),
        )

    def intent(self, tool_id: str, arguments: dict[str, Any]) -> ToolIntent:
        tool = self.tools[tool_id]
        self.count += 1
        return ToolIntent(
            run_id=self.run_id,
            case_id=self.case_id,
            agent_id=EXECUTOR,
            toolset_profile=PROFILE,
            tool_id=tool_id,
            tool_schema_version=tool["schema_version"],
            arguments=arguments,
            reason="T-040 lab test: a long Turkish note goes to QRadar in a form body.",
            expected_evidence="The note as QRadar stored it.",
            time_window=self.window(),
            cost_class=tool["cost_class"],
        )


async def tool_list(gateway_url: str, token: str) -> dict[str, dict[str, Any]]:
    """The note profile's tools by ID, as the gateway lists them (GET /v1/tools)."""
    async with httpx2.AsyncClient(base_url=gateway_url, timeout=30) as client:
        response = await client.get("/v1/tools", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    listed = response.json()
    assert listed["name"] == PROFILE
    return {tool["id"]: tool for tool in listed["tools"]}


def summary(offense_id: int, note: dict[str, Any], read: ToolResult) -> dict[str, Any]:
    """What the PR reports; the note's own text is left out."""
    return {
        "offense_id": offense_id,
        "note_id": note["id"],
        "characters": len(NOTE),
        "utf16_units": utf16_length(NOTE),
        "query_string_before_t040": len(urlencode({"note_text": NOTE})),
        "read_back_equal": [row.get("note_text") for row in read.data] == [NOTE],
        "author": note.get("username"),
    }
