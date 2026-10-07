"""T-052 criterion 3: record the planner's closed lab offense (`QRADAR_LAB_OFFENSE_ID`, 30).

Marked ``lab``: skipped unless the lab's settings are set, and skipped as well without the dev
stack's (AIS0C_GATEWAY_URL, AIS0C_WORKER_SECRETS_DIR, AIS0C_DATABASE_URL: a gateway that reaches
the lab). The test reads the offense's state straight from the lab before and after the recording
and asserts it is the same: the recorder opens, closes and notes nothing. To keep the test short it
leaves QRadar's own busy log source types out of the table; the committed recording
`harness/recordings/lab-30-dcsync` has them (only Health Metrics are left out).

    set -a; . ~/.config/ais0c/lab.env; set +a
    AIS0C_GATEWAY_URL=http://127.0.0.1:8090 AIS0C_WORKER_SECRETS_DIR=<dir with the tokens> \\
    AIS0C_DATABASE_URL=... QRADAR_LAB_OFFENSE_ID=30 uv run pytest harness/tests/test_replay_lab.py
"""

import asyncio
import json
import os
import ssl
import urllib.request
from pathlib import Path

import pytest

from ais0c_harness.replay.aql import run_query
from ais0c_harness.replay.record import record_offense, same_answer
from ais0c_harness.replay.recording import load_recording

from .eval_helpers import REPO_ROOT

pytestmark = pytest.mark.lab

STACK = ("AIS0C_GATEWAY_URL", "AIS0C_WORKER_SECRETS_DIR", "AIS0C_DATABASE_URL")
BUSY_TYPES = ("Health Metrics", "System Notification", "SIM Audit")
STATE_FIELDS = (
    "id",
    "status",
    "event_count",
    "last_updated_time",
    "close_time",
    "closing_reason_id",
    "assigned_to",
    "follow_up",
    "protected",
    "inactive",
)


def lab_offense(offense_id: int) -> dict[str, object]:
    base = os.environ["QRADAR_LAB_URL"]
    base = base if "://" in base else f"https://{base}"
    context = ssl.create_default_context()
    if os.environ.get("QRADAR_LAB_VERIFY_SSL", "true").lower() in {"false", "0", "no"}:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(  # noqa: S310 - the lab's own https URL
        f"{base}/api/siem/offenses/{offense_id}?fields={','.join(STATE_FIELDS)}",
        headers={
            "SEC": os.environ["QRADAR_LAB_TOKEN"],
            "Version": "29.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, context=context, timeout=60) as response:  # noqa: S310
        state = json.loads(response.read())
    assert isinstance(state, dict)
    return state


def test_recording_a_closed_lab_offense_leaves_it_as_it_was(tmp_path: Path) -> None:
    missing = [name for name in STACK if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip(f"set {', '.join(missing)} to record from the lab through the dev stack")
    offense_id = int(os.environ.get("QRADAR_LAB_OFFENSE_ID", "30"))
    before = lab_offense(offense_id)
    assert before["status"] == "CLOSED", f"lab offense {offense_id} must be closed"
    directory = tmp_path / f"lab-{offense_id}-test"

    manifest = asyncio.run(
        record_offense(
            root=REPO_ROOT,
            offense_id=offense_id,
            directory=directory,
            gateway_url=os.environ["AIS0C_GATEWAY_URL"].strip(),
            secrets_dir=Path(os.environ["AIS0C_WORKER_SECRETS_DIR"]),
            database_url=os.environ["AIS0C_DATABASE_URL"].strip(),
            domains=["bank.example"],
            hosts=["DC-LAB-01", "DC-LAB-02", "qradarce2"],
            excluded=BUSY_TYPES,
        )
    )
    after = lab_offense(offense_id)

    assert before == after
    recording = load_recording(directory)
    assert manifest.offense_id == offense_id
    assert recording.offense.offense_id == offense_id
    assert recording.manifest.excluded == list(BUSY_TYPES)
    assert len(recording.audits) >= 5
    for audit in recording.audits:
        ours = run_query(
            audit.query, recording.event_rows, now=recording.offense.last_updated_time
        ).rows
        assert same_answer(audit.query, [dict(row) for row in ours], audit.rows), audit.name
