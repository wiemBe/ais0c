"""Wrapping of untrusted data (tool results, log text, external knowledge) before it enters a
prompt.

Format and rules: docs/impl/prompts.md, "Güvenilmez veri"; trust layers: architecture §22,
decision T-20.

    <untrusted_7f3a9c source="qradar.ariel" evidence_id="ev_01JB3K...">
    ... tool result ...
    </untrusted_7f3a9c>

The tag suffix is a fresh random nonce per agent run. Inside the content, anything that
reads as the start of an `untrusted_*` or `org_context` tag has its `<` replaced by `&lt;`,
so log text cannot close the data block or open a trusted one, even when it guesses the
nonce. Case, whitespace, invisible format characters (such as zero-width spaces) and
compatibility forms (such as the fullwidth less-than sign) do not hide such a tag.

The source says where the content came from, and only known sources are accepted: a
connector's results (`qradar.<x>`, `falcon.<x>`) and external knowledge (`kb.<kind>`).
Knowledge has a known origin but is wrapped like log data: a CTI report or a runbook can
carry an attacker's text.
"""

import re
import secrets
import unicodedata
from enum import StrEnum
from typing import Final

NONCE_BYTES = 6  # 12 hex characters
NEUTRALIZED_ANGLE = "&lt;"
MAX_SOURCE_LENGTH: Final = 64


class KnowledgeKind(StrEnum):
    """Kinds of external knowledge (architecture §22); each is wrapped as `kb.<kind>`."""

    ATTACK = "attack"
    CTI = "cti"
    IOC = "ioc"
    RUNBOOK = "runbook"
    CASE = "case"

    @property
    def source(self) -> str:
        return f"kb.{self.value}"


# Connectors whose results reach a prompt; such a block's source is `<connector>.<x>`.
CONNECTOR_SOURCES: Final = ("qradar", "falcon")
KNOWLEDGE_SOURCES: Final = frozenset(kind.source for kind in KnowledgeKind)

_NONCE = re.compile(r"[0-9a-f]{8,64}")
_CONNECTOR_SOURCE = re.compile(rf"(?:{'|'.join(CONNECTOR_SOURCES)})\.[a-z0-9][a-z0-9_.-]*")
# Stricter than the contracts' EvidenceId pattern (`ev_\S+`), which allows `"` and `>`.
_EVIDENCE_ID = re.compile(r"ev_[A-Za-z0-9_.:-]{1,128}")
_RESERVED_TAG_NAMES = ("untrusted_", "org_context")


def new_nonce() -> str:
    """Return a fresh random tag suffix: lowercase hex, at least 8 characters."""
    return secrets.token_hex(NONCE_BYTES)


def is_known_source(source: str) -> bool:
    """Whether `source` may name an untrusted block: `qradar.<x>`, `falcon.<x>` or one of
    KNOWLEDGE_SOURCES.

    `<x>` is lowercase letters, digits, `_`, `.` and `-`, and the whole source is at most
    MAX_SOURCE_LENGTH characters.
    """
    if len(source) > MAX_SOURCE_LENGTH:
        return False
    return source in KNOWLEDGE_SOURCES or _CONNECTOR_SOURCE.fullmatch(source) is not None


def wrap_untrusted(content: str, source: str, evidence_id: str, nonce: str) -> str:
    """Wrap `content` in an `untrusted_<nonce>` block after neutralizing tag-like text.

    `source`, `evidence_id` and `nonce` are written into the tag as they are, so they are
    validated instead of escaped; an invalid value or an unknown source raises ValueError.
    """
    if not _NONCE.fullmatch(nonce):
        raise ValueError("nonce must be 8 to 64 lowercase hex characters")
    if not is_known_source(source):
        raise ValueError(f"unknown source: {source!r}")
    if not _EVIDENCE_ID.fullmatch(evidence_id):
        raise ValueError(f"invalid evidence_id: {evidence_id!r}")
    tag = f"untrusted_{nonce}"
    return (
        f'<{tag} source="{source}" evidence_id="{evidence_id}">\n'
        f"{neutralize_tags(content)}\n"
        f"</{tag}>"
    )


def neutralize_tags(content: str) -> str:
    """Replace the `<` of every tag-like `untrusted_*` or `org_context` opening or closing."""
    parts: list[str] = []
    for index, char in enumerate(content):
        if _fold(char) == "<" and _starts_reserved_tag(content, index + 1):
            parts.append(NEUTRALIZED_ANGLE)
        else:
            parts.append(char)
    return "".join(parts)


def _fold(char: str) -> str:
    """How a reader would likely see `char`: "" for whitespace and invisible characters."""
    if char.isspace() or unicodedata.category(char) == "Cf":
        return ""
    return unicodedata.normalize("NFKC", char).casefold()


def _starts_reserved_tag(content: str, start: int) -> bool:
    """Whether the text from `start` reads as `[/]untrusted_` or `[/]org_context`."""
    seen = ""
    for index in range(start, len(content)):
        folded = _fold(content[index])
        if not folded:
            continue
        seen += folded
        name = seen.removeprefix("/")
        if any(name.startswith(reserved) for reserved in _RESERVED_TAG_NAMES):
            return True
        if not any(reserved.startswith(name) for reserved in _RESERVED_TAG_NAMES):
            return False
    return False
