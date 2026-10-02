"""Wrapping of untrusted data (tool results, log text) before it enters a prompt.

Format and rules: docs/impl/prompts.md, "Güvenilmez veri"; decision T-17.

    <untrusted_7f3a9c source="qradar.ariel" evidence_id="ev_01JB3K...">
    ... tool result ...
    </untrusted_7f3a9c>

The tag suffix is a fresh random nonce per agent run. Inside the content, anything that
reads as the start of an `untrusted_*` or `org_context` tag has its `<` replaced by `&lt;`,
so log text cannot close the data block or open a trusted one, even when it guesses the
nonce. Case, whitespace, invisible format characters (such as zero-width spaces) and
compatibility forms (such as the fullwidth less-than sign) do not hide such a tag.
"""

import re
import secrets
import unicodedata

NONCE_BYTES = 6  # 12 hex characters
NEUTRALIZED_ANGLE = "&lt;"

_NONCE = re.compile(r"[0-9a-f]{8,64}")
_SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
# Stricter than the contracts' EvidenceId pattern (`ev_\S+`), which allows `"` and `>`.
_EVIDENCE_ID = re.compile(r"ev_[A-Za-z0-9_.:-]{1,128}")
_RESERVED_TAG_NAMES = ("untrusted_", "org_context")


def new_nonce() -> str:
    """Return a fresh random tag suffix: lowercase hex, at least 8 characters."""
    return secrets.token_hex(NONCE_BYTES)


def wrap_untrusted(content: str, source: str, evidence_id: str, nonce: str) -> str:
    """Wrap `content` in an `untrusted_<nonce>` block after neutralizing tag-like text.

    `source`, `evidence_id` and `nonce` are written into the tag as they are, so they are
    validated instead of escaped; an invalid value raises ValueError.
    """
    if not _NONCE.fullmatch(nonce):
        raise ValueError("nonce must be 8 to 64 lowercase hex characters")
    if not _SOURCE.fullmatch(source):
        raise ValueError(f"invalid source: {source!r}")
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
