"""Deterministic injection scan of skill text (decision T-21; architecture §22).

A skill's instructions enter the agent's prompt as part of it, not inside the `untrusted_*`
wrapper, so the loader refuses skill text that

- tries to override the agent's rules ("ignore previous instructions", "you are now", ...);
- carries chat-template markers or role headers ("<|im_start|>", "[INST]", "System:");
- reads as an `untrusted_*` or `org_context` tag, escaped forms such as `&lt;org_context`
  included;
- hides content from the people who review and approve it: invisible and control characters,
  HTML comments and, in instructions.md, any character outside printable ASCII apart from
  typographic punctuation. Instructions are English, so this also keeps out look-alike letters
  from other scripts.

Matching sees through case, runs of whitespace and line breaks, compatibility forms such as
fullwidth letters, combining marks and invisible format characters. The scan is the first
check; review and the red-team suites (T-030) are the others.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Finding:
    line: int
    """1-based line of the scanned text."""
    reason: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.reason}"


# Characters a reviewer cannot see, or that are not text at all. Tab and line feed are fine.
_HIDDEN_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cn", "Co", "Cs", "Zl", "Zp"})
_VISIBLE_CONTROLS: Final = frozenset("\t\n")
# Dropped before matching, so they cannot split a phrase: combining marks, invisible format
# characters and the hidden characters above.
_DROPPED_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cn", "Co", "Cs", "Mn", "Me"})
# Typographic punctuation editors insert. Anything else outside printable ASCII is refused in
# instructions.md.
_TYPOGRAPHY: Final = frozenset(
    "\N{LEFT SINGLE QUOTATION MARK}\N{RIGHT SINGLE QUOTATION MARK}"
    "\N{LEFT DOUBLE QUOTATION MARK}\N{RIGHT DOUBLE QUOTATION MARK}"
    "\N{EN DASH}\N{EM DASH}\N{HORIZONTAL ELLIPSIS}\N{BULLET}\N{MIDDLE DOT}"
    "\N{RIGHTWARDS ARROW}\N{LESS-THAN OR EQUAL TO}\N{GREATER-THAN OR EQUAL TO}"
    "\N{MULTIPLICATION SIGN}"
)

_VERB = r"(?:ignore|disregard|forget|override|overrule)"
_QUALIFIER = (
    r"(?:all|any|every|each|the|your|my|our|these|those|of|previous|prior|earlier|above"
    r"|preceding|foregoing|former|original|initial|existing|other|system|shared|hard|safety"
    r"|security|platform)"
)
_TARGET = (
    r"(?:instructions?|rules?|prompts?|directives?|guidelines?|guidance|guardrails?"
    r"|constraints?|polic(?:y|ies)|context)"
)
_FILLER = r"(?:all|everything|anything|the|of|that|what|was|is|said|written|stated|you|were|told)"
_POSITION = r"(?:above|before|previously|earlier|so far)"

# Matched against the text folded with one space per run of whitespace.
_PHRASES: Final = (
    ("instruction override", re.compile(rf"\b{_VERB}(?: {_QUALIFIER}){{0,4}} {_TARGET}\b")),
    ("instruction override", re.compile(rf"\b{_VERB}(?: {_FILLER})* {_POSITION}\b")),
    (
        "instruction override",
        re.compile(
            r"(?:talimat|kural|y[oö]nerge|komut)\w*(?: \w+){0,2} "
            r"(?:yok ?say|g[oö]rmezden gel|unut|dikkate alma|ge[cç]ersiz k[iı]l)"
        ),
    ),
    ("role change", re.compile(r"\b(?:you are (?:now|no longer)|from now on)\b")),
    ("role change", re.compile(r"\bpretend (?:to be|you are|that you)\b")),
    ("prompt reference", re.compile(r"\b(?:system|developer) (?:prompt|instructions?)\b")),
    ("jailbreak phrase", re.compile(r"\b(?:jailbr(?:eak|oken)|developer mode|do anything now)\b")),
    (
        "chat-template marker",
        re.compile(r"<\||\|>|\[/?inst\]|<</?sys>>|</?s>|<(?:start|end)_of_turn>"),
    ),
)
# Matched against each line folded the same way, after any quote, list or heading marks.
_ROLE_HEADER: Final = re.compile(
    r"^(?:(?:>|[-*+]|\d+[.)]|#+) ?)*(?:system|assistant|developer)(?: ?:|$)"
)
# Matched against the text folded with whitespace removed, so `< / org_context` is caught.
_ANGLES: Final = (
    "<\N{SINGLE LEFT-POINTING ANGLE QUOTATION MARK}\N{MATHEMATICAL LEFT ANGLE BRACKET}"
    "\N{LEFT ANGLE BRACKET}\N{LEFT DOUBLE ANGLE BRACKET}"
)
_OPENING: Final = rf"(?:[{_ANGLES}]|&lt;?|&#0*60;?|&#x0*3c;?|\\u0*3c|\\x3c|%3c)"
_TAGS: Final = (
    ("trust-layer tag", re.compile(rf"{_OPENING}/?(?:untrusted|org[_.\-]?context)")),
    ("HTML comment", re.compile(rf"{_OPENING}!--")),
)
_MAX_SNIPPET: Final = 60


def scan_text(text: str) -> list[Finding]:
    """Findings for skill text that reaches a prompt: override phrases, chat-template markers
    and role headers, trust-layer tags, HTML comments and hidden characters."""
    findings = _hidden_characters(text, ascii_only=False) + _patterns(text)
    return sorted(findings, key=lambda finding: finding.line)


def scan_instructions(text: str) -> list[Finding]:
    """scan_text, and every character must be printable ASCII or typographic punctuation."""
    findings = _hidden_characters(text, ascii_only=True) + _patterns(text)
    return sorted(findings, key=lambda finding: finding.line)


def _hidden_characters(text: str, *, ascii_only: bool) -> list[Finding]:
    """One finding per distinct refused character, at its first line."""
    findings: list[Finding] = []
    seen: set[str] = set()
    for index, char in enumerate(text):
        if char in seen or char in _VISIBLE_CONTROLS:
            continue
        if unicodedata.category(char) in _HIDDEN_CATEGORIES:
            reason = f"hidden or control character {_describe(char)}"
        elif ascii_only and not (" " <= char <= "~" or char in _TYPOGRAPHY):
            reason = (
                f"character {_describe(char)}: instructions are English and use printable ASCII "
                "apart from typographic punctuation"
            )
        else:
            continue
        seen.add(char)
        findings.append(Finding(_line(text, index), reason))
    return findings


def _patterns(text: str) -> list[Finding]:
    findings: list[Finding] = []
    spaced, origin = _fold(text, spaces=True)
    for reason, pattern in _PHRASES:
        findings.extend(_matches(text, spaced, origin, pattern, reason))
    tight, tight_origin = _fold(text, spaces=False)
    for reason, pattern in _TAGS:
        findings.extend(_matches(text, tight, tight_origin, pattern, reason))
    for number, line in enumerate(text.split("\n"), start=1):
        folded, _ = _fold(line, spaces=True)
        if _ROLE_HEADER.match(folded.strip()):
            findings.append(Finding(number, f"role header {_snippet(line)}"))
    return findings


def _matches(
    text: str, folded: str, origin: list[int], pattern: re.Pattern[str], reason: str
) -> list[Finding]:
    findings: list[Finding] = []
    for match in pattern.finditer(folded):
        first, last = origin[match.start()], origin[match.end() - 1]
        findings.append(Finding(_line(text, first), f"{reason} {_snippet(text[first : last + 1])}"))
    return findings


def _fold(text: str, *, spaces: bool) -> tuple[str, list[int]]:
    """`text` as a reader would likely see it, and the index in `text` of each folded character.

    Compatibility forms become plain characters (NFKC), case is folded, and combining marks and
    invisible characters are dropped. Each run of whitespace becomes one space, or nothing when
    `spaces` is false.
    """
    folded: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        for part in unicodedata.normalize("NFKC", char).casefold():
            if part.isspace():
                if spaces and folded and folded[-1] != " ":
                    folded.append(" ")
                    origin.append(index)
            elif unicodedata.category(part) not in _DROPPED_CATEGORIES:
                folded.append(part)
                origin.append(index)
    return "".join(folded), origin


def _line(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _snippet(text: str) -> str:
    """The matched text for an error message; repr shows hidden characters as escapes."""
    if len(text) > _MAX_SNIPPET:
        text = text[: _MAX_SNIPPET - 3] + "..."
    return repr(text)


def _describe(char: str) -> str:
    return f"U+{ord(char):04X} ({unicodedata.name(char, 'unnamed')})"
