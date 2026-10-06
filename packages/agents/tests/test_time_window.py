"""T-048 criterion 4 (decision T-53): the window as AQL parts the model copies.

The prompt of Investigation and Verification gives the task's window as `starttime BETWEEN
<ms> AND <ms>` and as START/STOP in the QRadar console's time zone, widened by an hour on each
side; for Verification only as far as its profile's 2-hour AQL window allows. The expected
texts below are worked out by hand from the UTC window.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ais0c_agents import build_verification_agent, load_aql_rules
from ais0c_agents.investigation import (
    CONSOLE_ZONE,
    aql_window,
    example_query,
    render_time_window,
)
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
    instruction_text,
    investigation_manifest,
    investigation_output,
    investigation_prompt,
    investigation_task,
    run_investigation,
)

ISTANBUL = ZoneInfo("Europe/Istanbul")
NEW_YORK = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")
# The helpers' window: 2026-10-02 13:00 to 14:00 UTC.
WINDOW = TimeWindow(start=START, end=END)
# Like the lab offense: one minute, with seconds and milliseconds.
LAB_WINDOW = TimeWindow(
    start=datetime(2026, 10, 5, 12, 57, 43, 905000, tzinfo=UTC),
    end=datetime(2026, 10, 5, 12, 58, 43, 905000, tzinfo=UTC),
)
INVESTIGATE = load_aql_rules(GATEWAY_POLICY, profile="qradar-investigate-read")
VERIFY = load_aql_rules(GATEWAY_POLICY, profile="qradar-verify-read")


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# --- the two parts ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("zone", "start_stop"),
    [
        (ISTANBUL, "START '2026-10-02 15:00' STOP '2026-10-02 18:00'"),
        (NEW_YORK, "START '2026-10-02 08:00' STOP '2026-10-02 11:00'"),
    ],
    ids=["Europe/Istanbul", "America/New_York"],
)
def test_the_parts_of_a_utc_window(zone: ZoneInfo, start_stop: str) -> None:
    parts = aql_window(WINDOW, zone)

    assert parts.starttime == "starttime BETWEEN 1790946000000 AND 1790949600000"
    assert parts.start_stop == start_stop
    assert parts.span == timedelta(hours=3)


def test_start_is_rounded_down_and_stop_up_to_the_minute() -> None:
    parts = aql_window(LAB_WINDOW, ISTANBUL)

    assert parts.starttime == "starttime BETWEEN 1791205063905 AND 1791205123905"
    assert parts.start_stop == "START '2026-10-05 14:57' STOP '2026-10-05 16:59'"


def test_the_end_rounds_up_to_the_millisecond() -> None:
    window = TimeWindow(start=START, end=END + timedelta(microseconds=1))

    assert aql_window(window, ISTANBUL).starttime == (
        "starttime BETWEEN 1790946000000 AND 1790949600001"
    )


@pytest.mark.parametrize(
    ("window", "start_stop", "span"),
    [
        # 2026-03-29 01:00 UTC: Berlin goes from 02:00 CET to 03:00 CEST.
        (
            TimeWindow(start=utc(2026, 3, 29, 0, 30), end=utc(2026, 3, 29, 1, 30)),
            "START '2026-03-29 00:30' STOP '2026-03-29 04:30'",
            timedelta(hours=4),
        ),
        # 2026-10-25 01:00 UTC: Berlin goes from 03:00 CEST back to 02:00 CET.
        (
            TimeWindow(start=utc(2026, 10, 25, 0, 30), end=utc(2026, 10, 25, 1, 30)),
            "START '2026-10-25 01:30' STOP '2026-10-25 03:30'",
            timedelta(hours=2),
        ),
    ],
    ids=["spring forward", "fall back"],
)
def test_a_window_across_a_clock_change(
    window: TimeWindow, start_stop: str, span: timedelta
) -> None:
    parts = aql_window(window, BERLIN)

    assert parts.start_stop == start_stop
    # The span is what the AQL Guard measures: the difference of the console times as written.
    assert parts.span == span
    assert INVESTIGATE.check(example_query(parts, limit=100)).allowed


def test_the_margin_shrinks_to_the_profiles_window() -> None:
    assert aql_window(WINDOW, ISTANBUL, max_span=timedelta(hours=2)).start_stop == (
        "START '2026-10-02 15:30' STOP '2026-10-02 17:30'"
    )
    lab = aql_window(LAB_WINDOW, ISTANBUL, max_span=timedelta(hours=2))
    assert lab.start_stop == "START '2026-10-05 14:58' STOP '2026-10-05 16:58'"
    assert lab.span == timedelta(hours=2)
    # The exact bound never shrinks.
    assert lab.starttime == aql_window(LAB_WINDOW, ISTANBUL).starttime


def test_a_window_longer_than_the_profiles_keeps_no_margin() -> None:
    window = TimeWindow(start=START, end=START + timedelta(hours=3))

    parts = aql_window(window, ISTANBUL, max_span=timedelta(hours=2))

    assert parts.start_stop == "START '2026-10-02 16:00' STOP '2026-10-02 19:00'"
    assert parts.span == timedelta(hours=3)


@pytest.mark.parametrize("window", [WINDOW, LAB_WINDOW], ids=["one hour", "one minute"])
@pytest.mark.parametrize("zone", [ISTANBUL, NEW_YORK], ids=["Europe/Istanbul", "America/New_York"])
def test_the_examples_pass_their_profiles_guard_without_double_quotes(
    window: TimeWindow, zone: ZoneInfo
) -> None:
    investigate = example_query(aql_window(window, zone), limit=100)
    verify = example_query(aql_window(window, zone, max_span=MAX_QUERY_WINDOW), limit=EXAMPLE_LIMIT)

    assert INVESTIGATE.check(investigate).allowed, investigate
    assert VERIFY.check(verify).allowed, verify
    assert '"' not in investigate + verify
    # The widened START/STOP is too wide for the verify profile: that is why it shrinks.
    assert not VERIFY.check(example_query(aql_window(window, zone), limit=EXAMPLE_LIMIT)).allowed


def test_the_verify_limits_are_the_policys() -> None:
    assert MAX_QUERY_WINDOW == VERIFY.aql.max_window
    assert EXAMPLE_LIMIT <= VERIFY.aql.max_limit


def test_the_console_zone_defaults_to_istanbul() -> None:
    assert CONSOLE_ZONE == ISTANBUL == ZoneInfo("Europe/Istanbul")
    assert build_investigation(ScriptedModel()).console_zone == ISTANBUL
    verification = build_verification_agent(
        manifest=verification_manifest(),
        prompt=verification_prompt(),
        profiles=PROFILES,
        gateway=verification_gateway(),
        model=ScriptedModel().model,
    )
    assert verification.console_zone == ISTANBUL


# --- the prompts --------------------------------------------------------------------------------


def test_the_investigation_prompt_gives_the_parts_and_says_to_copy_them() -> None:
    script = ScriptedModel(answer(investigation_output("ev_c1", ranks=(1,))))

    run_investigation(build_investigation(script))

    text = instruction_text(script)
    window = investigation_task().task.time_window
    parts = aql_window(window, ISTANBUL)
    assert render_time_window(window, ISTANBUL) in text
    assert f"WHERE part: {parts.starttime}" in text
    assert f"START/STOP part: {parts.start_stop}" in text
    assert "QRadar console time zone: Europe/Istanbul" in text
    assert "copied exactly as they are" in text
    assert "Never convert, recompute or narrow a time in them" in text
    [example] = [
        line.split("Example: ", 1)[1] for line in text.splitlines() if line.startswith("Example: ")
    ]
    assert example == example_query(parts, limit=100)
    assert INVESTIGATE.check(example).allowed
    assert '"' not in example
    # The day-wide margin of T-023 is gone.
    assert "one day before" not in text


def test_the_investigation_prompt_uses_the_given_console_zone() -> None:
    script = ScriptedModel(answer(investigation_output("ev_c1", ranks=(1,))))
    agent = build_investigation(script)
    agent = type(agent)(
        manifest=investigation_manifest(),
        prompt=investigation_prompt(),
        profile=agent.profile,
        aql_rules=agent.aql_rules,
        agent=agent.agent,
        console_zone=NEW_YORK,
    )

    text = agent.render_instructions(investigation_task(), nonce=NONCE, tool_budget=3)

    assert "START/STOP part: START '2026-10-02 08:00' STOP '2026-10-02 11:00'" in text
    assert "QRadar console time zone: America/New_York" in text


def test_the_verification_prompt_gives_parts_that_fit_its_profile() -> None:
    agent = build_verification_agent(
        manifest=verification_manifest(),
        prompt=verification_prompt(),
        profiles=PROFILES,
        gateway=verification_gateway(),
        model=ScriptedModel(answer(verification_output())).model,
        console_zone=NEW_YORK,
    )

    text = agent.render_instructions(verification_task(), nonce=NONCE, tool_budget=3)

    assert "WHERE part: starttime BETWEEN 1790946000000 AND 1790949600000" in text
    assert "START/STOP part: START '2026-10-02 08:30' STOP '2026-10-02 10:30'" in text
    assert "QRadar console time zone: America/New_York" in text
    assert "copied exactly as they are" in text
    assert "Never convert or recompute" in text
    [example] = [
        line.split("Example: ", 1)[1] for line in text.splitlines() if line.startswith("Example: ")
    ]
    assert VERIFY.check(example).allowed, example
    assert '"' not in example


def test_a_verification_window_longer_than_two_hours_asks_for_a_part() -> None:
    window = TimeWindow(start=START, end=START + timedelta(hours=3))

    text = render_time_window(window, ISTANBUL, limit=EXAMPLE_LIMIT, max_span=MAX_QUERY_WINDOW)

    assert "START/STOP part: START '2026-10-02 16:00' STOP '2026-10-02 19:00'" in text
    assert "more than 2 hours apart" in text
    assert "do not convert them" in text
    assert "Example:" not in text
