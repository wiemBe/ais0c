# ruff: noqa: S608 - AQL test queries, not SQL built from input
"""The recording format and the anonymizer (T-052 criterion 2).

The format is read through its models and a damaged recording is refused with the file named.
The anonymizer's negative test: a synthetic input with private addresses, a public address and
the lab's names comes out with none of them, an address means the same everywhere, and the
mapping table is written nowhere. The repository test: no recording under harness/recordings/
holds an address outside the documentation ranges.
"""

import gzip
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from ais0c_harness.replay.anonymize import (
    AnonymizationError,
    Anonymizer,
    addresses,
    foreign_addresses,
    is_documentation,
)
from ais0c_harness.replay.record import RecordError, build_recording
from ais0c_harness.replay.recording import (
    AUDITS_FILE,
    EVENTS_FILE,
    MANIFEST_FILE,
    OFFENSE_FILE,
    RECORDINGS_DIR,
    AuditQuery,
    RecordingError,
    load_recording,
)

from .eval_helpers import REPO_ROOT
from .replay_helpers import (
    NOW,
    OFFENSE,
    event,
    raw_recording,
    write_synthetic,
)

LAB_TEXT = (
    "<134>Oct  5 15:07:43 DC-LAB-01 AgentDevice=WindowsLog Computer=DC-LAB-01.bank.example "
    "OriginatingComputer=192.168.10.157 Source Network Address: 10.20.30.40 peer 8.8.8.8 "
    "again 192.168.10.157 v6 fe80::1 and 2606:4700:4700::1111 object DC=bank,DC=example "
    "workstation dc-lab-02 version 6.3.9600.17415 time 14:58:22"
)


# --- the format -------------------------------------------------------------------------------


def test_a_recording_reads_back_through_its_models(tmp_path: Path) -> None:
    recording = write_synthetic(tmp_path / "synthetic-77")

    assert recording.manifest.recording_id == "synthetic-77"
    assert recording.manifest.offense_id == 77
    assert recording.manifest.events == len(recording.events) == 6
    assert recording.offense.offense_source == "svc_backup"
    assert [call.tool_id for call in recording.calls] == ["get_offense", "get_rule"]
    assert {item.name for item in recording.manifest.files} == {
        "offense.json",
        "enrichment.json",
        "tools.json",
        "events.jsonl.gz",
        "audits.json",
    }


def test_a_recording_is_reproducible(tmp_path: Path) -> None:
    first = write_synthetic(tmp_path / "one-1")
    second = write_synthetic(tmp_path / "one-2")

    assert [item.sha256 for item in first.manifest.files] == [
        item.sha256 for item in second.manifest.files
    ]


def test_a_recording_is_never_overwritten(tmp_path: Path) -> None:
    write_synthetic(tmp_path / "once-1")

    with pytest.raises(RecordingError, match="never overwritten"):
        build_recording(raw_recording(), tmp_path / "once-1", recorded_at=NOW)


def damaged(tmp_path: Path) -> Path:
    directory = tmp_path / "damaged-1"
    write_synthetic(directory)
    return directory


def test_a_changed_file_is_refused_naming_the_file(tmp_path: Path) -> None:
    directory = damaged(tmp_path)
    (directory / OFFENSE_FILE).write_text("{}", encoding="utf-8")

    with pytest.raises(RecordingError, match=r"offense\.json: does not match the manifest"):
        load_recording(directory)


def test_a_missing_file_is_refused_naming_the_file(tmp_path: Path) -> None:
    directory = damaged(tmp_path)
    (directory / AUDITS_FILE).unlink()

    with pytest.raises(RecordingError, match=r"audits\.json: cannot be read"):
        load_recording(directory)


def test_a_manifest_that_is_not_one_is_refused(tmp_path: Path) -> None:
    directory = damaged(tmp_path)
    (directory / MANIFEST_FILE).write_text(
        json.dumps({"recording_id": "damaged-1"}), encoding="utf-8"
    )

    with pytest.raises(RecordingError, match=r"manifest\.json"):
        load_recording(directory)


def test_a_manifest_for_another_directory_is_refused(tmp_path: Path) -> None:
    directory = damaged(tmp_path)
    moved = tmp_path / "elsewhere-1"
    shutil.copytree(directory, moved)

    with pytest.raises(RecordingError, match="names the recording damaged-1"):
        load_recording(moved)


