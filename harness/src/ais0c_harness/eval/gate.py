"""The model gate (B2, agent-harness.md §5, decision T-64 (4)): compare two reports.

The baseline is the dev report; the candidate is the report of the same scenarios run with the
on-prem production models. Two reports are compared only when they ran the same thing apart
from the model: the same suite and scenario versions, agent versions and prompt hashes, tool
lists and k. Otherwise the gate is not comparable (exit 2) and says why. Different model
releases are the point; both are printed.

The candidate is blocked (exit 1) when one of its hard gates fails, when a scenario that passes
pass^k in the baseline does not in the candidate, or when a suite's pass rate drops by more
than the allowed drop (default 10 points). Otherwise it passes (exit 0). Token and time
differences are printed for information only.
"""

from dataclasses import dataclass
from fractions import Fraction
from typing import Final

from ais0c_harness.eval.report import Report, Spread

PASS: Final = 0
BLOCK: Final = 1
INCOMPARABLE: Final = 2
DEFAULT_MAX_PASS_RATE_DROP: Final = Fraction(1, 10)


@dataclass(frozen=True)
class GateResult:
    exit_code: int
    reasons: list[str]
    """Why the gate blocked or could not compare; empty when it passed."""
    text: str
    """Everything the gate prints."""


def incomparable_reasons(baseline: Report, candidate: Report) -> list[str]:
    """Why the two reports cannot be compared; empty when they can."""
    reasons: list[str] = []
    if baseline.settings.k != candidate.settings.k:
        reasons.append(f"k differs: {baseline.settings.k} and {candidate.settings.k}")
    pairs = [
        ("suite versions", _suites(baseline), _suites(candidate)),
        ("scenario versions", _scenarios(baseline), _scenarios(candidate)),
        ("agent versions", _agents(baseline, "agent_version"), _agents(candidate, "agent_version")),
        ("prompt hashes", _agents(baseline, "prompt_hash"), _agents(candidate, "prompt_hash")),
        (
            "tool profile hashes",
            _agents(baseline, "toolset_sha256"),
            _agents(candidate, "toolset_sha256"),
        ),
    ]
    for name, left, right in pairs:
        if left != right:
            keys = sorted(
                key for key in left.keys() | right.keys() if left.get(key) != right.get(key)
            )
            reasons.append(f"{name} differ: {', '.join(keys)}")
    return reasons


def compare_reports(
    baseline: Report,
    candidate: Report,
    *,
    max_pass_rate_drop: Fraction = DEFAULT_MAX_PASS_RATE_DROP,
) -> GateResult:
    lines = ["Model gate (B2)", "", "Model releases:"]
    for label, report in (("baseline", baseline), ("candidate", candidate)):
        for agent in report.agents:
            release = agent.model_release.model_dump(mode="json", exclude_none=True)
            lines.append(f"  {label} {agent.agent_id} {agent.model_alias}: {release}")
    lines.append("")

    reasons = incomparable_reasons(baseline, candidate)
    if reasons:
        lines += ["Not comparable:", *(f"  - {reason}" for reason in reasons)]
        return GateResult(exit_code=INCOMPARABLE, reasons=reasons, text="\n".join(lines) + "\n")

    for gate in candidate.hard_gates:
        if not gate.passed:
            reasons.append(f"candidate hard gate {gate.id} fails: {gate.detail}")
    candidate_scenarios = {scenario.scenario_id: scenario for scenario in candidate.scenarios}
    for scenario in baseline.scenarios:
        other = candidate_scenarios[scenario.scenario_id]
        if scenario.pass_k and not other.pass_k:
            reasons.append(
                f"{scenario.scenario_id} passes pass^k in the baseline, not in the candidate "
                f"({other.status}, {other.passes}/{other.k})"
            )
    candidate_suites = {suite.id: suite for suite in candidate.suites}
    lines.append("Pass rates (baseline -> candidate):")
    for suite in baseline.suites:
        other = candidate_suites[suite.id]
        before = Fraction(suite.passes, suite.runs) if suite.runs else Fraction(0)
        after = Fraction(other.passes, other.runs) if other.runs else Fraction(0)
        lines.append(
            f"  {suite.id}: {float(before):.0%} -> {float(after):.0%} "
            f"({suite.passes}/{suite.runs} -> {other.passes}/{other.runs})"
        )
        if before - after > max_pass_rate_drop:
            reasons.append(
                f"{suite.id}: the pass rate drops {float(before - after):.0%}, more than "
                f"{float(max_pass_rate_drop):.0%}"
            )

    lines += ["", "For information (median, baseline -> candidate):"]
    baseline_scenarios = {scenario.scenario_id: scenario for scenario in baseline.scenarios}
    for scenario_id, scenario in baseline_scenarios.items():
        other = candidate_scenarios[scenario_id]
        lines.append(
            f"  {scenario_id}: tokens {_median(scenario.tokens)} -> {_median(other.tokens)}, "
            f"seconds {_median(scenario.seconds, '{:.1f}')} -> {_median(other.seconds, '{:.1f}')}"
        )
    lines.append(f"  total tokens: {baseline.total_tokens:,} -> {candidate.total_tokens:,}")
    lines.append("")

    if reasons:
        lines += ["Blocked:", *(f"  - {reason}" for reason in reasons)]
        return GateResult(exit_code=BLOCK, reasons=reasons, text="\n".join(lines) + "\n")
    lines.append("Passed.")
    return GateResult(exit_code=PASS, reasons=[], text="\n".join(lines) + "\n")


def _suites(report: Report) -> dict[str, str]:
    return {suite.id: suite.version for suite in report.suites}


def _scenarios(report: Report) -> dict[str, str]:
    return {
        f"{scenario.suite_id}/{scenario.scenario_id}": scenario.scenario_version
        for scenario in report.scenarios
    }


def _agents(report: Report, field: str) -> dict[str, str]:
    return {agent.agent_id: str(getattr(agent, field)) for agent in report.agents}


def _median(spread: Spread | None, form: str = "{:,.0f}") -> str:
    return "-" if spread is None else form.format(spread.median)


def parse_drop(value: str) -> Fraction:
    """`--max-pass-rate-drop`: a fraction between 0 and 1, e.g. 0.10."""
    drop = Fraction(value)
    if not 0 <= drop <= 1:
        raise ValueError("the pass rate drop is between 0 and 1")
    return drop
