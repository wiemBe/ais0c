"""Prompt loading, hashing and assembly (docs/impl/prompts.md).

A prompt is a versioned template, prompts/<agent>/v<N>.md, plus a version of the shared rules,
prompts/_shared/rules/v<N>.md; the agent manifest names both. A template names its inputs with
`{{ placeholder }}`s. Rendering replaces them in one pass over the template, so text inside a
value is never read as a placeholder.

Data reaches the prompt in trust layers that are never mixed (architecture §22, T-20).
Organization facts go inside <org_context>, built only from typed facts (render_org_context).
Log data and external knowledge go inside the policy package's `untrusted_*` wrapper
(wrap_json_lines, render_knowledge). Policy never reaches the prompt: code enforces it.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Final, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from ais0c_agents.manifest import AgentManifest
from ais0c_contracts import (
    SHORT_TEXT_MAX_LENGTH,
    SUMMARY_MAX_LENGTH,
    CatalogContext,
    CriticalAssetHit,
    IocHit,
    ShortText,
    Summary,
    UtcDatetime,
)
from ais0c_policy import KnowledgeKind, neutralize_tags, wrap_untrusted

SHARED_RULES_PLACEHOLDER: Final = "shared_rules"

# The evidence_id of an untrusted block that is not gateway evidence: prompt context and tool
# calls that returned no evidence. Claims can never cite it.
NO_EVIDENCE_ID: Final = "ev_none"

KNOWLEDGE_TEXT_MAX_LENGTH: Final = 4000

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
_PROMPT_PATH = re.compile(r"prompts/[a-z][a-z0-9-]*/v[1-9][0-9]*\.md")
_SHARED_RULES_PATH = re.compile(r"prompts/_shared/rules/v[1-9][0-9]*\.md")


class PromptError(ValueError):
    """A prompt file is missing, invalid, or rendered with the wrong values."""


@dataclass(frozen=True)
class PromptTemplate:
    path: str
    """Repository-relative path of the template, e.g. prompts/triage/v2.md."""
    template: str
    shared_rules_path: str
    """Repository-relative path of the shared rules, e.g. prompts/_shared/rules/v2.md."""
    shared_rules: str
    sha256: str
    """Hash of the prompt files; written to every run's Run Envelope (agent-harness.md §3)."""

    @property
    def version(self) -> str:
        """The template's agent and version, e.g. triage/v2."""
        return self.path.removeprefix("prompts/").removesuffix(".md")

    @property
    def placeholders(self) -> frozenset[str]:
        return frozenset(_PLACEHOLDER.findall(self.template))

    def render(self, values: Mapping[str, str]) -> str:
        """Fill every placeholder; the shared rules are filled in automatically, verbatim.

        Raises PromptError if a placeholder has no value or a value has no placeholder.
        """
        expected = self.placeholders - {SHARED_RULES_PLACEHOLDER}
        if SHARED_RULES_PLACEHOLDER in values:
            raise PromptError(f"the shared rules come from {self.shared_rules_path}, not a value")
        if missing := expected - values.keys():
            raise PromptError(f"{self.path}: no value for {', '.join(sorted(missing))}")
        if unused := values.keys() - expected:
            raise PromptError(f"{self.path}: no placeholder for {', '.join(sorted(unused))}")
        filled = {**values, SHARED_RULES_PLACEHOLDER: self.shared_rules.removesuffix("\n")}
        return _PLACEHOLDER.sub(lambda match: filled[match.group(1)], self.template)


def load_agent_prompt(repo_root: Path, manifest: AgentManifest) -> PromptTemplate:
    """Load the prompt template and the shared rules version that `manifest` names."""
    return load_prompt(repo_root, manifest.prompt, shared_rules=manifest.shared_rules)


