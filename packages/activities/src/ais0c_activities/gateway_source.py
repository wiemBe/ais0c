"""Offenses from QRadar through the MCP Policy Gateway (architecture §6, §13; T-012).

`GatewayOffenseSource` is the `OffenseSource` of a running platform. It reads with the tools of
one gateway profile, the Triage agent's `qradar-triage-read`: `list_offenses` finds changed,
single and closed offenses, and `list_rules`, `list_source_addresses`,
`list_local_destination_addresses` and `list_offense_types` fill in the snapshot's rule names,
addresses and offense type. Lookups are batched per page of offenses.

Reading offenses is the platform's own work, not an agent's, but the gateway records every call
under an agent run. Each read is therefore a system run of the pseudo agent `offense-source`
(`ais0c_activities.gateway.system_run`): the intake's reads belong to the context
`offense-intake`, the read of one offense to its case ID. The gateway records their calls and
evidence like any other.

QRadar's values are untrusted. The snapshot keeps them as they are, because they reach a prompt
only inside the `untrusted_*` wrapper, but within the contract's limits: the description is cut
to 500 characters and the address lists to 50 entries. QRadar does not list an offense's users;
the user list holds the offense source when the offense is indexed on a user name.
"""

from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from ais0c_activities.db import SessionFactory
from ais0c_activities.gateway import SystemRun, system_run, utc_now
from ais0c_activities.names import case_workflow_id
from ais0c_agents import GatewayClient, ToolsetProfile
from ais0c_contracts import Budget, OffenseSnapshot, TimeWindow

SOURCE_AGENT_ID: Final = "offense-source"
INTAKE_CONTEXT: Final = "offense-intake"
# The time window a read declares: at most this long (the Triage profile allows 31 days).
SOURCE_WINDOW: Final = timedelta(days=30)
MIN_WINDOW: Final = timedelta(minutes=1)
# Declared, not enforced: a page of offenses takes one list call and a few lookups.
SOURCE_BUDGET: Final = Budget(tokens=0, tool_calls=20, seconds=300)
# IDs per lookup call; below the gateway's default row cap of 200.
LOOKUP_CHUNK: Final = 100
OFFENSE_FIELDS: Final = ",".join(
    (
        "id",
        "description",
        "offense_type",
        "offense_source",
        "rules(id)",
        "categories",
        "magnitude",
        "start_time",
        "last_updated_time",
        "event_count",
        "log_sources(id)",
        "source_address_ids",
        "local_destination_address_ids",
    )
)
USERNAME_OFFENSE_TYPE: Final = "Username"
MAX_DESCRIPTION_LENGTH: Final = 500
MAX_ADDRESSES: Final = 50

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MILLISECOND: Final = timedelta(milliseconds=1)


class OffenseSourceError(RuntimeError):
    """QRadar returned an offense the source cannot read."""


