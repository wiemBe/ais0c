"""Settings of offense intake and case evaluation, read from the environment.

| Variable | Default | Meaning |
|---|---|---|
| `AIS0C_MAX_CONCURRENT_CASES` | 10 | Cases evaluating at the same time; the rest wait in the pending queue |
| `AIS0C_GROUP_FULL_ANALYSES_PER_HOUR` | 5 | N: offenses of one group that get a full analysis per hour |
| `AIS0C_SLA_HIGH_MINUTES` | 10 | Agent SLA for critical and high offenses |
| `AIS0C_SLA_LOW_MINUTES` | 60 | Agent SLA for medium, low and unrated offenses |
| `AIS0C_REEVALUATION_MINUTES` | 30 | An update that only brings more events is evaluated again once this long has passed since the last evaluation (D-31) |
| `AIS0C_TRIAGE_RETRY_MINUTES` | 5 | Wait before a Triage run that the model's outage ended runs once more (D-33) |

The group limit and the SLA defaults are the values of architecture §9, the re-evaluation
interval the one of D-31 and the retry wait the one of task T-014. §9 gives no number for the
concurrent case limit; 10 is this package's choice.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Self

from ais0c_activities.levels import level_rank
from ais0c_contracts import Level

MAX_CONCURRENT_CASES_ENV: Final = "AIS0C_MAX_CONCURRENT_CASES"
GROUP_FULL_ANALYSES_PER_HOUR_ENV: Final = "AIS0C_GROUP_FULL_ANALYSES_PER_HOUR"
SLA_HIGH_MINUTES_ENV: Final = "AIS0C_SLA_HIGH_MINUTES"
SLA_LOW_MINUTES_ENV: Final = "AIS0C_SLA_LOW_MINUTES"
REEVALUATION_MINUTES_ENV: Final = "AIS0C_REEVALUATION_MINUTES"
TRIAGE_RETRY_MINUTES_ENV: Final = "AIS0C_TRIAGE_RETRY_MINUTES"


@dataclass(frozen=True)
class CaseSettings:
    max_concurrent_cases: int = 10
    group_full_analyses_per_hour: int = 5
    sla_high: timedelta = timedelta(minutes=10)
    sla_low: timedelta = timedelta(minutes=60)
    reevaluation_interval: timedelta = timedelta(minutes=30)
    triage_retry_delay: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if self.max_concurrent_cases < 1:
            raise ValueError("max_concurrent_cases must be at least 1")
        if self.group_full_analyses_per_hour < 1:
            raise ValueError("group_full_analyses_per_hour must be at least 1")
        if self.sla_high <= timedelta(0) or self.sla_low <= timedelta(0):
            raise ValueError("SLA durations must be positive")
        if self.reevaluation_interval <= timedelta(0) or self.triage_retry_delay <= timedelta(0):
            raise ValueError(
                "the re-evaluation interval and the triage retry delay must be positive"
            )

    def sla_for(self, level: Level | None) -> timedelta:
        """Critical and high get the short SLA; medium, low and no level the long one."""
        return self.sla_high if level_rank(level) >= level_rank(Level.HIGH) else self.sla_low

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        """Settings from `environ` (default `os.environ`); an unset variable keeps its default."""
        env = os.environ if environ is None else environ
        defaults = cls()
        return cls(
            max_concurrent_cases=_positive_int(
                env, MAX_CONCURRENT_CASES_ENV, defaults.max_concurrent_cases
            ),
            group_full_analyses_per_hour=_positive_int(
                env, GROUP_FULL_ANALYSES_PER_HOUR_ENV, defaults.group_full_analyses_per_hour
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
            triage_retry_delay=timedelta(
                minutes=_positive_int(
                    env, TRIAGE_RETRY_MINUTES_ENV, _minutes(defaults.triage_retry_delay)
                )
            ),
        )


def _minutes(duration: timedelta) -> int:
    return int(duration.total_seconds() // 60)


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
