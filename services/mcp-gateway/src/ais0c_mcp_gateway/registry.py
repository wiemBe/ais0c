"""The gateway registry: tools, profiles, quota pools and limits (architecture §8.2, §13.3).

Loaded once at startup from config/connectors/<connector>.yaml (what a profile may call) and
config/policies/<connector>.yaml (the rules its calls must pass). Tool descriptions and input
schemas come only from here: the gateway never reads tool metadata from an MCP server.

Loading fails on anything inconsistent, so a mistake in the files stops the gateway instead
of weakening a check: an Ariel tool without the AQL Guard or the ownership check, a write tool
in an agent profile, a profile missing from one of the two files, an invalid JSON Schema.

Agents get read tools only (AGENTS.md hard rule 2). A write tool names its `caller`, the one
platform component that may call it (today only the Action Executor), and a profile with a
caller belongs to that component alone: every tool of the profile names the same caller. An
agent profile, which has no caller, may not use a server profile with write tools, so no agent
call reaches an MCP instance whose QRadar token can write (architecture §11.2). Every
free-text argument of a write tool needs a text rule (text_rules.py).
"""

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from ais0c_contracts import CostClass, EvidenceSource
from ais0c_mcp_gateway.text_rules import TextRule
from ais0c_policy import AqlProfile, FieldFilter, IntentRules

ToolName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,62}$")]
ArgumentName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
PoolName = Literal["case", "hunt"]
POOL_NAMES: tuple[PoolName, ...] = ("case", "hunt")
Risk = Literal["read", "write"]
# Platform components, not agents, that may own a profile. Their calls run in pseudo agent runs
# under this agent ID (D-33).
Caller = Literal["action-executor"]
# JSON Schema keywords that pin a string's form; a string argument without one is free text.
_FIXED_FORM = frozenset({"const", "enum", "pattern"})


class RegistryError(ValueError):
    """The connector manifest or the gateway policy is invalid or inconsistent."""


class SearchStep(StrEnum):
    """A tool's step in the Ariel search lifecycle (architecture §11.1)."""

    CREATE = "create"
    STATUS = "status"
    RESULTS = "results"
    DELETE = "delete"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- connector manifest (config/connectors/<id>.yaml) --------------------------------------


