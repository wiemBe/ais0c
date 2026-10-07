"""Settings of offense intake and case evaluation, read from the environment.

| Variable | Default | Meaning |
|---|---|---|
| `AIS0C_CASE_URL_BASE` | none | Base of the platform's case page, e.g. `https://ais0c.example.com/cases`; the case's link on every QRadar note and alert e-mail is `<base>/<case_id>` (T-045) |
| `AIS0C_MAX_CONCURRENT_CASES` | 10 | Cases evaluating at the same time; the rest wait in the pending queue |
| `AIS0C_GROUP_FULL_ANALYSES_PER_HOUR` | 5 | N: offenses of one group that get a full analysis per hour; also the group's hourly limit of novelty escapes, on a counter of its own (T-62) |
| `AIS0C_GROUP_SETTLE_MINUTES` | 10 | How long a group's case waits after the group's storm began before it evaluates the group, so the whole burst is in its summary (T-62) |
| `AIS0C_SLA_HIGH_MINUTES` | 10 | Agent SLA for critical and high offenses |
| `AIS0C_SLA_LOW_MINUTES` | 60 | Agent SLA for medium, low and unrated offenses |
| `AIS0C_REEVALUATION_MINUTES` | 30 | An update that only brings more events is evaluated again once this long has passed since the last evaluation (D-31) |
| `AIS0C_AGENT_RETRY_MINUTES` | 5 | Wait before an agent run that the model's outage ended runs once more (D-33) |
| `AIS0C_PLAN_TOKENS` | 440000 | Plan budget: tokens of all the steps of one evaluation's plan together (T-41) |
| `AIS0C_PLAN_TOOL_CALLS` | 40 | Plan budget: tool calls of all the steps together |
| `AIS0C_PLAN_SECONDS` | 480 | Plan budget: wall-clock seconds of all the steps together |
| `AIS0C_QA_SAMPLE_PERCENT` | 10 | Low and medium FP decisions sampled for operator review (S-10) |
| `AIS0C_QA_UNDEFINED_SAMPLE_PERCENT` | 30 | The same when a rule of the offense is undefined in the Analysis Catalog or not in it (D-35) |

`AIS0C_CASE_URL_BASE` is the one setting without a default: without it the worker does not
start (T-045 criterion 6), because every evaluation ends in a note that carries the case's
link. The group limit, the settle time and the SLA defaults are the values of architecture §9
(T-62), the re-evaluation
interval the one of D-31 and the retry wait the one of task T-014; the retry wait is also the
re-evaluation interval of a case without an AI decision (T-30 (2)). §9 gives no number for the
concurrent case limit; 10 is this package's choice. The plan budget's defaults are those of
task T-044; T-030 measures them. The sample rates are those of decision T-42; 0 turns sampling
off.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Self

from ais0c_activities.levels import level_rank
from ais0c_contracts import Budget, Level

CASE_URL_BASE_ENV: Final = "AIS0C_CASE_URL_BASE"
MAX_CONCURRENT_CASES_ENV: Final = "AIS0C_MAX_CONCURRENT_CASES"
GROUP_FULL_ANALYSES_PER_HOUR_ENV: Final = "AIS0C_GROUP_FULL_ANALYSES_PER_HOUR"
GROUP_SETTLE_MINUTES_ENV: Final = "AIS0C_GROUP_SETTLE_MINUTES"
SLA_HIGH_MINUTES_ENV: Final = "AIS0C_SLA_HIGH_MINUTES"
SLA_LOW_MINUTES_ENV: Final = "AIS0C_SLA_LOW_MINUTES"
REEVALUATION_MINUTES_ENV: Final = "AIS0C_REEVALUATION_MINUTES"
AGENT_RETRY_MINUTES_ENV: Final = "AIS0C_AGENT_RETRY_MINUTES"
PLAN_TOKENS_ENV: Final = "AIS0C_PLAN_TOKENS"
PLAN_TOOL_CALLS_ENV: Final = "AIS0C_PLAN_TOOL_CALLS"
PLAN_SECONDS_ENV: Final = "AIS0C_PLAN_SECONDS"
QA_SAMPLE_PERCENT_ENV: Final = "AIS0C_QA_SAMPLE_PERCENT"
QA_UNDEFINED_SAMPLE_PERCENT_ENV: Final = "AIS0C_QA_UNDEFINED_SAMPLE_PERCENT"

# The base of a case link: scheme, host, optional port and path, no query or fragment, so
# `<base>/<case_id>` is a link the executor accepts (ais0c_executor.common.identity.CASE_URL).
_CASE_URL_BASE: Final = re.compile(
    r"https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~%/+-]*)?"
)
# The longest case ID is a group's, `group-G-<12 hex>-<UTC time>` (ais0c_activities.grouping),
# longer than `case-` and a 19-digit offense ID; the executor accepts a link of at most 200
# characters (ais0c_executor.common.identity.MAX_CASE_URL_LENGTH).
MAX_CASE_URL_BASE_LENGTH: Final = 200 - len("/group-G-" + "f" * 12 + "-20261007T090000Z")


@dataclass(frozen=True)
class CaseSettings:
    case_url_base: str
    """Base of the platform's case page; every note and e-mail links `<base>/<case_id>`
    (T-045 criterion 6). Without it the worker does not start."""

    max_concurrent_cases: int = 10
    group_full_analyses_per_hour: int = 5
    group_settle: timedelta = timedelta(minutes=10)
    sla_high: timedelta = timedelta(minutes=10)
    sla_low: timedelta = timedelta(minutes=60)
    reevaluation_interval: timedelta = timedelta(minutes=30)
    agent_retry_delay: timedelta = timedelta(minutes=5)
    plan_tokens: int = 440000
    plan_tool_calls: int = 40
    plan_seconds: int = 480
    qa_sample_percent: int = 10
    qa_undefined_sample_percent: int = 30

    def __post_init__(self) -> None:
        base = self.case_url_base
        if base.endswith("/") or not _CASE_URL_BASE.fullmatch(base):
            raise ValueError(
                "case_url_base must be an http(s) address without a trailing slash, such as "
                "https://ais0c.example.com/cases"
            )
        if len(base) > MAX_CASE_URL_BASE_LENGTH:
            raise ValueError(f"case_url_base must be at most {MAX_CASE_URL_BASE_LENGTH} characters")
        if self.max_concurrent_cases < 1:
            raise ValueError("max_concurrent_cases must be at least 1")
        if self.group_full_analyses_per_hour < 1:
            raise ValueError("group_full_analyses_per_hour must be at least 1")
        if self.group_settle <= timedelta(0):
            raise ValueError("group_settle must be positive")
        if self.sla_high <= timedelta(0) or self.sla_low <= timedelta(0):
            raise ValueError("SLA durations must be positive")
        if self.reevaluation_interval <= timedelta(0) or self.agent_retry_delay <= timedelta(0):
            raise ValueError(
                "the re-evaluation interval and the agent retry delay must be positive"
            )
        if min(self.plan_tokens, self.plan_tool_calls, self.plan_seconds) < 1:
            raise ValueError("the plan budget must be positive")
        for rate in (self.qa_sample_percent, self.qa_undefined_sample_percent):
            if not 0 <= rate <= 100:
                raise ValueError("QA sample rates are percentages from 0 to 100")

    @property
    def plan_budget(self) -> Budget:
        """What the steps of one evaluation's plan may use together (decision T-41)."""
        return Budget(
            tokens=self.plan_tokens, tool_calls=self.plan_tool_calls, seconds=self.plan_seconds
        )

    def case_url(self, case_id: str) -> str:
        """The platform page of `case_id`, the link every note and alert e-mail carries."""
        return f"{self.case_url_base}/{case_id}"

    def sla_for(self, level: Level | None) -> timedelta:
        """Critical and high get the short SLA; medium, low and no level the long one."""
        return self.sla_high if level_rank(level) >= level_rank(Level.HIGH) else self.sla_low

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        """Settings from `environ` (default `os.environ`); an unset variable keeps its default.

        `AIS0C_CASE_URL_BASE` is the exception: without it there is no case link to put on a
        note, so the settings do not load (T-045 criterion 6).
        """
        env = os.environ if environ is None else environ
        # Only the other settings have defaults; the URL here is never used as a value.
        defaults = cls(case_url_base="https://defaults.invalid")
        return cls(
            case_url_base=_required_url(env),
            max_concurrent_cases=_positive_int(
                env, MAX_CONCURRENT_CASES_ENV, defaults.max_concurrent_cases
            ),
            group_full_analyses_per_hour=_positive_int(
                env, GROUP_FULL_ANALYSES_PER_HOUR_ENV, defaults.group_full_analyses_per_hour
            ),
            group_settle=timedelta(
                minutes=_positive_int(
                    env, GROUP_SETTLE_MINUTES_ENV, _minutes(defaults.group_settle)
                )
            ),
            sla_high=timedelta(
                minutes=_positive_int(env, SLA_HIGH_MINUTES_ENV, _minutes(defaults.sla_high))
            ),
            sla_low=timedelta(
                minutes=_positive_int(env, SLA_LOW_MINUTES_ENV, _minutes(defaults.sla_low))
            ),
            reevaluation_interval=timedelta(
                minutes=_positive_int(
                    env, REEVALUATION_MINUTES_ENV, _minutes(defaults.reevaluation_interval)
                )
            ),
            agent_retry_delay=timedelta(
                minutes=_positive_int(
                    env, AGENT_RETRY_MINUTES_ENV, _minutes(defaults.agent_retry_delay)
                )
            ),
            plan_tokens=_positive_int(env, PLAN_TOKENS_ENV, defaults.plan_tokens),
            plan_tool_calls=_positive_int(env, PLAN_TOOL_CALLS_ENV, defaults.plan_tool_calls),
            plan_seconds=_positive_int(env, PLAN_SECONDS_ENV, defaults.plan_seconds),
            qa_sample_percent=_percent(env, QA_SAMPLE_PERCENT_ENV, defaults.qa_sample_percent),
            qa_undefined_sample_percent=_percent(
                env, QA_UNDEFINED_SAMPLE_PERCENT_ENV, defaults.qa_undefined_sample_percent
            ),
        )


def _minutes(duration: timedelta) -> int:
    return int(duration.total_seconds() // 60)


def _required_url(env: Mapping[str, str]) -> str:
    value = env.get(CASE_URL_BASE_ENV, "").strip()
    if not value:
        raise ValueError(f"{CASE_URL_BASE_ENV} is not set")
    return value


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a positive integer") from None
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _percent(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole percentage from 0 to 100") from None
    if not 0 <= value <= 100:
        raise ValueError(f"{name} must be a whole percentage from 0 to 100")
    return value
