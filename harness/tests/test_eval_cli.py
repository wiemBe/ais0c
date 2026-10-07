"""`python -m ais0c_harness.eval` (T-030 criterion 11): every command's exit codes and the
settings errors, which stop `run` before any model call."""

import io
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from pydantic_ai.models import Model

from ais0c_agents import LITELLM_API_KEY_ENV, LITELLM_BASE_URL_ENV
from ais0c_harness.eval import AgentConfig, ScenarioBase, TriageScenario, load_report, load_suites
from ais0c_harness.eval.cli import Dependencies, main

from .eval_helpers import REPO_ROOT, varying_model

KEY = {LITELLM_API_KEY_ENV: "test-key-not-a-secret"}


class Factory:
    """A model factory that records its calls; `fp` makes every run answer fp."""

    def __init__(self, *, fp: bool = False) -> None:
        self.calls = 0
        self.fp = fp

    def __call__(self, config: AgentConfig, played: ScenarioBase, env: Mapping[str, str]) -> Model:
        self.calls += 1
        assert isinstance(played, TriageScenario)
        return varying_model(played, [{"verdict": "fp"} if self.fp else None])


def cli(
    args: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
    deps: Dependencies | None = None,
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["--root", str(REPO_ROOT), *args],
        environ=KEY if environ is None else environ,
        deps=deps or Dependencies(model_factory=Factory(), retry_delay_seconds=0),
        stdout=out,
        stderr=err,
    )
    return code, out.getvalue(), err.getvalue()


def run_args(out: Path, *extra: str) -> list[str]:
    return [
        "run",
        "--suite",
        "trust-layers",
        "--scenario",
        "tl-01-catalog-note-fp",
        "--k",
        "1",
        "--out",
        str(out),
        *extra,
    ]


# --- list ---------------------------------------------------------------------------------------


def test_list_shows_suites_scenarios_and_versions() -> None:
    code, out, _ = cli(["list"])

    assert code == 0
    for suite in load_suites(REPO_ROOT):
        assert f"{suite.id}  security  agent triage  version {suite.version}" in out
        for scenario in suite.scenarios:
            assert f"  {scenario.id}  {scenario.version}  " in out


def test_list_takes_one_suite() -> None:
    code, out, _ = cli(["list", "--suite", "adversarial-fn"])

    assert code == 0
    assert "adversarial-fn" in out
    assert "trust-layers" not in out


def test_list_of_an_unknown_suite_is_a_settings_error() -> None:
    code, out, err = cli(["list", "--suite", "prompt-injection"])

    assert (code, out) == (2, "")
    assert "no suite prompt-injection" in err


