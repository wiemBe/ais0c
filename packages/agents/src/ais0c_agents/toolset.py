"""Gateway tools: the only tools an agent has.

An agent sees exactly the tools of its manifest's toolset profile. Each call becomes a
ToolIntent for the gateway (architecture §13.2); the model supplies the tool's arguments, the
reason for the call and the evidence it expects, and the run supplies the rest. That includes
the run's own ID, under which the gateway records the call (T-19). The result reaches the model
only inside the `untrusted_*` wrapper, as JSON lines: a header with the status and coverage,
then one line per row.

The wrapper tag carries the call's evidence alias, `ev_<n>` for the run's n-th tool call, not
the gateway's evidence ID (decision T-27): models miscopy 32-character IDs. The model cites the
alias; the evidence ID stays in the tool return's metadata, which the model never sees, and
the output validator maps the alias back.

The tools form a FunctionToolset with an `id`, so TemporalDurability can run each call as an
activity (T-012). GatewayToolset wraps it and stays in workflow code, where the message
history is: it numbers each call and hands the alias to the tool as an argument. The gateway
client is bound when the agent is built; per-run values travel in RunDeps, which serializes.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
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
from pydantic_ai import ModelRetry, RunContext, UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturn,
    ToolReturnPart,
)
from pydantic_ai.tools import Tool
from pydantic_ai.toolsets import FunctionToolset, ToolsetTool, WrapperToolset

from ais0c_agents.gateway import GatewayClient, GatewayError
from ais0c_agents.prompts import NO_EVIDENCE_ID, wrap_json_lines
from ais0c_contracts import (
    SHORT_TEXT_MAX_LENGTH,
    CostClass,
    EvidenceId,
    RunId,
    ShortText,
    TimeWindow,
    ToolIntent,
    ToolResult,
    ToolStatus,
)
from ais0c_policy import CONNECTOR_SOURCES, MAX_SOURCE_LENGTH, is_known_source

ToolName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,62}$")]
NonBlankShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=SHORT_TEXT_MAX_LENGTH)
]

TOOLSET_ID_PREFIX: Final = "gateway-"
# The policy package's wrapper accepts the same nonces; checking early fails a run before any
# model request.
_NONCE = re.compile(r"^[0-9a-f]{8,64}$")
# The evidence IDs the policy package's wrapper accepts. The model sees only aliases, but a
# gateway evidence ID must still fit: claims carry it into storage, notes and e-mails.
_GATEWAY_EVIDENCE_ID = re.compile(r"^ev_[A-Za-z0-9_.:-]{1,128}$")

EVIDENCE_ALIAS_ARG: Final = "evidence_alias"
"""The argument GatewayToolset adds to every call: the call's evidence alias."""
_EVIDENCE_ALIAS = re.compile(r"^ev_[1-9][0-9]{0,5}$")
EvidenceAlias = Annotated[str, StringConstraints(pattern=_EVIDENCE_ALIAS.pattern)]


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
    """Connector id (config/connectors/); prefixes the `source` of every result block. Only
    the connectors the policy package's wrapper knows are accepted (qradar, falcon)."""
    tools: Annotated[tuple[ToolSpec, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _check_tools(self) -> Self:
        ids = [tool.id for tool in self.tools]
        if len(ids) != len(set(ids)):
            raise ValueError(f"profile {self.name!r} lists a tool twice")
        for tool_id in ids:
            # A tool result is named after its connector; kb.* names external knowledge.
            source = result_source(self, tool_id)
            if self.connector not in CONNECTOR_SOURCES or not is_known_source(source):
                raise ValueError(
                    f"{source} is not a source the wrapper accepts for tool results: "
                    f"{' or '.join(CONNECTOR_SOURCES)} tools, at most {MAX_SOURCE_LENGTH} "
                    "characters"
                )
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
    context_evidence: tuple[EvidenceId, ...] = ()
    """The gateway's evidence IDs of the evidence from earlier agents that the prompt shows
    (evidence.render_context_evidence), in its order: the model cites the n-th as `ev_c<n>`
    (decision T-38)."""
    reviewed_claim_texts: tuple[ShortText, ...] = ()
    """The texts of the claims the prompt shows (verification.py), in their order: an output
    validator checks that every disagreement names one of them exactly, so the workflow can
    tell which claim the agent contested. The default keeps runs that never set it valid."""


class GatewayCallRecord(BaseModel):
    """Kept in the tool return's metadata, which the model never sees."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str
    status: ToolStatus
    evidence_id: str | None
    """The gateway's evidence ID, when the model may cite the result."""
    evidence_alias: EvidenceAlias | None
    """What the model saw on the result's tag and cites instead; set exactly when
    `evidence_id` is."""

    @model_validator(mode="after")
    def _alias_with_evidence(self) -> Self:
        if (self.evidence_id is None) != (self.evidence_alias is None):
            raise ValueError("evidence_alias is set exactly when evidence_id is")
        return self


class _ToolCallArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: NonBlankShortText
    expected_evidence: NonBlankShortText
    arguments: dict[str, JsonValue]


@dataclass
class GatewayToolset(WrapperToolset[RunDeps]):
    """The gateway tools of one profile; numbers each call with its evidence alias (T-27).

    Under TemporalDurability only the wrapped FunctionToolset's calls become activities, so
    this wrapper runs in workflow code and sees the run's message history. Each call gets
    `ev_<n>` for the run's n-th tool call, under EVIDENCE_ALIAS_ARG, which overwrites an
    argument of that name from the model. History is the only state, so a replay after a
    worker restart numbers every call the same way.
    """

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[RunDeps],
        tool: ToolsetTool[RunDeps],
    ) -> object:
        alias = evidence_alias_of_call(ctx.messages, ctx.tool_call_id)
        return await super().call_tool(name, {**tool_args, EVIDENCE_ALIAS_ARG: alias}, ctx, tool)


def build_gateway_toolset(
    profile: ToolsetProfile, gateway: GatewayClient, *, agent_id: str
) -> GatewayToolset:
    tools = [_gateway_tool(spec, profile, gateway, agent_id) for spec in profile.tools]
    return GatewayToolset(FunctionToolset(tools, id=f"{TOOLSET_ID_PREFIX}{profile.name}"))


def evidence_alias_of_call(messages: Sequence[ModelMessage], tool_call_id: str | None) -> str:
    """The evidence alias of a tool call in the model's latest response.

    `ev_<n>` for the run's n-th tool call: every tool call of every response counts, in order,
    output tool calls included, so aliases stay unique whatever toolsets a run has. Raises
    UnexpectedModelBehavior when the call is not in the latest response exactly once, since
    two calls would then share an alias.
    """
    responses = [message for message in messages if isinstance(message, ModelResponse)]
    latest = (
        [part.tool_call_id for part in responses[-1].parts if isinstance(part, ToolCallPart)]
        if responses
        else []
    )
    if tool_call_id is None or latest.count(tool_call_id) != 1:
        raise UnexpectedModelBehavior(
            f"tool call {tool_call_id!r:.80} is not in the model's latest response exactly once"
        )
    earlier = sum(
        isinstance(part, ToolCallPart) for response in responses[:-1] for part in response.parts
    )
    return f"ev_{earlier + latest.index(tool_call_id) + 1}"


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


def render_tool_result(result: ToolResult, *, source: str, nonce: str, alias: str) -> str:
    """The wrapped text the model sees for a tool result of the call with evidence `alias`.

    The tag carries the alias only when the result has evidence to cite; every other block
    carries NO_EVIDENCE_ID. The gateway's evidence ID is never part of the text.
    """
    header: dict[str, JsonValue] = result.model_dump(mode="json", exclude={"data", "evidence_id"})
    header["rows"] = len(result.data)
    evidence = alias if citable_evidence_id(result) is not None else NO_EVIDENCE_ID
    return wrap_json_lines([header, *result.data], source=source, nonce=nonce, evidence_id=evidence)


def result_source(profile: ToolsetProfile, tool_id: str) -> str:
    """The `source` attribute of a result block, e.g. qradar.get_offense."""
    return f"{profile.connector}.{tool_id}"


def citable_evidence_id(result: ToolResult) -> str | None:
    """The evidence ID of an `ok` result, or None when the result has no evidence to cite.

    Raises GatewayError for an evidence ID the wrapper would not accept: the contract allows
    any non-space text after `ev_`, quotes and angle brackets included.
    """
    if result.status is not ToolStatus.OK or result.evidence_id in (None, NO_EVIDENCE_ID):
        return None
    if not _GATEWAY_EVIDENCE_ID.fullmatch(result.evidence_id):
        raise GatewayError(f"gateway returned an unusable evidence_id: {result.evidence_id!r:.80}")
    return result.evidence_id


def evidence_aliases(messages: Iterable[ModelMessage]) -> dict[str, str]:
    """The evidence a model may cite in a run: evidence alias -> the gateway's evidence ID.

    Read from the metadata of the run's gateway tool returns with an `ok` status. An alias
    bound to two different evidence IDs is left out; GatewayToolset never makes one.
    """
    found: dict[str, str] = {}
    clashing: set[str] = set()
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
            if (
                record.status is not ToolStatus.OK
                or record.evidence_id in (None, NO_EVIDENCE_ID)
                or record.evidence_alias is None
            ):
                continue
            if found.setdefault(record.evidence_alias, record.evidence_id) != record.evidence_id:
                clashing.add(record.evidence_alias)
    return {alias: evidence_id for alias, evidence_id in found.items() if alias not in clashing}


def _gateway_tool(
    spec: ToolSpec, profile: ToolsetProfile, gateway: GatewayClient, agent_id: str
) -> Tool[RunDeps]:
    source = result_source(profile, spec.id)

    async def call_gateway(ctx: RunContext[RunDeps], **raw_args: object) -> ToolReturn:
        alias = raw_args.pop(EVIDENCE_ALIAS_ARG, None)
        if not isinstance(alias, str) or not _EVIDENCE_ALIAS.fullmatch(alias):
            # Only GatewayToolset knows the run's history; without it no call is made.
            raise GatewayError(f"{spec.id} was called without an evidence alias")
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
        evidence_id = citable_evidence_id(result)
        record = GatewayCallRecord(
            tool_id=spec.id,
            status=result.status,
            evidence_id=evidence_id,
            evidence_alias=None if evidence_id is None else alias,
        )
        return ToolReturn(
            return_value=render_tool_result(
                result, source=source, nonce=ctx.deps.nonce, alias=alias
            ),
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
