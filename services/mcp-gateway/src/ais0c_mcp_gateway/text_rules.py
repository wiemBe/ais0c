"""Limits on the free text a call sends to QRadar, such as an offense note (T-018 criterion 5).

The Action Executor builds a note from structured fields and a fixed template, and cleans every
field first (architecture §9, "QRadar offense notu"). The gateway checks the finished text once
more, whatever built it. A text argument with a rule in config/policies/<connector>.yaml:

- is at most `max_length` characters (code points) long;
- holds no control character, invisible format character (zero-width characters, bidirectional
  overrides, Unicode tags, the byte order mark), lone surrogate, or line or paragraph separator.
  With `multiline`, the line feed "\\n" is allowed, because the note template puts each field on
  its own line; "\\r", tabs and the other line breaks are not.

These are the characters `ais0c_executor.common.is_clean(..., multiline=True)` refuses (T-017),
so a note the executor accepts passes here. A denial names the argument and, for a character,
its code point; it never repeats the text.
"""

import unicodedata
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field

# Control (Cc), format (Cf), surrogate (Cs), line separator (Zl), paragraph separator (Zp).
_NOT_ALLOWED: Final = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})


class TextRule(BaseModel):
    """The limits of one text argument of a gateway profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_length: Annotated[int, Field(ge=1)]
    multiline: bool = False
    """Whether the text may break lines with "\\n"."""


def text_problem(name: str, value: str, rule: TextRule) -> str | None:
    """What is wrong with the text `value` of the argument `name`; None when it passes `rule`."""
    if len(value) > rule.max_length:
        return f"{name} is longer than {rule.max_length} characters"
    for char in value:
        if char == "\n" and rule.multiline:
            continue
        if unicodedata.category(char) in _NOT_ALLOWED:
            return f"{name} holds a control or invisible character (U+{ord(char):04X})"
    return None
