"""Prompt loading, hashing and assembly (docs/impl/prompts.md).

A prompt is a versioned template, prompts/<agent>/v<N>.md, plus the shared rules in
prompts/_shared/rules.md. A template names its inputs with `{{ placeholder }}`s. Rendering
replaces them in one pass over the template, so text inside a value is never read as a
placeholder.

Data reaches the prompt in two kinds of sections that are never mixed: trusted organization
context inside <org_context>, built from the Analysis Catalog, and everything else inside the
policy package's `untrusted_*` wrapper.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import JsonValue

from ais0c_contracts import CatalogContext
from ais0c_policy import neutralize_tags, wrap_untrusted

SHARED_RULES_PATH: Final = "prompts/_shared/rules.md"
SHARED_RULES_PLACEHOLDER: Final = "shared_rules"

# The evidence_id of an untrusted block that is not gateway evidence: prompt context and tool
# calls that returned no evidence. Claims can never cite it.
NO_EVIDENCE_ID: Final = "ev_none"

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
_PROMPT_PATH = re.compile(r"prompts/[a-z][a-z0-9-]*/v[1-9][0-9]*\.md")


class PromptError(ValueError):
    """A prompt file is missing, invalid, or rendered with the wrong values."""


@dataclass(frozen=True)
class PromptTemplate:
    path: str
    """Repository-relative path of the template, e.g. prompts/triage/v1.md."""
    template: str
    shared_rules: str
    sha256: str
    """Hash of the prompt files; written to every run's Run Envelope (agent-harness.md §3)."""

    @property
    def version(self) -> str:
        """The template's agent and version, e.g. triage/v1."""
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
            raise PromptError("the shared rules come from prompts/_shared/rules.md, not a value")
        if missing := expected - values.keys():
            raise PromptError(f"{self.path}: no value for {', '.join(sorted(missing))}")
        if unused := values.keys() - expected:
            raise PromptError(f"{self.path}: no placeholder for {', '.join(sorted(unused))}")
        filled = {**values, SHARED_RULES_PLACEHOLDER: self.shared_rules.removesuffix("\n")}
        return _PLACEHOLDER.sub(lambda match: filled[match.group(1)], self.template)


def load_prompt(repo_root: Path, path: str) -> PromptTemplate:
    """Load a prompt template and the shared rules from under `repo_root`."""
    if not _PROMPT_PATH.fullmatch(path):
        raise PromptError(f"prompt path must look like prompts/<agent>/v<N>.md, got {path!r}")
    prompts_dir = (repo_root / "prompts").resolve()
    files: list[tuple[str, bytes]] = []
    for relative in (path, SHARED_RULES_PATH):
        file = (repo_root / relative).resolve()
        if not file.is_relative_to(prompts_dir):
            raise PromptError(f"{relative} resolves to a file outside prompts/")
        try:
            files.append((relative, file.read_bytes()))
        except OSError as error:
            raise PromptError(f"cannot read {relative}: {error}") from error
    try:
        template, shared_rules = (data.decode("utf-8") for _, data in files)
    except UnicodeDecodeError as error:
        raise PromptError(f"prompt files must be UTF-8: {error}") from error
    if _PLACEHOLDER.findall(template).count(SHARED_RULES_PLACEHOLDER) != 1:
        raise PromptError(f"{path} must contain {{{{ {SHARED_RULES_PLACEHOLDER} }}}} exactly once")
    return PromptTemplate(
        path=path, template=template, shared_rules=shared_rules, sha256=prompt_hash(files)
    )


def prompt_hash(files: Iterable[tuple[str, bytes]]) -> str:
    """SHA-256 over each file's path, length and bytes, in the given order."""
    digest = hashlib.sha256()
    for path, data in files:
        digest.update(f"{path}\0{len(data)}\0".encode())
        digest.update(data)
    return digest.hexdigest()


def render_org_context(catalog: CatalogContext) -> str:
    """The trusted <org_context> section, built from the Analysis Catalog (D-25)."""
    lines: list[str] = []
    for rule in catalog.rules:
        fields = [f"mode={rule.mode}"]
        if rule.min_level is not None:
            fields.append(f"min_level={rule.min_level}")
        lines.append(f"Rule {rule.rule_id}: {', '.join(fields)}.")
        if rule.context_note:
            lines.append(f"Note: {rule.context_note}")
    for source in catalog.log_sources:
        fields = [source.description] if source.description else []
        if source.criticality is not None:
            fields.append(f"criticality={source.criticality}")
        lines.append(f"Log source {source.log_source_id}: {', '.join(fields) or 'not described'}.")
        if source.context_note:
            lines.append(f"Note: {source.context_note}")
    body = "\n".join(lines) or "The Analysis Catalog has no entries for this offense."
    # Catalog notes are trusted, but still cannot close this section or open another one.
    return f"<org_context>\n{neutralize_tags(body)}\n</org_context>"


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
