"""T-049 criterion 1 (T-55): copy-ready epoch-millisecond AQL bounds."""

import re
from datetime import timedelta
from itertools import pairwise

from ais0c_agents import build_verification_agent, load_aql_rules
from ais0c_agents.investigation import aql_window, example_query, render_time_window
from ais0c_agents.verification import EXAMPLE_LIMIT, MAX_QUERY_WINDOW
from ais0c_contracts import TimeWindow

from .helpers import (
    END,
    GATEWAY_POLICY,
    NONCE,
    PROFILES,
    START,
    ScriptedModel,
    answer,
    verification_gateway,
    verification_manifest,
    verification_output,
    verification_prompt,
    verification_task,
)
from .investigation_helpers import (
    build_investigation,
    investigation_task,
)

WINDOW = TimeWindow(start=START, end=END)
INVESTIGATE = load_aql_rules(GATEWAY_POLICY, profile="qradar-investigate-read")
VERIFY = load_aql_rules(GATEWAY_POLICY, profile="qradar-verify-read")
BOUND = re.compile(r"^(?:\d+\. )?START (\d+) STOP (\d+)$", re.MULTILINE)


def test_a_window_is_one_exact_epoch_millisecond_part() -> None:
    parts = aql_window(WINDOW)

    assert parts.start_stop == "START 1790946000000 STOP 1790949600000"
    assert parts.span == timedelta(hours=1)


def test_fractional_milliseconds_are_covered_by_rounding_the_end_up() -> None:
    window = TimeWindow(
        start=START + timedelta(microseconds=999),
        end=END + timedelta(microseconds=1),
    )

    assert aql_window(window).start_stop == "START 1790946000000 STOP 1790949600001"


def test_the_examples_pass_their_profiles_guard() -> None:
    investigate = example_query(aql_window(WINDOW), limit=100)
    verify = example_query(aql_window(WINDOW), limit=EXAMPLE_LIMIT)

    assert INVESTIGATE.check(investigate).allowed, investigate
    assert VERIFY.check(verify).allowed, verify
    assert "starttime BETWEEN" not in investigate + verify
    assert "'2026-" not in investigate + verify


def test_the_verify_limits_are_the_policys() -> None:
    assert MAX_QUERY_WINDOW == VERIFY.aql.max_window
    assert EXAMPLE_LIMIT <= VERIFY.aql.max_limit


def test_the_investigation_prompt_gives_one_part_to_copy_unchanged() -> None:
    agent = build_investigation(ScriptedModel())

    text = agent.render_instructions(investigation_task(), nonce=NONCE, tool_budget=3)
    expected = "START 1790946000000 STOP 1790949600000"
    assert render_time_window(WINDOW, max_span=INVESTIGATE.aql.max_window) in text
    assert f"Ready AQL time bound (copy this exact complete part after LIMIT):\n{expected}" in text
    assert "Window in UTC, for reading only: 2026-10-02T13:00:00Z to 2026-10-02T14:00:00Z" in text
    assert f"LIMIT 100 {expected}" in text
    assert "copied exactly after LIMIT" in text
    assert "change the numbers" in text
    assert "starttime BETWEEN" not in text
    assert "Europe/Istanbul" not in text
    assert "QRadar console time zone" not in text


def test_the_verification_prompt_gives_a_part_that_passes_its_guard() -> None:
    agent = build_verification_agent(
        manifest=verification_manifest(),
        prompt=verification_prompt(),
        profiles=PROFILES,
        gateway=verification_gateway(),
        model=ScriptedModel(answer(verification_output())).model,
    )

    text = agent.render_instructions(verification_task(), nonce=NONCE, tool_budget=3)
    expected = "START 1790946000000 STOP 1790949600000"
    assert expected in text
    assert "copied exactly after" in text
    [example] = [
        line.split("Example: ", 1)[1] for line in text.splitlines() if line.startswith("Example: ")
    ]
    assert VERIFY.check(example).allowed, example
    assert "starttime BETWEEN" not in text
    assert "Europe/Istanbul" not in text


def test_a_long_window_is_split_into_consecutive_profile_sized_parts() -> None:
    window = TimeWindow(start=START, end=START + timedelta(hours=5))

    text = render_time_window(window, limit=EXAMPLE_LIMIT, max_span=MAX_QUERY_WINDOW)
    bounds = [(int(start), int(stop)) for start, stop in BOUND.findall(text)]

    assert bounds == [
        (1790946000000, 1790953200000),
        (1790953200000, 1790960400000),
        (1790960400000, 1790964000000),
    ]
    assert all(stop - start <= 2 * 60 * 60 * 1000 for start, stop in bounds)
    assert all(left[1] == right[0] for left, right in pairwise(bounds))
    [example] = [
        line.split("Example: ", 1)[1] for line in text.splitlines() if line.startswith("Example: ")
    ]
    assert VERIFY.check(example).allowed, example


def test_only_the_first_six_parts_of_a_long_window_are_shown() -> None:
    window = TimeWindow(start=START, end=START + timedelta(hours=13))

    text = render_time_window(window, max_span=MAX_QUERY_WINDOW)
    bounds = [(int(start), int(stop)) for start, stop in BOUND.findall(text)]

    assert len(bounds) == 6
    assert all(left[1] == right[0] for left, right in pairwise(bounds))
    assert "Only the first 6 consecutive parts are shown; the rest is omitted." in text