def load_prompt(repo_root: Path, path: str, *, shared_rules: str) -> PromptTemplate:
    """Load a prompt template and a version of the shared rules from under `repo_root`.

    `path` is prompts/<agent>/v<N>.md and `shared_rules` is prompts/_shared/rules/v<N>.md.
    """
    if not _PROMPT_PATH.fullmatch(path):
        raise PromptError(f"prompt path must look like prompts/<agent>/v<N>.md, got {path!r}")
    if not _SHARED_RULES_PATH.fullmatch(shared_rules):
        raise PromptError(
            f"shared rules path must look like prompts/_shared/rules/v<N>.md, got {shared_rules!r}"
        )
    prompts_dir = (repo_root / "prompts").resolve()
    files: list[tuple[str, bytes]] = []
    for relative in (path, shared_rules):
        file = (repo_root / relative).resolve()
        if not file.is_relative_to(prompts_dir):
            raise PromptError(f"{relative} resolves to a file outside prompts/")
        try:
            files.append((relative, file.read_bytes()))
        except OSError as error:
            raise PromptError(f"cannot read {relative}: {error}") from error
    try:
        template, rules = (data.decode("utf-8") for _, data in files)
    except UnicodeDecodeError as error:
        raise PromptError(f"prompt files must be UTF-8: {error}") from error
    if _PLACEHOLDER.findall(template).count(SHARED_RULES_PLACEHOLDER) != 1:
        raise PromptError(f"{path} must contain {{{{ {SHARED_RULES_PLACEHOLDER} }}}} exactly once")
    return PromptTemplate(
        path=path,
        template=template,
        shared_rules_path=shared_rules,
        shared_rules=rules,
        sha256=prompt_hash(files),
    )


def prompt_hash(files: Iterable[tuple[str, bytes]]) -> str:
    """SHA-256 over each file's path, length and bytes, in the given order."""
    digest = hashlib.sha256()
    for path, data in files:
        digest.update(f"{path}\0{len(data)}\0".encode())
        digest.update(data)
    return digest.hexdigest()


# --- organization facts: <org_context> ----------------------------------------------------------


