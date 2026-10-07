"""The queries the agents really wrote (T-052 criterion 4): at least 95% of those the gateway's
AQL Guard lets through run on the replay engine, and the engine refuses what QRadar refused.

`agent_queries.tsv` holds the Ariel queries of the Investigation and Verification runs in the dev
database (offenses 30-35), anonymized as a recording is. The queries the Guard of today denies are
not the engine's business: the Guard runs first, as in the gateway. The engine's answer for the rest
is rows, QRadar's error, or `Unsupported`; only the last counts against it."""

from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from ais0c_harness.replay.aql import QueryError, Unsupported, run_query
from ais0c_mcp_gateway.registry import Registry, load_registry
from ais0c_policy import check_aql

from .eval_helpers import REPO_ROOT

CORPUS: Final = Path(__file__).parent / "agent_queries.tsv"
RECORDING: Final = "lab-30-dcsync"
MINIMUM_SHARE: Final = 0.95
PROFILES: Final = {
    "investigation": "qradar-investigate-read",
    "verification": "qradar-verify-read",
}


@dataclass(frozen=True)
class Written:
    agent: str
    status: str
    query: str


def corpus() -> list[Written]:
    found = [
        Written(*line.split("\t", 2))
        for line in CORPUS.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    return found


@pytest.fixture(scope="module")
def registry() -> Registry:
    return load_registry(REPO_ROOT / "config", ("qradar",))


def outcome(written: Written, registry: Registry) -> str:
    """`denied` (by today's Guard), `rows`, `error` (QRadar's) or `unsupported`."""
    from ais0c_harness.replay.recording import recording_of

    profile = registry.profiles[PROFILES[written.agent]]
    assert profile.aql is not None
    if not check_aql(written.query, profile.aql, profile.connector.indexed_fields).allowed:
        return "denied"
    recording = recording_of(REPO_ROOT.resolve(), RECORDING)
    try:
        run_query(written.query, recording.event_rows, now=recording.offense.last_updated_time)
    except Unsupported:
        return "unsupported"
    except QueryError:
        return "error"
    return "rows"


def test_the_corpus_is_the_agents_own_queries() -> None:
    written = corpus()

    assert len(written) == 75
    assert {item.agent for item in written} == {"investigation", "verification"}
    assert {item.status for item in written} == {"ok", "error", "denied"}


def test_at_least_95_percent_of_the_queries_the_guard_allows_run(registry: Registry) -> None:
    results = [(item, outcome(item, registry)) for item in corpus()]
    allowed = [(item, result) for item, result in results if result != "denied"]
    unsupported = [item.query for item, result in allowed if result == "unsupported"]

    assert len(allowed) >= 60
    assert len(unsupported) <= (1 - MINIMUM_SHARE) * len(allowed), unsupported
    # What the engine cannot run is a field the recording does not keep, nothing else.
    assert all(
        "identityhostname" in query.lower() or "devicetime" in query.lower()
        for query in unsupported
    )


def test_a_query_qradar_ran_is_never_refused_by_the_engine(registry: Registry) -> None:
    refused = [
        item.query
        for item in corpus()
        if item.status == "ok" and outcome(item, registry) == "error"
    ]

    assert refused == []


def test_a_query_qradar_refused_is_never_answered_with_rows(registry: Registry) -> None:
    answered = [
        item.query
        for item in corpus()
        if item.status == "error" and outcome(item, registry) == "rows"
    ]

    assert answered == []
