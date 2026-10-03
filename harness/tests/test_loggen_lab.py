"""Lab QRadar parse checks (acceptance criterion 6), marked ``lab`` so they are
skipped unless QRADAR_LAB_URL and QRADAR_LAB_TOKEN are set.

They guard the wire formats, which is where this generator is most likely to
regress silently: a format the DSM no longer parses still *sends*, but every
event lands as the unparsed "Event 0" (qid 0). Each test sends a scenario to the
lab and asserts, over a tight recent window, that the DSM mapped the events to a
real QID. The Ariel client here is stdlib-only and test-scoped; the platform
reaches QRadar only through the gateway (AGENTS.md hard rule 1), but harness lab
tests may talk to the lab directly (docs/impl/repo-structure.md).
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from ais0c_harness.loggen.__main__ import build
from ais0c_harness.loggen.sender import open_target

pytestmark = pytest.mark.lab

POLL_TIMEOUT_S = 90
POLL_INTERVAL_S = 3


def _verify_ssl() -> bool:
    return os.environ.get("QRADAR_LAB_VERIFY_SSL", "true").lower() not in {"false", "0", "no"}


def _ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not _verify_ssl():
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _api(path: str, method: str = "GET", headers: dict[str, str] | None = None) -> object:
    base = os.environ["QRADAR_LAB_URL"]
    url = f"https://{base}/api{path}" if "://" not in base else f"{base}/api{path}"
    request = urllib.request.Request(url, method=method)  # noqa: S310 - https lab URL from env
    request.add_header("SEC", os.environ["QRADAR_LAB_TOKEN"])
    request.add_header("Version", "29.0")
    request.add_header("Accept", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, context=_ctx(), timeout=60) as response:  # noqa: S310
        body = response.read()
    return json.loads(body) if body else None


def _ariel(aql: str) -> list[dict[str, object]]:
    """Run an Ariel search to completion and return its rows."""
    search = _api("/ariel/searches?query_expression=" + urllib.parse.quote(aql), method="POST")
    assert isinstance(search, dict)
    sid = search["search_id"]
    deadline = time.monotonic() + POLL_TIMEOUT_S
    try:
        while True:
            status = _api(f"/ariel/searches/{sid}")
            assert isinstance(status, dict)
            if status["status"] in {"COMPLETED", "ERROR", "CANCELED"}:
                break
            if time.monotonic() > deadline:
                pytest.fail("Ariel search did not complete in time")
            time.sleep(POLL_INTERVAL_S)
        assert status["status"] == "COMPLETED", status.get("error_messages")
        result = _api(f"/ariel/searches/{sid}/results", headers={"Range": "items=0-999"})
        assert isinstance(result, dict)
        rows = next(iter(result.values()))
        assert isinstance(rows, list)
        return rows
    finally:
        try:
            _api(f"/ariel/searches/{sid}", method="DELETE")
        except urllib.error.HTTPError:
            pass


def _send_to_lab(scenario: str, seed: int) -> int:
    """Send a scenario with a recent base time; return the number of events."""
    host = os.environ["QRADAR_LAB_URL"].split("://")[-1].split(":")[0]
    base_time = datetime.now(UTC) - timedelta(minutes=2)
    events, lines = build(scenario_name=scenario, seed=seed, base_time=base_time)
    with open_target(host, 514, "tcp") as target:
        for line in lines:
            target.send(line)
    return len(events)


def _as_int(value: object) -> int:
    """Ariel returns counts and ids as numbers or numeric strings."""
    return int(float(str(value)))


def _poll_until(
    aql: str, predicate: Callable[[list[dict[str, object]]], bool]
) -> list[dict[str, object]]:
    deadline = time.monotonic() + POLL_TIMEOUT_S
    rows: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        rows = _ariel(aql)
        if predicate(rows):
            return rows
        time.sleep(POLL_INTERVAL_S)
    return rows


def test_fortigate_events_parse_with_the_fortigate_dsm() -> None:
    # FortiGate auto-discovers, so this test is self-contained: it does not
    # depend on any log source existing in the lab beforehand.
    sent = _send_to_lab("s3-vpn-yeni-ulke", seed=101)
    aql = (
        "SELECT qid, COUNT(*) AS n FROM events "
        "WHERE LOGSOURCETYPENAME(devicetype)='Fortinet FortiGate Security Gateway' "
        "GROUP BY qid LAST 8 MINUTES"
    )
    rows = _poll_until(aql, lambda r: sum(_as_int(x["n"]) for x in r) >= sent)
    assert rows, "no FortiGate events were ingested"
    # Every FortiGate event must map to a real QID; qid 0 means the format broke.
    assert all(_as_int(row["qid"]) != 0 for row in rows), (
        f"FortiGate events landed unparsed: {rows}"
    )


def _has(rows: list[dict[str, object]], needle: str) -> bool:
    return any(needle in str(row["q"]).lower() for row in rows)


def test_windows_security_events_parse_with_the_windows_dsm() -> None:
    # Needs the lab's Windows log sources (DC-LAB-01 etc.); if the DSM format
    # regressed, the events would route but land as qid 0 and never appear here.
    _send_to_lab("s2-dcsync", seed=102)
    aql = (
        "SELECT qid, QIDNAME(qid) AS q, COUNT(*) AS n FROM events "
        "WHERE LOGSOURCETYPENAME(devicetype)='Microsoft Windows Security Event Log' "
        "AND qid<>0 GROUP BY qid LAST 8 MINUTES"
    )
    # Poll until both the logon (4624) and the DCSync (4662) events have parsed,
    # rather than stopping at a raw count that leftover logons could satisfy.
    rows = _poll_until(
        aql,
        lambda r: _has(r, "logged on") and _has(r, "operation was performed on an object"),
    )
    assert _has(rows, "logged on"), f"no parsed Windows logon event: {rows}"
    # The DCSync 4662 event (hunt pack H2) is the one the triage must catch.
    assert _has(rows, "operation was performed on an object"), f"no parsed 4662 event: {rows}"