# Not a ContractModel: contract models are defined only in packages/contracts.
class MaintenanceWindow(BaseModel):
    """A planned maintenance window of some log sources: an organization fact (architecture
    §22). Contracts and storage have no maintenance windows yet; the builder already takes them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: UtcDatetime
    end: UtcDatetime
    log_source_ids: Annotated[tuple[int, ...], Field(min_length=1, max_length=50)]
    context_note: Summary | None = None

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> Self:
        if self.end <= self.start:
            raise ValueError("a maintenance window must end after it starts")
        return self


def render_org_context(
    catalog: CatalogContext,
    *,
    critical_assets: Sequence[CriticalAssetHit] = (),
    maintenance_windows: Sequence[MaintenanceWindow] = (),
) -> str:
    """The <org_context> section: organization facts, never instructions (T-20).

    Takes typed facts only: the Analysis Catalog entries (D-25), the critical assets the
    offense touches and maintenance windows. External knowledge has its own types and becomes
    untrusted data (render_knowledge); any other value raises TypeError. Free text comes only
    from a fact's `context_note`, cut to the Summary limit; the other text values are short
    labels, cut to the ShortText limit. Every value stays on one line.
    """
    _require_fact(catalog, CatalogContext)
    lines: list[str] = []
    if not catalog.rules and not catalog.log_sources:
        lines.append("The Analysis Catalog has no entries for this offense.")
    for rule in catalog.rules:
        fields = [f"mode={rule.mode}"]
        if rule.min_level is not None:
            fields.append(f"min_level={rule.min_level}")
        lines.append(f"Rule {rule.rule_id}: {', '.join(fields)}.")
        lines.extend(_note(rule.context_note))
    for source in catalog.log_sources:
        fields = _labels(source.description)
        if source.criticality is not None:
            fields.append(f"criticality={source.criticality}")
        lines.append(f"Log source {source.log_source_id}: {', '.join(fields) or 'not described'}.")
        lines.extend(_note(source.context_note))
    for asset in critical_assets:
        _require_fact(asset, CriticalAssetHit)
        fields = [*_labels(asset.label), f"level={asset.level}"]
        lines.append(
            f"Critical asset {_one_line(asset.value, SHORT_TEXT_MAX_LENGTH)}: {', '.join(fields)}."
        )
    for window in maintenance_windows:
        _require_fact(window, MaintenanceWindow)
        sources = ", ".join(str(source_id) for source_id in window.log_source_ids)
        lines.append(
            f"Maintenance window {_utc(window.start)} to {_utc(window.end)}: log sources {sources}."
        )
        lines.extend(_note(window.context_note))
    # Facts are trusted, but still cannot close this section or open another one.
    return "<org_context>\n" + neutralize_tags("\n".join(lines)) + "\n</org_context>"


def _require_fact(value: object, kind: type) -> None:
    # Type checkers already reject other types; this stops untyped callers too.
    if not isinstance(value, kind):
        raise TypeError(
            f"org_context takes organization facts only; got {type(value).__name__}, "
            f"expected {kind.__name__}"
        )


def _note(text: str | None) -> list[str]:
    note = _one_line(text or "", SUMMARY_MAX_LENGTH)
    return [f"Note: {note}"] if note else []


def _labels(text: str | None) -> list[str]:
    label = _one_line(text or "", SHORT_TEXT_MAX_LENGTH)
    return [label] if label else []


def _one_line(text: str, limit: int) -> str:
    """`text` on one line with single spaces, cut to `limit` characters."""
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- untrusted data: log data and external knowledge -------------------------------------------


# Not a ContractModel: contract models are defined only in packages/contracts.
class KnowledgeItem(BaseModel):
    """One piece of external knowledge (architecture §22): an ATT&CK technique, part of a CTI
    report, an IOC, part of a runbook or a past case.

    Its origin is known, but it is untrusted data all the same: render_knowledge wraps it as
    `kb.<kind>`, and render_org_context does not take it. The knowledge plane that will supply
    it comes in Faz 3.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: KnowledgeKind
    ref: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    """What it is in its source: an ATT&CK ID, a report, runbook or case ID, an indicator."""
    title: ShortText | None = None
    text: Annotated[str, StringConstraints(max_length=KNOWLEDGE_TEXT_MAX_LENGTH)]


def render_knowledge(items: Iterable[KnowledgeItem | IocHit], *, nonce: str) -> str:
    """External knowledge as untrusted data (T-20): one `kb.<kind>` block per kind, in the
    order of KnowledgeKind, with one JSON line per item.

    The enrichment's IOC hits are knowledge of kind `ioc`. Returns "" when there is none.
    """
    rows: dict[KnowledgeKind, list[JsonValue]] = {}
    for item in items:
        if isinstance(item, IocHit):
            rows.setdefault(KnowledgeKind.IOC, []).append(item.model_dump(mode="json"))
        elif isinstance(item, KnowledgeItem):
            rows.setdefault(item.kind, []).append(item.model_dump(mode="json", exclude={"kind"}))
        else:
            raise TypeError(f"not external knowledge: {type(item).__name__}")
    return "\n\n".join(
        wrap_json_lines(rows[kind], source=kind.source, nonce=nonce)
        for kind in KnowledgeKind
        if kind in rows
    )


def wrap_json_lines(
    lines: Iterable[JsonValue], *, source: str, nonce: str, evidence_id: str = NO_EVIDENCE_ID
) -> str:
    """Untrusted data as one JSON document per line, inside the `untrusted_*` wrapper."""
    content = "\n".join(_json_line(line) for line in lines)
    return wrap_untrusted(content, source, evidence_id, nonce)


# json.dumps escapes control characters, but not these line breaks: a value could otherwise
# seem to start a new row.
_LINE_BREAKS = str.maketrans(
    {"\x85": "\\u0085", "\N{LINE SEPARATOR}": "\\u2028", "\N{PARAGRAPH SEPARATOR}": "\\u2029"}
)


def _json_line(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False).translate(_LINE_BREAKS)
