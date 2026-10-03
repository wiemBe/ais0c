"""Gateway tools: the only tools an agent has.

An agent sees exactly the tools of its manifest's toolset profile. Each call becomes a
ToolIntent for the gateway (architecture §13.2); the model supplies the tool's arguments, the
reason for the call and the evidence it expects, and the run supplies the rest. That includes
the run's own ID, under which the gateway records the call (T-19). The result reaches the model
only inside the `untrusted_*` wrapper, as JSON lines: a header with the status and coverage,
then one line per row. The wrapper tag carries the evidence ID.

The tools form a FunctionToolset with an `id`, so TemporalDurability can run each call as an
activity (T-012). The gateway client is bound when the agent is built; per-run values travel
in RunDeps, which serializes.
"""

import re
from collections.abc import Iterable, Mapping
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    ValidationError,
    model_validator,
)
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturn, ToolReturnPart
from pydantic_ai.tools import Tool
from pydantic_ai.toolsets import FunctionToolset

from ais0c_agents.gateway import GatewayClient, GatewayError
from ais0c_agents.prompts import NO_EVIDENCE_ID, wrap_json_lines
from ais0c_contracts import (
    SHORT_TEXT_MAX_LENGTH,
    CostClass,
    RunId,
    TimeWindow,
    ToolIntent,
    ToolResult,
    ToolStatus,
)

ToolName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,62}$")]
NonBlankShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=SHORT_TEXT_MAX_LENGTH)
]

TOOLSET_ID_PREFIX: Final = "gateway-"
# Longest `source` attribute the policy package's wrapper accepts.
MAX_SOURCE_LENGTH: Final = 64
# The policy package's wrapper accepts the same nonces; checking early fails a run before any
# model request.
_NONCE = re.compile(r"^[0-9a-f]{8,64}$")


class ToolSpec(BaseModel):
    """One tool of a toolset profile, as the model sees it.

    The description comes from the platform's own registry, never from the upstream MCP server
    (§13.3). Agents get read tools only: writes happen in the executor (AGENTS.md hard rule 2).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ToolName
    description: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    schema_version: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    cost_class: CostClass
    risk: Literal["read"] = "read"
    parameters: dict[str, JsonValue]
    """JSON Schema of the tool's own arguments; the gateway validates them against it."""

    @model_validator(mode="after")
    def _object_schema(self) -> Self:
        if self.parameters.get("type") != "object":
            raise ValueError("parameters must be a JSON Schema of type object")
        return self


class ToolsetProfile(BaseModel):
    """A gateway toolset profile (§11.2), e.g. qradar-triage-read."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Name
    connector: Name
    """Connector id (config/connectors/); prefixes the `source` of every result block."""
    tools: Annotated[tuple[ToolSpec, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _check_tools(self) -> Self:
        ids = [tool.id for tool in self.tools]
        if len(ids) != len(set(ids)):
            raise ValueError(f"profile {self.name!r} lists a tool twice")
        for tool_id in ids:
            if len(result_source(self, tool_id)) > MAX_SOURCE_LENGTH:
                raise ValueError(f"{self.connector}.{tool_id} is longer than {MAX_SOURCE_LENGTH}")
        return self


class RunDeps(BaseModel):
    """Per-run values the gateway tools need; serializable for Temporal activities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: RunId
    """The agent run (`agent_runs.run_id`); every ToolIntent of the run carries it."""
    case_id: str | None
    hunt_id: str | None
    time_window: TimeWindow
    nonce: Annotated[str, StringConstraints(pattern=_NONCE.pattern)]
    """Suffix of this run's `untrusted_*` tags (prompts.md)."""


