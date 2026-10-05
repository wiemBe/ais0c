"""AQL an agent suggests to the operator (architecture §9, "Acil bakılması gereken event'ler";
decision T-39).

An urgent event's `aql` is a query the operator runs in QRadar by hand. It must pass the AQL
Guard under the rules of the `qradar-investigate-read` profile: that profile's `aql` rules and
the file's `indexed_fields` in config/policies/qradar.yaml, which the gateway also reads. They
bound the Investigation agent's own queries, the closest measure there is for a query an
operator should run.

load_aql_rules reads the rules when the agent is built, outside any workflow. SuggestedAqlCheck
is an output validator: it runs in workflow code and is pure.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_ai import ModelRetry

from ais0c_agents._walk import replace_models
from ais0c_agents._yaml import load_yaml
from ais0c_contracts import UrgentEvent
from ais0c_policy import AqlGuardResult, AqlProfile, AqlRejectReason, check_aql

SUGGESTED_AQL_PROFILE: Final = "qradar-investigate-read"
"""The gateway profile whose AQL rules a suggested query must pass (decision T-39)."""


class AqlRulesError(ValueError):
    """The gateway policy file is missing or invalid, or lacks the profile's AQL rules."""


# Only the parts of config/policies/<connector>.yaml this module reads; the gateway validates
# the whole file (ais0c_mcp_gateway.registry).
class _PolicyProfile(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    aql: AqlProfile | None = None


class _ConnectorPolicy(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    indexed_fields: tuple[str, ...]
    profiles: dict[str, _PolicyProfile]


@dataclass(frozen=True)
class AqlRules:
    """The AQL Guard rules of one gateway profile."""

    profile: str
    aql: AqlProfile
    indexed_fields: tuple[str, ...]

    def check(self, query: str) -> AqlGuardResult:
        return check_aql(query, self.aql, self.indexed_fields)

    def explain(self, reason: AqlRejectReason) -> str:
        """The reason code and what it means under these rules, for the model."""
        hint = _hints(self).get(reason)
        return f"{reason.value} ({hint})" if hint else reason.value


def load_aql_rules(path: Path, *, profile: str = SUGGESTED_AQL_PROFILE) -> AqlRules:
    """Read `profile`'s AQL rules and the indexed fields from the gateway policy at `path`
    (config/policies/qradar.yaml).

    Raises AqlRulesError when the file cannot be read or is invalid, or when it has no such
    profile or the profile has no `aql` rules.
    """
    try:
        data = load_yaml(path)
    except (OSError, yaml.YAMLError) as error:
        raise AqlRulesError(f"cannot read the gateway policy {path}: {error}") from error
    try:
        policy = _ConnectorPolicy.model_validate(data)
    except ValidationError as error:
        raise AqlRulesError(f"{path}: invalid gateway policy: {error}") from error
    entry = policy.profiles.get(profile)
    if entry is None:
        raise AqlRulesError(f"{path} has no profile {profile!r}")
    if entry.aql is None:
        raise AqlRulesError(f"{path}: profile {profile!r} has no aql rules")
    return AqlRules(profile=profile, aql=entry.aql, indexed_fields=policy.indexed_fields)


@dataclass(frozen=True)
class SuggestedAqlCheck:
    """Output validator: the `aql` of every urgent event in the output passes the AQL Guard.

    A rejected query sends the output back to the model with the Guard's reasons; the message
    never repeats the query. A blank `aql` counts as no query and becomes None.
    """

    rules: AqlRules

    def __call__[OutputT: BaseModel](self, output: OutputT) -> OutputT:
        rejected: list[str] = []

        def check(event: UrgentEvent) -> UrgentEvent:
            if event.aql is None:
                return event
            if not event.aql.strip():
                return event.model_copy(update={"aql": None})
            result = self.rules.check(event.aql)
            if not result.allowed:
                reasons = "; ".join(self.rules.explain(reason) for reason in result.reasons)
                rejected.append(f"urgent event {event.rank}: {reasons}")
            return event

        checked = replace_models(output, UrgentEvent, check)
        if rejected:
            raise ModelRetry(
                f"The AQL Guard rejected the aql of {'; '.join(rejected)}. "
                "Fix the query or leave aql empty."
            )
        return checked


def _hints(rules: AqlRules) -> dict[AqlRejectReason, str]:
    aql = rules.aql
    reason = AqlRejectReason
    return {
        reason.EMPTY_QUERY: "the query is empty",
        reason.UNEXPECTED_CHARACTER: "it has a character the guard cannot read",
        reason.UNTERMINATED_LITERAL: "a quoted value is not closed",
        reason.AMBIGUOUS_ESCAPE: "a backslash escapes a quote; double the quote instead",
        reason.COMMENT_NOT_ALLOWED: "comments are not allowed",
        reason.UNBALANCED_PARENTHESES: "the parentheses do not match",
        reason.NOT_SELECT: "the query must be a SELECT",
        reason.MULTIPLE_STATEMENTS: "the query must be a single statement",
        reason.NESTED_SELECT: "nested SELECTs are not allowed",
        reason.FROM_INVALID: "FROM must name exactly one table",
        reason.TABLE_NOT_ALLOWED: f"only these tables: {_listed(aql.allowed_tables)}",
        reason.MISSING_LIMIT: "the query needs a LIMIT",
        reason.LIMIT_INVALID: "LIMIT must be one positive integer",
        reason.LIMIT_EXCEEDS_PROFILE: f"LIMIT is at most {aql.max_limit}",
        reason.LIMIT_AFTER_TIME_BOUND: "LIMIT must come before LAST or START ... STOP",
        reason.MISSING_TIME_BOUND: "the query needs LAST or START ... STOP",
        reason.TIME_BOUND_INVALID: "LAST or START ... STOP cannot be read",
        reason.WINDOW_EXCEEDS_PROFILE: f"the window is at most {_duration(aql.max_window)}",
        reason.WIDE_WINDOW_UNINDEXED_FILTER: (
            f"a window over {_duration(aql.wide_window_threshold)} must filter on an indexed "
            f"field: {_listed(rules.indexed_fields)}"
        ),
    }


def _listed(names: Iterable[str]) -> str:
    return ", ".join(sorted(names))


def _duration(value: timedelta) -> str:
    """7 days, 24 hours, 30 minutes."""
    for unit, size in (("day", timedelta(days=1)), ("hour", timedelta(hours=1))):
        if value % size == timedelta(0):
            count = value // size
            return f"{count} {unit}{'s' if count != 1 else ''}"
    minutes = value / timedelta(minutes=1)
    return f"{minutes:g} minutes"