def test_a_row_count_that_is_wrong_is_refused(tmp_path: Path) -> None:
    directory = damaged(tmp_path)
    manifest = json.loads((directory / MANIFEST_FILE).read_text(encoding="utf-8"))
    manifest["events"] = 7
    (directory / MANIFEST_FILE).write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RecordingError, match=r"events\.jsonl\.gz: 6 rows, the manifest says 7"):
        load_recording(directory)


def test_an_event_row_with_a_wrong_type_is_refused_with_its_line(tmp_path: Path) -> None:
    directory = damaged(tmp_path)
    lines = gzip.decompress((directory / EVENTS_FILE).read_bytes()).decode().splitlines()
    row = json.loads(lines[2])
    row["qid"] = "not a number"
    lines[2] = json.dumps(row)
    data = gzip.compress(("\n".join(lines) + "\n").encode())
    (directory / EVENTS_FILE).write_bytes(data)
    _refresh(directory, EVENTS_FILE)

    with pytest.raises(RecordingError, match=r"events\.jsonl\.gz: line 3: qid"):
        load_recording(directory)


def test_an_event_row_with_an_unknown_column_is_refused(tmp_path: Path) -> None:
    directory = damaged(tmp_path)
    lines = gzip.decompress((directory / EVENTS_FILE).read_bytes()).decode().splitlines()
    row = json.loads(lines[0])
    row["eventname"] = "x"
    lines[0] = json.dumps(row)
    (directory / EVENTS_FILE).write_bytes(gzip.compress(("\n".join(lines) + "\n").encode()))
    _refresh(directory, EVENTS_FILE)

    with pytest.raises(RecordingError, match=r"events\.jsonl\.gz: line 1: eventname"):
        load_recording(directory)


def _refresh(directory: Path, name: str) -> None:
    """Make the manifest agree with a rewritten file, so the file's own content is what fails."""
    manifest = json.loads((directory / MANIFEST_FILE).read_text(encoding="utf-8"))
    data = (directory / name).read_bytes()
    for entry in manifest["files"]:
        if entry["name"] == name:
            entry["sha256"] = hashlib.sha256(data).hexdigest()
            entry["size"] = len(data)
    (directory / MANIFEST_FILE).write_text(json.dumps(manifest), encoding="utf-8")


# --- anonymization -----------------------------------------------------------------------------


def anonymizer() -> Anonymizer:
    anonymous = Anonymizer(domains=["bank.example"], hosts=["DC-LAB-01", "DC-LAB-02"])
    anonymous.collect(LAB_TEXT)
    anonymous.freeze()
    return anonymous


def test_no_private_or_public_address_and_no_lab_name_survives() -> None:
    out = anonymizer().text(LAB_TEXT)

    assert foreign_addresses(out) == []
    for leaked in ("192.168.10.157", "10.20.30.40", "8.8.8.8", "fe80::1", "2606:4700"):
        assert leaked not in out
    for name in ("DC-LAB-01", "DC-LAB-02", "bank.example", "DC=bank"):
        assert name.lower() not in out.lower()
    assert "host-01.corp.example.com" in out
    assert "DC=corp,DC=example,DC=com" in out
    assert "host-02" in out


def test_an_address_means_the_same_everywhere() -> None:
    out = anonymizer().text(LAB_TEXT)
    found = [str(address) for address in addresses(out)]

    # 192.168.10.157 appears twice in the input: both become one address.
    assert found.count(found[0]) == 2
    assert len(set(found)) == len(found) - 1
    assert all(is_documentation(address) for address in addresses(out))


def test_the_replacement_is_deterministic_and_collision_free() -> None:
    first, second = anonymizer(), anonymizer()
    reversed_order = Anonymizer(domains=["bank.example"], hosts=["DC-LAB-01", "DC-LAB-02"])
    reversed_order.collect(" ".join(reversed(LAB_TEXT.split(" "))))
    reversed_order.freeze()

    assert first.text(LAB_TEXT) == second.text(LAB_TEXT)
    assert reversed_order.text("192.168.10.157") == first.text("192.168.10.157")
    assert len({first.text(f"10.0.0.{n}") for n in range(1)}) == 1


