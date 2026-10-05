"""Evidence in prompts and outputs: context evidence and the common evidence check.

A model never sees the gateway's evidence IDs (decision T-27): it cites aliases.

- A tool result's evidence is `ev_<n>`, for the run's n-th tool call (toolset.py).
- Evidence from earlier agents (EvidenceRef) is `ev_c<n>`, for the n-th item of the run's
  context evidence (decision T-38). render_context_evidence puts each item in its own
  `untrusted_*` block under that alias. RunDeps.context_evidence keeps the gateway's IDs in the
  same order; the model never sees RunDeps.

check_evidence is the output validator every agent registers. It applies one rule to every
evidence field of the output, at any depth: each value must be an alias of this run, and the
validated output carries the gateway's evidence ID in its place. An evidence field is one typed
`EvidenceId` (ais0c_contracts), alone, optional, or in a list or tuple: `Claim.evidence_ids`,
`TimelineEntry.evidence_ids`, `UrgentEvent.evidence_id`, `Recommendation.evidence_ids`,
`VerificationResult.checked_evidence_ids` and any such field added later. A field that holds an
`EvidenceId` in another shape stops the agent's build (check_evidence_fields).
"""

import re
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from functools import cache
from typing import Annotated, Final, Union, cast, get_args, get_origin

from pydantic import BaseModel, StringConstraints
from pydantic_ai import ModelRetry, RunContext

from ais0c_agents.prompts import wrap_json_lines
from ais0c_agents.toolset import RunDeps, evidence_aliases
from ais0c_contracts import EvidenceId, EvidenceRef
from ais0c_policy import neutralize_tags

CONTEXT_ALIAS_PREFIX: Final = "ev_c"
# What a context evidence block shows: where to find the data again and the excerpt, which the
# gateway masked. The evidence ID and the query hash stay out.
CONTEXT_EVIDENCE_FIELDS: Final = frozenset(
    {"query_text", "time_start", "time_end", "identifiers", "excerpt"}
)
# How much of the evidence IDs a rejected output cited goes back to the model.
MAX_SHOWN_CITATIONS: Final = 5
MAX_SHOWN_CITATION_LENGTH: Final = 80

_ALIAS = re.compile(r"ev_(c?)([1-9][0-9]*)")
# What makes a string an EvidenceId: Annotated[str, StringConstraints(pattern=...)].
_EVIDENCE_ID: Final = next(
    item for item in get_args(EvidenceId)[1:] if isinstance(item, StringConstraints)
)

type Cite = Callable[[str, str], str]


def context_alias(position: int) -> str:
    """The alias of the `position`-th item (from 1) of a run's context evidence."""
    if position < 1:
        raise ValueError(f"context evidence positions start at 1, got {position}")
    return f"{CONTEXT_ALIAS_PREFIX}{position}"


def context_source(ref: EvidenceRef) -> str:
    """The `source` of a context evidence block: qradar.evidence or falcon.evidence."""
    return f"{ref.source.value}.evidence"


def render_context_evidence(evidence: Sequence[EvidenceRef], *, nonce: str) -> str:
    """Evidence from earlier agents as untrusted data (decision T-38).

    Each item is its own `untrusted_*` block with source `qradar.evidence` or
    `falcon.evidence` and evidence_id `ev_c<n>`, `n` its place in `evidence` from 1. The block
    holds one JSON line: the query, the time range, the identifiers and the masked excerpt.
    The gateway's evidence ID is not part of the text: the run passes the same list's IDs in
    RunDeps.context_evidence. Returns "" when there is no evidence.
    """
    return "\n\n".join(
        wrap_json_lines(
            [ref.model_dump(mode="json", include=set(CONTEXT_EVIDENCE_FIELDS))],
            source=context_source(ref),
            nonce=nonce,
            evidence_id=context_alias(position),
        )
        for position, ref in enumerate(evidence, start=1)
    )


