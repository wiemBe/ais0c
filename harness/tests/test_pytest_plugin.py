"""The `lab` marker skips lab QRadar tests unless the lab settings are present."""

import pytest

pytest_plugins = ["pytester"]

LAB_URL = "https://qradar-lab.example.com"
LAB_TOKEN = "placeholder"  # noqa: S105 - synthetic value, never a real token
LAB_ENV_VARS = ("QRADAR_LAB_URL", "QRADAR_LAB_TOKEN")

SUITE = """
import pytest

@pytest.mark.lab
def test_against_lab_qradar():
    pass

def test_unit():
    pass
"""


@pytest.fixture
def suite(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    for name in LAB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    pytester.makepyfile(SUITE)
    return pytester


def run_suite(pytester: pytest.Pytester) -> pytest.RunResult:
    # --strict-markers fails the run if the plugin does not register the marker.
    return pytester.runpytest("-p", "ais0c_harness.pytest_plugin", "--strict-markers", "-rs")


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        pytest.param({"QRADAR_LAB_TOKEN": LAB_TOKEN}, "QRADAR_LAB_URL", id="no-url"),
        pytest.param({"QRADAR_LAB_URL": LAB_URL}, "QRADAR_LAB_TOKEN", id="no-token"),
        pytest.param(
            {"QRADAR_LAB_URL": "", "QRADAR_LAB_TOKEN": LAB_TOKEN}, "QRADAR_LAB_URL", id="empty-url"
        ),
        pytest.param({}, "QRADAR_LAB_URL, QRADAR_LAB_TOKEN", id="nothing-set"),
    ],
)
def test_lab_tests_are_skipped_without_lab_settings(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], missing: str
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    result = run_suite(suite)

    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines([f"*lab test: {missing} not set*"])


def test_lab_tests_run_when_lab_settings_are_present(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("QRADAR_LAB_URL", LAB_URL)
    monkeypatch.setenv("QRADAR_LAB_TOKEN", LAB_TOKEN)

    result = run_suite(suite)

    result.assert_outcomes(passed=2)