def test_the_module_runs_as_a_command() -> None:
    done = subprocess.run(
        [sys.executable, "-m", "ais0c_harness.eval", "list", "--suite", "trust-layers"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert "tl-01-catalog-note-fp" in done.stdout


# --- run ----------------------------------------------------------------------------------------


def test_run_exits_0_when_the_gates_pass(tmp_path: Path) -> None:
    factory = Factory()
    code, out, _ = cli(
        run_args(tmp_path / "out"), deps=Dependencies(model_factory=factory, retry_delay_seconds=0)
    )

    assert code == 0
    assert factory.calls == 1
    assert out.splitlines()[-1].startswith("passed;")
    assert load_report(tmp_path / "out" / "report.json").passed


def test_run_exits_1_when_a_gate_fails(tmp_path: Path) -> None:
    deps = Dependencies(model_factory=Factory(fp=True), retry_delay_seconds=0)

    code, out, _ = cli(run_args(tmp_path / "out"), deps=deps)

    assert code == 1
    assert "security_pass_k: FAIL" in out
    assert not load_report(tmp_path / "out" / "report.json").passed


@pytest.mark.parametrize("key", [None, "", "   "])
def test_run_without_a_litellm_key_stops_before_any_model(tmp_path: Path, key: str | None) -> None:
    factory = Factory()
    environ = {} if key is None else {LITELLM_API_KEY_ENV: key}

    code, _, err = cli(
        run_args(tmp_path / "out"), environ=environ, deps=Dependencies(model_factory=factory)
    )

    assert code == 2
    assert "LITELLM_API_KEY is not set" in err
    assert factory.calls == 0
    assert not (tmp_path / "out").exists()


def test_run_never_overwrites_a_report(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "report.json").write_text("{}", encoding="utf-8")
    factory = Factory()

    code, _, err = cli(run_args(out), deps=Dependencies(model_factory=factory))

    assert code == 2
    assert "is not an empty directory" in err
    assert factory.calls == 0
    assert (out / "report.json").read_text(encoding="utf-8") == "{}"


def test_run_into_an_empty_directory_is_fine(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()

    code, _, _ = cli(run_args(tmp_path / "out"))

    assert code == 0


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--suite", "prompt-injection"], "no suite prompt-injection"),
        (
            ["--suite", "trust-layers", "--scenario", "afn-01-username-pentest-label"],
            "no scenario afn-01",
        ),
        (
            ["--suite", "trust-layers", "--registry", "config/models/missing.yaml"],
            "no model registry",
        ),
    ],
)
def test_run_with_an_unknown_name_stops_before_any_model(
    tmp_path: Path, args: list[str], message: str
) -> None:
    factory = Factory()

    code, _, err = cli(
        ["run", *args, "--out", str(tmp_path / "out")], deps=Dependencies(model_factory=factory)
    )

    assert code == 2
    assert message in err
    assert factory.calls == 0


@pytest.mark.parametrize("option", ["--k", "--concurrency", "--max-total-tokens"])
def test_run_rejects_a_count_below_one(tmp_path: Path, option: str) -> None:
    code, _, _ = cli([*run_args(tmp_path / "out"), option, "0"])

    assert code == 2


def test_run_with_an_invalid_litellm_url_stops_before_any_model(tmp_path: Path) -> None:
    environ = {**KEY, LITELLM_BASE_URL_ENV: "ftp://litellm:4000"}

    code, _, err = cli(run_args(tmp_path / "out"), environ=environ, deps=Dependencies())

    assert code == 2
    assert "LITELLM_BASE_URL must be an http(s) URL" in err
    assert not (tmp_path / "out").exists()


# --- gate ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reports(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("gate")
    found: dict[str, Path] = {}
    for name, fp, extra in (("good", False, []), ("fp", True, []), ("k2", False, ["--k", "2"])):
        out = root / name
        args = run_args(out) if not extra else [*run_args(out)[:-4], *extra, "--out", str(out)]
        cli(args, deps=Dependencies(model_factory=Factory(fp=fp), retry_delay_seconds=0))
        found[name] = out / "report.json"
    return found


def test_gate_exits_0_1_and_2(reports: dict[str, Path]) -> None:
    good, fp, k2 = (str(reports[name]) for name in ("good", "fp", "k2"))

    assert cli(["gate", "--baseline", good, "--candidate", good])[0] == 0
    assert cli(["gate", "--baseline", good, "--candidate", fp])[0] == 1
    code, out, _ = cli(["gate", "--baseline", good, "--candidate", k2])
    assert code == 2
    assert "k differs: 1 and 2" in out


def test_gate_takes_the_allowed_drop(reports: dict[str, Path]) -> None:
    good, fp = str(reports["good"]), str(reports["fp"])

    code, out, _ = cli(["gate", "--baseline", good, "--candidate", fp, "--max-pass-rate-drop", "1"])

    # pass^k still fails: the candidate's hard gate and the scenario block.
    assert code == 1
    assert "drops" not in out


@pytest.mark.parametrize("drop", ["2", "-0.1", "ten"])
def test_gate_rejects_an_invalid_drop(reports: dict[str, Path], drop: str) -> None:
    good = str(reports["good"])

    assert (
        cli(["gate", "--baseline", good, "--candidate", good, "--max-pass-rate-drop", drop])[0] == 2
    )


def test_gate_with_an_unreadable_report_is_a_settings_error(
    reports: dict[str, Path], tmp_path: Path
) -> None:
    broken = tmp_path / "report.json"
    broken.write_text('{"schema_version": 2}', encoding="utf-8")

    for candidate in (str(broken), str(tmp_path / "missing.json")):
        code, _, err = cli(["gate", "--baseline", str(reports["good"]), "--candidate", candidate])
        assert code == 2
        assert "cannot read the report" in err


def test_no_command_is_a_settings_error() -> None:
    assert cli([])[0] == 2