def citable_evidence(ctx: RunContext[RunDeps]) -> dict[str, str]:
    """What the model may cite in this run: alias -> the gateway's evidence ID.

    The aliases of the run's `ok` tool results (`ev_<n>`) and of its context evidence
    (`ev_c<n>`).
    """
    context = {
        context_alias(position): evidence_id
        for position, evidence_id in enumerate(ctx.deps.context_evidence, start=1)
    }
    return evidence_aliases(ctx.messages) | context


def check_evidence[OutputT: BaseModel](ctx: RunContext[RunDeps], output: OutputT) -> OutputT:
    """Output validator: every evidence field of `output` cites only evidence of this run.

    The model cites aliases; the validated output carries the gateway's evidence IDs instead.
    A citation that is not an alias of this run (an unknown alias, `ev_none`, an evidence ID
    itself) sends the output back to the model for another try (Pydantic AI output retries).
    The message lists the aliases it may cite and holds no evidence ID the model did not write.
    """
    citable = citable_evidence(ctx)
    rejected: list[_Rejected] = []

    def cite(alias: str, noun: str) -> str:
        if alias in citable:
            return citable[alias]
        rejected.append(_Rejected(alias, noun))
        return alias

    checked = _cite_model(output, cite, root=True)
    if rejected:
        raise ModelRetry(_rejection(rejected, citable, context=bool(ctx.deps.context_evidence)))
    return checked


def evidence_fields(model: type[BaseModel]) -> frozenset[str]:
    """The names of `model`'s own evidence fields, which check_evidence checks.

    Raises TypeError for a field that holds an EvidenceId in a shape check_evidence does not
    cover, such as a dict or a set.
    """
    return frozenset(name for name, kind in _fields(model) if kind is not _Kind.NESTED)


def check_evidence_fields(model: type[BaseModel]) -> None:
    """Raise TypeError if `model`, or a model nested in it, holds an EvidenceId in a shape
    check_evidence does not cover. builder.create_agent calls it for the output type."""
    seen: set[type[BaseModel]] = set()
    pending = [model]
    while pending:
        current = pending.pop()
        if current not in seen:
            seen.add(current)
            _fields(current)
            pending.extend(_nested_models(current))


# --- walking an output -----------------------------------------------------------------------


class _Kind(Enum):
    ONE = "one"
    """An EvidenceId, or None."""
    MANY = "many"
    """A list or tuple of EvidenceIds."""
    NESTED = "nested"
    """Models, alone, optional, or in a list, tuple or dict value; walked at run time."""


@dataclass(frozen=True)
class _Rejected:
    alias: str
    noun: str
    """What the model removes if it cannot cite: "claim", "ID from <field>"..."""


def _cite_model[M: BaseModel](model: M, cite: Cite, *, root: bool) -> M:
    updates: dict[str, object] = {}
    for name, kind in _fields(type(model)):
        value: object = getattr(model, name)
        noun = f"ID from {name}" if root else _noun(type(model))
        if kind is _Kind.ONE:
            mapped = value if value is None else cite(cast(str, value), noun)
        elif kind is _Kind.MANY:
            aliases = [cite(alias, noun) for alias in cast(Sequence[str], value)]
            mapped = tuple(aliases) if isinstance(value, tuple) else aliases
        else:
            mapped = _cite_value(value, cite)
        if mapped is not value:
            updates[name] = mapped
    return model.model_copy(update=updates) if updates else model


def _cite_value(value: object, cite: Cite) -> object:
    if isinstance(value, BaseModel):
        return _cite_model(value, cite, root=False)
    if isinstance(value, list | tuple):
        original = cast(Sequence[object], value)
        items = [_cite_value(item, cite) for item in original]
        if all(new is old for new, old in zip(items, original, strict=True)):
            return value
        return tuple(items) if isinstance(value, tuple) else items
    if isinstance(value, dict):
        entries = cast(dict[object, object], value)
        mapped = {key: _cite_value(item, cite) for key, item in entries.items()}
        return value if all(mapped[key] is item for key, item in entries.items()) else mapped
    return value


