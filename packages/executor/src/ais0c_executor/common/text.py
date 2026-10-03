"""Cleaning of the text that goes into a note or an e-mail (architecture §9).

Event names, user names and the AI's summary can carry text an attacker put into a log. Cleaned,
such a value is one line of plain text: it cannot add a line of its own (a fake `[AI-SOC]`
header, say), hide text from the reader or reorder it on screen.
"""

import unicodedata

ELLIPSIS = "…"

# Control characters (Cc), invisible format characters (Cf: zero-width characters, bidirectional
# overrides, Unicode tag characters, the byte order mark) and lone surrogates (Cs), which cannot
# even be encoded.
_HIDDEN = frozenset({"Cc", "Cf", "Cs"})
# Not in a clean text: the hidden characters, and line and paragraph separators (Zl, Zp), which
# break a line as "\n" does.
_NOT_CLEAN = _HIDDEN | {"Zl", "Zp"}


def clean_text(value: str, max_length: int | None = None) -> str:
    """`value` as one line of plain text, at most `max_length` characters long.

    - Line breaks, tabs and every other kind of whitespace become a space; runs of spaces
      become one, and the ends are trimmed.
    - Control characters, invisible format characters and lone surrogates are removed.
    - The text is put in NFC form, so a letter and its accent count as one character.
    - A longer text is cut and ends with "…", which counts toward `max_length`.
    """
    if not isinstance(value, str):
        raise TypeError(f"expected str, got {type(value).__name__}")
    if max_length is not None and max_length < 1:
        raise ValueError("max_length must be at least 1")
    kept: list[str] = []
    for char in value:
        if char.isspace():
            kept.append(" ")
        elif unicodedata.category(char) not in _HIDDEN:
            kept.append(char)
    # Without surrogates the text can be normalized; NFC adds no space or hidden character.
    text = " ".join(unicodedata.normalize("NFC", "".join(kept)).split())
    if max_length is not None and len(text) > max_length:
        text = text[: max_length - 1].rstrip() + ELLIPSIS
    return text


def is_clean(value: str, *, multiline: bool = False) -> bool:
    """Whether `value` has no control character, invisible format character, lone surrogate or
    line break. With `multiline`, "\\n" is allowed; "\\r" and the other line breaks are not."""
    return all(
        (multiline and char == "\n") or unicodedata.category(char) not in _NOT_CLEAN
        for char in value
    )
