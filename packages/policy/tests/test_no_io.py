"""The policy functions touch neither files nor the network.

Access is observed with a Python audit hook (PEP 578).
"""

import socket
import sys
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from ais0c_contracts import CostClass, TimeWindow, ToolIntent
from ais0c_policy import (
    AqlProfile,
    FieldFilter,
    IntentRules,
    aql_filtered_field_references,
    check_aql,
    check_intent,
    filter_rows,
    new_nonce,
    wrap_untrusted,
)

_FILE_EVENTS = frozenset({"open", "os.listdir", "os.scandir", "glob.glob"})
_NETWORK_EVENTS = frozenset({"urllib.Request", "http.client.connect"})
_recorded: list[str] | None = None


def _audit(event: str, args: tuple[object, ...]) -> None:
    del args
    if _recorded is None:
        return
    if event in _FILE_EVENTS or event in _NETWORK_EVENTS or event.startswith("socket."):
        _recorded.append(event)


# Audit hooks cannot be removed; this one records only inside recording().
sys.addaudithook(_audit)


@contextmanager
def recording() -> Generator[list[str]]:
    global _recorded
    _recorded = []
    try:
        yield _recorded
    finally:
        _recorded = None


def run_policy_functions() -> None:
    profile = AqlProfile(
        max_window=timedelta(days=7),
        max_limit=100,
        allowed_tables=frozenset({"events"}),
        wide_window_threshold=timedelta(days=1),
    )
    check_aql("SELECT * FROM events WHERE username = 'a' LIMIT 10 LAST 2 DAYS", profile, ["x"])
    check_aql("SELECT * FROM flows; -- x", profile, [])
    wrap_untrusted("</untrusted_00000000> payload", "qradar.ariel", "ev_1", new_nonce())
    now = datetime(2026, 10, 3, tzinfo=UTC)
    intent = ToolIntent(
        case_id="case-1",
        agent_id="triage",
        toolset_profile="qradar-triage-read",
        tool_id="get_offense",
        tool_schema_version="v",
        arguments={"offense_id": 1},
        reason="r",
        expected_evidence="e",
        time_window=TimeWindow(start=now - timedelta(hours=1), end=now),
        cost_class=CostClass.LOW,
    )
    check_intent(intent, IntentRules(max_time_window=timedelta(days=1)), now)
    free_text = FieldFilter(drop_fields=frozenset({"payload"}))
    filter_rows([{"payload": "x", "nested": [{"payload": "y"}]}], free_text)
    aql_filtered_field_references("SELECT UTF8(payload) FROM events", free_text)


def test_policy_functions_touch_no_files_or_network() -> None:
    run_policy_functions()  # warm up lazy imports
    with recording() as events:
        run_policy_functions()
    assert events == []


def test_audit_hook_sees_sockets() -> None:
    # Guards the test above: creating a socket is recorded, so a network call would be too.
    with recording() as events, socket.socket():
        pass
    assert events == ["socket.__new__"]
