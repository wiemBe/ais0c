"""querylang reads only the pipeline file it is given and makes no network calls.

File and network access is observed with a Python audit hook (PEP 578), which also sees
access made by pySigma and the QRadar backend.
"""

import shutil
import socket
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from ais0c_querylang import compile_sigma, load_pipeline

TESTS = Path(__file__).resolve().parent
PIPELINE_PATH = TESTS.parents[2] / "config/sigma/qradar-pipeline.yaml"
H2_RULE = TESTS / "rules/h2_dcsync.yml"

_FILE_EVENTS = frozenset({"open", "os.listdir", "os.scandir", "glob.glob"})
_NETWORK_EVENTS = frozenset({"urllib.Request", "http.client.connect"})
_recorded: list[tuple[str, tuple[object, ...]]] | None = None


def _audit(event: str, args: tuple[object, ...]) -> None:
    if _recorded is None:
        return
    if event in _FILE_EVENTS or event in _NETWORK_EVENTS or event.startswith("socket."):
        _recorded.append((event, args))


# Audit hooks cannot be removed; this one records only inside recording().
sys.addaudithook(_audit)


@contextmanager
def recording() -> Generator[list[tuple[str, tuple[object, ...]]]]:
    global _recorded
    _recorded = []
    try:
        yield _recorded
    finally:
        _recorded = None


def test_load_and_compile_read_only_the_given_pipeline_file(tmp_path: Path) -> None:
    pipeline_path = tmp_path / "pipeline.yaml"
    shutil.copyfile(PIPELINE_PATH, pipeline_path)
    rule_yaml = H2_RULE.read_text(encoding="utf-8")
    compile_sigma(rule_yaml, load_pipeline(pipeline_path))  # warm up lazy imports

    with recording() as events:
        compile_sigma(rule_yaml, load_pipeline(pipeline_path))

    assert events, "the audit hook recorded nothing; the test would not notice access"
    assert {(event, str(args[0])) for event, args in events} == {("open", str(pipeline_path))}


def test_audit_hook_sees_sockets() -> None:
    # Guards the test above: creating a socket is recorded, so a network call would be too.
    with recording() as events, socket.socket():
        pass
    assert [event for event, _ in events] == ["socket.__new__"]