def _noun(model: type[BaseModel]) -> str:
    """`TimelineEntry` -> "timeline entry"."""
    return re.sub(r"(?<!^)(?=[A-Z])", " ", model.__name__).lower()


def _rejection(rejected: Sequence[_Rejected], citable: Mapping[str, str], *, context: bool) -> str:
    """What the model is told about citations it may not make: its own rejected citations,
    tag-neutralized and cut short, and the aliases it may cite. No gateway evidence ID."""
    unknown = sorted({item.alias for item in rejected})
    shown = ", ".join(cited[:MAX_SHOWN_CITATION_LENGTH] for cited in unknown[:MAX_SHOWN_CITATIONS])
    nouns = " or ".join(dict.fromkeys(item.noun for item in rejected))
    where = (
        "are neither context evidence nor returned by your tool calls in this run"
        if context
        else "were not returned by your tool calls in this run"
    )
    text = f"These evidence_ids {where}: {neutralize_tags(shown)}."
    if not citable:
        return f"{text} Your tool calls returned no evidence you can cite: remove the {nouns}."
    listed = ", ".join(sorted(citable, key=_alias_order))
    return f"{text} You can cite only {listed}. Cite one of them or remove the {nouns}."


def _alias_order(alias: str) -> tuple[bool, int]:
    """Tool aliases first, then context aliases, each by number."""
    match = _ALIAS.fullmatch(alias)
    if match is None:  # pragma: no cover - citable_evidence holds only aliases
        raise ValueError(f"not an evidence alias: {alias!r}")
    return bool(match.group(1)), int(match.group(2))


# --- evidence fields of a model ----------------------------------------------------------------


@cache
def _fields(model: type[BaseModel]) -> tuple[tuple[str, _Kind], ...]:
    """The fields of `model` that hold evidence IDs or models, and how they hold them."""
    found: list[tuple[str, _Kind]] = []
    for name, field in model.model_fields.items():
        kind = _kind(field.rebuild_annotation(), where=f"{model.__name__}.{name}")
        if kind is not None:
            found.append((name, kind))
    return tuple(found)


def _kind(annotation: object, *, where: str) -> _Kind | None:
    annotation = _unwrap(annotation)
    if _is_evidence_id(annotation):
        return _Kind.ONE
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _Kind.NESTED
    origin = get_origin(annotation)
    members = [item for item in get_args(annotation) if item not in (Ellipsis, type(None))]
    member_kinds = [_kind(member, where=where) for member in members]
    kinds = set(member_kinds) - {None}
    if not kinds:
        return None
    if kinds == {_Kind.NESTED} and origin in (Union, types.UnionType, list, tuple, dict):
        return _Kind.NESTED
    if origin in (Union, types.UnionType) and len(members) == 1:
        return kinds.pop()  # an optional evidence field
    if origin in (list, tuple) and all(kind is _Kind.ONE for kind in member_kinds):
        return _Kind.MANY
    raise TypeError(
        f"{where} holds an EvidenceId in a shape check_evidence does not cover; use an "
        "EvidenceId, an optional one, or a list or tuple of them"
    )


def _unwrap(annotation: object) -> object:
    """`annotation` without the Annotated layers that do not make it an EvidenceId."""
    while get_origin(annotation) is Annotated and not _is_evidence_id(annotation):
        annotation = get_args(annotation)[0]
    return annotation


def _is_evidence_id(annotation: object) -> bool:
    if get_origin(annotation) is not Annotated:
        return False
    base, *metadata = get_args(annotation)
    return base is str and _EVIDENCE_ID in metadata


def _nested_models(model: type[BaseModel]) -> list[type[BaseModel]]:
    nested: list[type[BaseModel]] = []
    pending: list[object] = [field.rebuild_annotation() for field in model.model_fields.values()]
    while pending:
        annotation = pending.pop()
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            nested.append(annotation)
        else:
            pending.extend(get_args(annotation))
    return nested