class RowsResult(_Strict):
    rows: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class ToolEntry(_Strict):
    """One tool in the registry: what agents are told about it and how its result looks."""

    description: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    cost_class: CostClass
    result: Literal["object"] | RowsResult
    search: SearchStep | None = None
    page_size_argument: str | None = None
    input_schema: dict[str, JsonValue]

    @field_validator("input_schema")
    @classmethod
    def _strict_object_schema(cls, schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise ValueError(f"invalid JSON Schema: {error.message}") from None
        if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
            raise ValueError(
                "input_schema must be an object schema with additionalProperties: false"
            )
        if not isinstance(schema.get("properties"), dict):
            raise ValueError("input_schema needs properties")
        return schema

    @model_validator(mode="after")
    def _page_size_argument_exists(self) -> Self:
        properties = self.input_schema.get("properties")
        if self.page_size_argument is not None and (
            not isinstance(properties, dict) or self.page_size_argument not in properties
        ):
            raise ValueError(f"page_size_argument {self.page_size_argument!r} is not an input")
        return self

    @property
    def schema_version(self) -> str:
        """Hash of the input schema: agents send it, and it changes whenever the schema does."""
        canonical = json.dumps(self.input_schema, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    @property
    def rows_key(self) -> str | None:
        return None if self.result == "object" else self.result.rows

    @property
    def inputs(self) -> dict[str, dict[str, JsonValue]]:
        """The input schema's properties: argument name to its schema."""
        properties = self.input_schema.get("properties")
        if not isinstance(properties, dict):
            return {}
        return {name: spec for name, spec in properties.items() if isinstance(spec, dict)}


class ServerTool(_Strict):
    id: ToolName
    risk: Risk


class ServerProfile(_Strict):
    instance: Name
    tools: Annotated[tuple[ServerTool, ...], Field(min_length=1)]

    @property
    def has_write_tools(self) -> bool:
        return any(tool.risk == "write" for tool in self.tools)


class ProfileTool(_Strict):
    """A tool entry of a gateway profile (architecture §8.2)."""

    id: ToolName
    risk: Risk
    # The platform component that may call the tool. A write tool must name one: agents get
    # read tools only (AGENTS.md hard rule 2).
    caller: Caller | None = None
    guard: Literal["aql"] | None = None
    max_rows: Annotated[int, Field(ge=1)] | None = None
    only_own_searches: bool = False

    @model_validator(mode="after")
    def _write_needs_a_caller(self) -> Self:
        if self.risk == "write" and self.caller is None:
            raise ValueError("a risk: write tool needs a caller; agents get read tools only")
        return self


class GatewayProfileEntry(_Strict):
    server_profile: Name
    tools: Annotated[tuple[ProfileTool, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _one_caller(self) -> Self:
        if len({tool.caller for tool in self.tools}) > 1:
            raise ValueError(
                "every tool of a profile names the same caller, or none does: a profile "
                "belongs to agents or to one platform component"
            )
        return self

    @property
    def caller(self) -> Caller | None:
        return self.tools[0].caller


class HourRange(_Strict):
    """A daily local time range such as 20:00-07:00; it may run past midnight."""

    start: time
    end: time

    @model_validator(mode="before")
    @classmethod
    def _parse(cls, value: object) -> object:
        if isinstance(value, str):
            start, separator, end = value.partition("-")
            if not separator:
                raise ValueError("allowed_hours must look like 20:00-07:00")
            return {"start": start.strip(), "end": end.strip()}
        return value

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if self.start == self.end:
            raise ValueError("allowed_hours must not start and end at the same time")
        return self

    def contains(self, moment: time) -> bool:
        if self.start < self.end:
            return self.start <= moment < self.end
        return moment >= self.start or moment < self.end


class QuotaPoolConfig(_Strict):
    concurrent_searches: Annotated[int, Field(ge=1)]
    requests_per_minute: Annotated[int, Field(ge=1)]
    max_wait_seconds: Annotated[float, Field(ge=0)]
    search_ttl_seconds: Annotated[float, Field(gt=0)]
    allowed_hours: HourRange | None = None
    time_zone: str | None = None

    @model_validator(mode="after")
    def _time_zone(self) -> Self:
        if (self.allowed_hours is None) != (self.time_zone is None):
            raise ValueError("allowed_hours and time_zone go together")
        if self.time_zone is not None:
            try:
                ZoneInfo(self.time_zone)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError(f"unknown time zone {self.time_zone!r}") from None
        return self

    @property
    def zone(self) -> ZoneInfo | None:
        return None if self.time_zone is None else ZoneInfo(self.time_zone)


class Limits(_Strict):
    call_timeout_seconds: Annotated[float, Field(gt=0)]
    max_rows: Annotated[int, Field(ge=1)]
    max_result_bytes: Annotated[int, Field(ge=1024)]


class ConnectorManifest(_Strict):
    """config/connectors/<id>.yaml. The T-006 fields the gateway does not use are kept as-is."""

    id: Name
    server: str
    server_version: str
    upstream: dict[str, str]
    transport: Literal["streamable-http"]
    endpoint: str
    health_check: str
    capability_discovery: str
    supported_api_versions: tuple[str, ...]
    evidence_source: EvidenceSource
    server_profiles: dict[Name, ServerProfile]
    tools: dict[ToolName, ToolEntry]
    profiles: dict[Name, GatewayProfileEntry]
    quota_pools: dict[PoolName, QuotaPoolConfig]
    limits: Limits
    contract_tests: str

    @field_validator("server_profiles")
    @classmethod
    def _one_instance_each(cls, profiles: dict[str, ServerProfile]) -> dict[str, ServerProfile]:
        # An instance runs the fork with one --profile, with that profile's QRadar token.
        instances = [profile.instance for profile in profiles.values()]
        if len(set(instances)) != len(instances):
            raise ValueError("every server profile runs on its own instance")
        return profiles

    @field_validator("quota_pools")
    @classmethod
    def _every_pool(cls, pools: dict[PoolName, QuotaPoolConfig]) -> dict[PoolName, QuotaPoolConfig]:
        missing = set(POOL_NAMES) - pools.keys()
        if missing:
            raise ValueError(f"missing quota pools: {', '.join(sorted(missing))}")
        return pools


# --- gateway policy (config/policies/<id>.yaml) --------------------------------------------


class PolicyProfile(_Strict):
    max_time_window: Annotated[timedelta, Field(gt=timedelta(0))]
    aql: AqlProfile | None = None
    output_filter: str | None = None
    text_arguments: dict[ArgumentName, TextRule] = Field(
        default_factory=dict[ArgumentName, TextRule]
    )


class ConnectorPolicy(_Strict):
    connector: Name
    indexed_fields: tuple[str, ...]
    output_filters: dict[Name, FieldFilter] = Field(default_factory=dict[Name, FieldFilter])
    profiles: dict[Name, PolicyProfile]


# --- the resolved registry -----------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    """A tool as one profile may use it."""

    id: str
    entry: ToolEntry
    risk: Risk
    guard_aql: bool
    only_own_searches: bool
    max_rows: int

    @property
    def search(self) -> SearchStep | None:
        return self.entry.search


@dataclass(frozen=True)
class Connector:
    id: str
    evidence_source: EvidenceSource
    quota_pools: Mapping[PoolName, QuotaPoolConfig]
    limits: Limits
    indexed_fields: tuple[str, ...]


@dataclass(frozen=True)
class Profile:
    name: str
    connector: Connector
    # MCP instance the profile's calls go to (server_profiles.<name>.instance).
    instance: str
    tools: Mapping[str, Tool]
    intent_rules: IntentRules
    aql: AqlProfile | None
    output_filter: FieldFilter | None
    caller: Caller | None
    """The platform component the profile belongs to; None for an agent profile."""
    text_rules: Mapping[str, TextRule]
    """Limits of text arguments, by argument name, for every tool of the profile."""

    def tool_list(self) -> dict[str, JsonValue]:
        """The profile as its caller sees it; for an agent profile, packages/agents
        ToolsetProfile. A profile with write tools lists them as such, so an agent that was
        given its token by mistake cannot even load it."""
        tools: list[JsonValue] = [
            {
                "id": tool.id,
                "description": tool.entry.description,
                "schema_version": tool.entry.schema_version,
                "cost_class": tool.entry.cost_class.value,
                "risk": tool.risk,
                "parameters": tool.entry.input_schema,
            }
            for tool in self.tools.values()
        ]
        return {"name": self.name, "connector": self.connector.id, "tools": tools}


@dataclass(frozen=True)
class Registry:
    profiles: Mapping[str, Profile]
    connectors: Mapping[str, Connector]

    def instances(self) -> frozenset[str]:
        return frozenset(profile.instance for profile in self.profiles.values())


def load_registry(config_dir: Path, connector_ids: Iterable[str]) -> Registry:
    """Load and cross-check config/connectors/<id>.yaml and config/policies/<id>.yaml."""
    profiles: dict[str, Profile] = {}
    connectors: dict[str, Connector] = {}
    for connector_id in connector_ids:
        manifest = _load(config_dir / "connectors" / f"{connector_id}.yaml", ConnectorManifest)
        policy = _load(config_dir / "policies" / f"{connector_id}.yaml", ConnectorPolicy)
        try:
            connector, resolved = _resolve(manifest, policy, connector_id)
        except RegistryError as error:
            raise RegistryError(f"connector {connector_id!r}: {error}") from None
        clash = profiles.keys() & resolved.keys()
        if clash:
            raise RegistryError(f"profiles defined twice: {', '.join(sorted(clash))}")
        connectors[connector.id] = connector
        profiles.update(resolved)
    if not profiles:
        raise RegistryError("no gateway profiles")
    return Registry(profiles=profiles, connectors=connectors)


def _load[M: BaseModel](path: Path, model: type[M]) -> M:
    try:
        data: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        return model.model_validate(data)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        raise RegistryError(f"{path}: {error}") from None


def _resolve(
    manifest: ConnectorManifest, policy: ConnectorPolicy, connector_id: str
) -> tuple[Connector, dict[str, Profile]]:
    if manifest.id != connector_id or policy.connector != connector_id:
        raise RegistryError("the manifest id and the policy's connector must match the file name")
    if manifest.profiles.keys() != policy.profiles.keys():
        missing = manifest.profiles.keys() ^ policy.profiles.keys()
        raise RegistryError(f"profiles must be in both files: {', '.join(sorted(missing))}")
    connector = Connector(
        id=manifest.id,
        evidence_source=manifest.evidence_source,
        quota_pools=manifest.quota_pools,
        limits=manifest.limits,
        indexed_fields=policy.indexed_fields,
    )
    profiles = {
        name: _resolve_profile(name, entry, policy.profiles[name], manifest, policy, connector)
        for name, entry in manifest.profiles.items()
    }
    return connector, profiles


def _resolve_profile(
    name: str,
    entry: GatewayProfileEntry,
    rules: PolicyProfile,
    manifest: ConnectorManifest,
    policy: ConnectorPolicy,
    connector: Connector,
) -> Profile:
    server_profile = manifest.server_profiles.get(entry.server_profile)
    if server_profile is None:
        raise RegistryError(f"{name}: unknown server profile {entry.server_profile!r}")
    if entry.caller is None and server_profile.has_write_tools:
        raise RegistryError(
            f"{name} is an agent profile: it may not use {entry.server_profile}, "
            "which has write tools"
        )
    served = {tool.id: tool.risk for tool in server_profile.tools}
    tools: dict[str, Tool] = {}
    for profile_tool in entry.tools:
        tool_id = profile_tool.id
        where = f"{name}: tool {tool_id}"
        if tool_id in tools:
            raise RegistryError(f"{where} is listed twice")
        if served.get(tool_id) != profile_tool.risk:
            raise RegistryError(
                f"{where} is not a {profile_tool.risk} tool of {entry.server_profile}"
            )
        registry_entry = manifest.tools.get(tool_id)
        if registry_entry is None:
            raise RegistryError(f"{where} has no registry entry (description and schema)")
        _check_search_controls(where, profile_tool, registry_entry, rules)
        tools[tool_id] = Tool(
            id=tool_id,
            entry=registry_entry,
            risk=profile_tool.risk,
            guard_aql=profile_tool.guard == "aql",
            only_own_searches=profile_tool.only_own_searches,
            max_rows=profile_tool.max_rows or manifest.limits.max_rows,
        )
    _check_text_rules(name, tools, rules.text_arguments)

    output_filter = None
    if rules.output_filter is not None:
        output_filter = policy.output_filters.get(rules.output_filter)
        if output_filter is None:
            raise RegistryError(f"{name}: unknown output filter {rules.output_filter!r}")
    return Profile(
        name=name,
        connector=connector,
        instance=server_profile.instance,
        tools=tools,
        intent_rules=IntentRules(max_time_window=rules.max_time_window),
        aql=rules.aql,
        output_filter=output_filter,
        caller=entry.caller,
        text_rules=dict(rules.text_arguments),
    )


def _check_search_controls(
    where: str, profile_tool: ProfileTool, entry: ToolEntry, rules: PolicyProfile
) -> None:
    """Ariel tools must carry their controls; other tools must not claim them."""
    if entry.search is SearchStep.CREATE:
        if profile_tool.guard != "aql" or rules.aql is None:
            raise RegistryError(f"{where} starts Ariel searches: needs guard: aql and aql rules")
    elif profile_tool.guard is not None:
        raise RegistryError(f"{where} does not start Ariel searches; guard: aql does not apply")
    if entry.search in (SearchStep.STATUS, SearchStep.RESULTS, SearchStep.DELETE):
        if not profile_tool.only_own_searches:
            raise RegistryError(f"{where} reads Ariel searches: needs only_own_searches: true")
    elif profile_tool.only_own_searches:
        raise RegistryError(
            f"{where} does not read Ariel searches; only_own_searches does not apply"
        )


def _check_text_rules(
    name: str, tools: Mapping[str, Tool], text_rules: Mapping[str, TextRule]
) -> None:
    """A text rule limits a string argument of the profile's tools, below its schema's maximum;
    every free-text argument of a write tool has one."""
    for argument, rule in text_rules.items():
        specs = [
            spec
            for tool in tools.values()
            if (spec := tool.entry.inputs.get(argument)) is not None
            and spec.get("type") == "string"
        ]
        if not specs:
            raise RegistryError(
                f"{name}: text rule for {argument}, which no tool of the profile takes as a string"
            )
        for spec in specs:
            # maxLength counts code points and the rule UTF-16 code units. A text never has fewer
            # units than code points, so a rule at or below maxLength is the tighter limit.
            limit = spec.get("maxLength")
            if isinstance(limit, int) and rule.max_length > limit:
                raise RegistryError(
                    f"{name}: the text rule of {argument} allows more than its schema's "
                    f"maxLength ({limit})"
                )
    for tool in tools.values():
        if tool.risk != "write":
            continue
        for argument, spec in tool.entry.inputs.items():
            free_text = spec.get("type") == "string" and not spec.keys() & _FIXED_FORM
            if free_text and argument not in text_rules:
                raise RegistryError(
                    f"{name}: tool {tool.id} writes the free text {argument}: needs a text rule"
                )