class GatewayCallRecord(BaseModel):
    """Kept in the tool return's metadata, which the model never sees."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str
    status: ToolStatus
    evidence_id: str | None


class _ToolCallArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: NonBlankShortText
    expected_evidence: NonBlankShortText
    arguments: dict[str, JsonValue]


def build_gateway_toolset(
    profile: ToolsetProfile, gateway: GatewayClient, *, agent_id: str
) -> FunctionToolset[RunDeps]:
    tools = [_gateway_tool(spec, profile, gateway, agent_id) for spec in profile.tools]
    return FunctionToolset(tools, id=f"{TOOLSET_ID_PREFIX}{profile.name}")


def tool_parameters_schema(spec: ToolSpec) -> dict[str, Any]:
    """The JSON Schema the model fills for one call: the ToolIntent fields it owns.

    The tool's own schema goes under `arguments`; its `$defs` move to the top level, where its
    `#/$defs/...` references point.
    """
    arguments = dict(spec.parameters)
    definitions = arguments.pop("$defs", None)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": SHORT_TEXT_MAX_LENGTH,
                "description": "Why this call is needed now.",
            },
            "expected_evidence": {
                "type": "string",
                "minLength": 1,
                "maxLength": SHORT_TEXT_MAX_LENGTH,
                "description": "The evidence you expect this call to return.",
            },
            "arguments": arguments,
        },
        "required": ["reason", "expected_evidence", "arguments"],
        "additionalProperties": False,
    }
    if definitions is not None:
        schema["$defs"] = definitions
    return schema


def render_tool_result(result: ToolResult, *, source: str, nonce: str) -> str:
    """The wrapped text the model sees for a tool result.

    Only an `ok` result carries its evidence ID; every other block carries NO_EVIDENCE_ID.
    """
    # The evidence ID is on the wrapper tag, and only when the model may cite it.
    header: dict[str, JsonValue] = result.model_dump(mode="json", exclude={"data", "evidence_id"})
    header["rows"] = len(result.data)
    evidence_id = citable_evidence_id(result) or NO_EVIDENCE_ID
    try:
        return wrap_json_lines(
            [header, *result.data], source=source, nonce=nonce, evidence_id=evidence_id
        )
    except ValueError as error:
        # The source and nonce are validated earlier; this is a malformed evidence ID.
        raise GatewayError(f"gateway returned an unusable evidence_id: {error}") from error


def result_source(profile: ToolsetProfile, tool_id: str) -> str:
    """The `source` attribute of a result block, e.g. qradar.get_offense."""
    return f"{profile.connector}.{tool_id}"


def citable_evidence_id(result: ToolResult) -> str | None:
    if result.status is ToolStatus.OK and result.evidence_id not in (None, NO_EVIDENCE_ID):
        return result.evidence_id
    return None


def returned_evidence_ids(messages: Iterable[ModelMessage]) -> frozenset[str]:
    """Evidence IDs that gateway tools returned with an `ok` status in these messages."""
    found: set[str] = set()
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, ToolReturnPart) or not isinstance(part.metadata, Mapping):
                continue
            try:
                record = GatewayCallRecord.model_validate(part.metadata)
            except ValidationError:
                continue
            if record.status is ToolStatus.OK and record.evidence_id not in (None, NO_EVIDENCE_ID):
                found.add(record.evidence_id)
    return frozenset(found)


def _gateway_tool(
    spec: ToolSpec, profile: ToolsetProfile, gateway: GatewayClient, agent_id: str
) -> Tool[RunDeps]:
    source = result_source(profile, spec.id)

    async def call_gateway(ctx: RunContext[RunDeps], **raw_args: object) -> ToolReturn:
        try:
            args = _ToolCallArgs.model_validate(raw_args)
        except ValidationError as error:
            raise ModelRetry(f"Invalid call to {spec.id}: {_describe(error)}") from error
        intent = ToolIntent(
            run_id=ctx.deps.run_id,
            case_id=ctx.deps.case_id,
            hunt_id=ctx.deps.hunt_id,
            agent_id=agent_id,
            toolset_profile=profile.name,
            tool_id=spec.id,
            tool_schema_version=spec.schema_version,
            arguments=args.arguments,
            reason=args.reason,
            expected_evidence=args.expected_evidence,
            time_window=ctx.deps.time_window,
            cost_class=spec.cost_class,
        )
        result = await gateway.call(intent)
        record = GatewayCallRecord(
            tool_id=spec.id, status=result.status, evidence_id=citable_evidence_id(result)
        )
        return ToolReturn(
            return_value=render_tool_result(result, source=source, nonce=ctx.deps.nonce),
            metadata=record.model_dump(mode="json"),
        )

    return Tool.from_schema(
        call_gateway,
        name=spec.id,
        description=spec.description,
        json_schema=tool_parameters_schema(spec),
        takes_ctx=True,
    )


def _describe(error: ValidationError) -> str:
    """Field errors without the rejected values: they may echo text from a tool result."""
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'arguments'}: {item['msg']}"
        for item in error.errors(include_url=False, include_input=False, include_context=False)
    )