def test_a_double_colon_separator_is_not_an_address() -> None:
    anonymous = Anonymizer()
    anonymous.collect("SIM Audit-2 :: qradarce2")
    anonymous.freeze()

    assert anonymous.text("SIM Audit-2 :: qradarce2") == "SIM Audit-2 :: qradarce2"
    assert addresses("SIM Audit-2 :: qradarce2") == []


def test_versions_and_clock_times_are_left_alone() -> None:
    out = anonymizer().text(LAB_TEXT)

    assert "6.3.9600.17415" in out
    assert "14:58:22" in out


def test_too_many_addresses_for_the_documentation_ranges_are_refused() -> None:
    anonymous = Anonymizer()
    for third in range(4):
        for last in range(1, 255):
            anonymous.collect(f"10.0.{third}.{last}")

    with pytest.raises(AnonymizationError, match="the documentation ranges hold"):
        anonymous.freeze()


def test_replacing_before_the_mapping_is_made_is_refused() -> None:
    with pytest.raises(AnonymizationError, match="freeze"):
        Anonymizer().text("10.0.0.1")


def test_a_recording_built_from_lab_data_holds_no_foreign_address(tmp_path: Path) -> None:
    lab_events = [
        event(0, sourceip="192.168.10.157", payload=LAB_TEXT),
        event(1000, sourceip="10.20.30.40", username="svc_backup"),
    ]
    directory = tmp_path / "private-1"
    raw = raw_recording(lab_events)
    raw_offense = raw.offense.model_copy(
        update={"source_ips": ["192.168.10.157", "10.20.30.40"], "destination_ips": ["8.8.8.8"]}
    )

    build_recording(
        type(raw)(**{**raw.__dict__, "offense": raw_offense}),
        directory,
        domains=["bank.example"],
        hosts=["DC-LAB-01", "DC-LAB-02"],
        recorded_at=NOW,
    )

    for path in directory.iterdir():
        data = path.read_bytes()
        text = gzip.decompress(data).decode() if path.name.endswith(".gz") else data.decode()
        assert foreign_addresses(text) == []
        assert "192.168.10" not in text
        assert "DC-LAB" not in text
        assert "bank.example" not in text
    first = load_recording(directory).events[0].sourceip
    assert first is not None
    assert first.startswith(("192.0.2.", "198.51.100.", "203.0.113."))


def test_the_mapping_table_is_written_nowhere(tmp_path: Path) -> None:
    directory = tmp_path / "private-2"
    build_recording(
        raw_recording([event(0, sourceip="192.168.10.157")]), directory, recorded_at=NOW
    )

    names = {path.name for path in directory.iterdir()}

    assert names == {
        MANIFEST_FILE,
        OFFENSE_FILE,
        "enrichment.json",
        "tools.json",
        EVENTS_FILE,
        AUDITS_FILE,
    }
    assert "192.168.10.157" not in "".join(
        gzip.decompress(path.read_bytes()).decode()
        if path.name.endswith(".gz")
        else path.read_text(encoding="utf-8")
        for path in directory.iterdir()
    )


def test_a_recording_whose_audit_the_engine_cannot_answer_is_not_written(tmp_path: Path) -> None:
    lying = AuditQuery(
        name="count_all",
        query=f"SELECT COUNT(*) AS n FROM events LIMIT 5 START {OFFENSE.start_time.timestamp() * 1000:.0f} "
        f"STOP {OFFENSE.start_time.timestamp() * 1000 + 100_000:.0f}",
        rows=[{"n": 999.0}],
    )
    directory = tmp_path / "liar-1"

    with pytest.raises(RecordError, match="disagree: count_all"):
        build_recording(raw_recording(audits=[lying]), directory, recorded_at=NOW)

    assert not directory.exists()


# --- the repository's recordings -----------------------------------------------------------------


def repo_recordings() -> list[Path]:
    return sorted(path.parent for path in (REPO_ROOT / RECORDINGS_DIR).glob(f"*/{MANIFEST_FILE}"))


def test_every_recording_in_the_repository_reads() -> None:
    for directory in repo_recordings():
        load_recording(directory)


def test_no_file_under_recordings_holds_an_address_outside_the_documentation_ranges() -> None:
    files = [path for path in (REPO_ROOT / RECORDINGS_DIR).rglob("*") if path.is_file()]
    for path in files:
        data = path.read_bytes()
        text = gzip.decompress(data).decode() if path.suffix == ".gz" else data.decode()
        assert foreign_addresses(text) == [], path