class _Ref(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int


class _OffenseRow(BaseModel):
    """The fields of OFFENSE_FIELDS; QRadar may send null for any but the ID and times."""

    model_config = ConfigDict(extra="ignore")

    id: int
    description: str | None = None
    offense_type: int | None = None
    offense_source: str | None = None
    rules: list[_Ref] | None = None
    categories: list[str] | None = None
    magnitude: int | None = None
    start_time: int
    last_updated_time: int
    event_count: int | None = None
    log_sources: list[_Ref] | None = None
    source_address_ids: list[int] | None = None
    local_destination_address_ids: list[int] | None = None


@dataclass(frozen=True)
class _Lookups:
    offense_types: dict[int, str]
    rule_names: dict[int, str]
    source_ips: dict[int, str]
    destination_ips: dict[int, str]


class GatewayOffenseSource:
    """Implements `OffenseSource` with the gateway profile `profile` and its token's client."""

    def __init__(
        self,
        *,
        gateway: GatewayClient,
        profile: ToolsetProfile,
        sessions: SessionFactory,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._gateway = gateway
        self._profile = profile
        self._sessions = sessions
        self._clock = clock
        # Offense types do not change while QRadar runs; read them once per process.
        self._offense_types: dict[int, str] | None = None

    async def changed_offenses(
        self, *, after_time: datetime, after_id: int, limit: int
    ) -> list[OffenseSnapshot]:
        now = self._clock()
        after = _epoch_ms(after_time)
        window = TimeWindow(
            start=max(min(after_time, now - MIN_WINDOW), now - SOURCE_WINDOW), end=now
        )
        objective = "Read the open QRadar offenses changed since the intake checkpoint."
        async with self._run(INTAKE_CONTEXT, objective, window) as run:
            result = await run.call(
                "list_offenses",
                {
                    "filter": (
                        f'status = "OPEN" and (last_updated_time > {after} or '
                        f"(last_updated_time = {after} and id > {after_id}))"
                    ),
                    "sort": "+last_updated_time,+id",
                    "fields": OFFENSE_FIELDS,
                    "limit": limit,
                },
                reason="Poll for offenses that are new or changed since the last intake run.",
                expected_evidence="Open offenses ordered by last update, with rule and address IDs.",
            )
            return await self._snapshots(run, result.data)

    async def get_offense(self, offense_id: int) -> OffenseSnapshot | None:
        now = self._clock()
        window = TimeWindow(start=now - SOURCE_WINDOW, end=now)
        objective = f"Read QRadar offense {offense_id} for its case."
        async with self._run(case_workflow_id(offense_id), objective, window) as run:
            result = await run.call(
                "list_offenses",
                {"filter": f"id = {offense_id}", "fields": OFFENSE_FIELDS, "limit": 1},
                reason=f"Read offense {offense_id} as QRadar holds it now, before evaluating it.",
                expected_evidence="The offense with its rules, log sources and addresses.",
            )
            snapshots = await self._snapshots(run, result.data)
        return next((item for item in snapshots if item.offense_id == offense_id), None)

    async def closed_offenses(self, offense_ids: Collection[int]) -> list[int]:
        wanted = sorted(set(offense_ids))
        if not wanted:
            return []
        now = self._clock()
        window = TimeWindow(start=now - SOURCE_WINDOW, end=now)
        objective = "Find which offenses with an open case were closed in QRadar."
        closed: set[int] = set()
        async with self._run(INTAKE_CONTEXT, objective, window) as run:
            for chunk in _chunks(wanted, LOOKUP_CHUNK):
                result = await run.call(
                    "list_offenses",
                    {
                        "filter": f'status = "CLOSED" and id in ({_id_list(chunk)})',
                        "fields": "id",
                        "limit": len(chunk),
                    },
                    reason="Check whether offenses whose cases are open have been closed.",
                    expected_evidence="The IDs among the given offenses that are closed.",
                )
                closed.update(_ints(row.get("id") for row in result.data))
        return sorted(closed.intersection(wanted))

    def _run(
        self, context: str, objective: str, window: TimeWindow
    ) -> AbstractAsyncContextManager[SystemRun]:
        return system_run(
            sessions=self._sessions,
            gateway=self._gateway,
            profile=self._profile,
            agent_id=SOURCE_AGENT_ID,
            case_id=context,
            objective=objective,
            window=window,
            budget=SOURCE_BUDGET,
            clock=self._clock,
        )

    async def _snapshots(
        self, run: SystemRun, rows: Sequence[dict[str, JsonValue]]
    ) -> list[OffenseSnapshot]:
        offenses = [_parse(row) for row in rows]
        if not offenses:
            return []
        lookups = _Lookups(
            offense_types=await self._types(run),
            rule_names=await _lookup(
                run, "list_rules", "name", (ref.id for item in offenses for ref in item.rules or [])
            ),
            source_ips=await _lookup(
                run,
                "list_source_addresses",
                "source_ip",
                (i for item in offenses for i in item.source_address_ids or []),
            ),
            destination_ips=await _lookup(
                run,
                "list_local_destination_addresses",
                "local_destination_ip",
                (i for item in offenses for i in item.local_destination_address_ids or []),
            ),
        )
        return [_snapshot(item, lookups) for item in offenses]

    async def _types(self, run: SystemRun) -> dict[int, str]:
        if self._offense_types is None:
            result = await run.call(
                "list_offense_types",
                {"fields": "id,name", "limit": 100},
                reason="Name the offense types, e.g. whether an offense is indexed on a user.",
                expected_evidence="Offense type IDs with their names.",
            )
            self._offense_types = _named(result.data, "name")
        return self._offense_types


async def _lookup(
    run: SystemRun, tool_id: str, value_field: str, ids: Iterable[int]
) -> dict[int, str]:
    found: dict[int, str] = {}
    for chunk in _chunks(sorted(set(ids)), LOOKUP_CHUNK):
        result = await run.call(
            tool_id,
            {
                "filter": f"id in ({_id_list(chunk)})",
                "fields": f"id,{value_field}",
                "limit": len(chunk),
            },
            reason=f"Resolve the offenses' {value_field.replace('_', ' ')} values by ID.",
            expected_evidence=f"The {value_field.replace('_', ' ')} of each requested ID.",
        )
        found.update(_named(result.data, value_field))
    return found


def _parse(row: dict[str, JsonValue]) -> _OffenseRow:
    try:
        return _OffenseRow.model_validate(row)
    except ValidationError as error:
        # Field locations only: the rejected values are untrusted QRadar data.
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise OffenseSourceError(
            f"QRadar returned an offense the source cannot read; check {', '.join(fields)}"
        ) from None


def _snapshot(row: _OffenseRow, lookups: _Lookups) -> OffenseSnapshot:
    offense_type = (
        "unknown"
        if row.offense_type is None
        else lookups.offense_types.get(row.offense_type, str(row.offense_type))
    )
    source = row.offense_source or ""
    rule_ids = [ref.id for ref in row.rules or []]
    return OffenseSnapshot(
        offense_id=row.id,
        description=(row.description or "").strip()[:MAX_DESCRIPTION_LENGTH],
        offense_type=offense_type,
        offense_source=source,
        rule_ids=rule_ids,
        rule_names=[lookups.rule_names[i] for i in rule_ids if i in lookups.rule_names],
        categories=list(row.categories or []),
        magnitude=row.magnitude or 0,
        start_time=_from_ms(row.start_time),
        last_updated_time=_from_ms(row.last_updated_time),
        event_count=row.event_count or 0,
        log_source_ids=[ref.id for ref in row.log_sources or []],
        source_ips=_resolve(row.source_address_ids, lookups.source_ips),
        destination_ips=_resolve(row.local_destination_address_ids, lookups.destination_ips),
        usernames=[source] if offense_type == USERNAME_OFFENSE_TYPE and source else [],
    )


def _resolve(ids: list[int] | None, values: dict[int, str]) -> list[str]:
    return [values[i] for i in ids or [] if i in values][:MAX_ADDRESSES]


def _named(rows: Iterable[dict[str, JsonValue]], value_field: str) -> dict[int, str]:
    named: dict[int, str] = {}
    for row in rows:
        key, value = row.get("id"), row.get(value_field)
        if isinstance(key, int) and not isinstance(key, bool) and isinstance(value, str):
            named[key] = value
    return named


def _ints(values: Iterable[JsonValue]) -> Iterator[int]:
    return (value for value in values if isinstance(value, int) and not isinstance(value, bool))


def _chunks(values: Sequence[int], size: int) -> Iterator[Sequence[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _id_list(ids: Iterable[int]) -> str:
    return ",".join(str(int(i)) for i in ids)


def _epoch_ms(value: datetime) -> int:
    """QRadar's time format: milliseconds since the epoch, rounded down."""
    return (value - _EPOCH) // _MILLISECOND


def _from_ms(value: int) -> datetime:
    return _EPOCH + value * _MILLISECOND
