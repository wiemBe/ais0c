# SPDX-License-Identifier: Apache-2.0
"""Removing secret values from text before it leaves the process."""

from __future__ import annotations

from collections.abc import Iterable

REDACTED = "[REDACTED]"


class Redactor:
    """Replaces every occurrence of the given secret values."""

    def __init__(self, secrets: Iterable[str]) -> None:
        # Longest first, so a secret that contains another one is replaced whole.
        self._secrets = tuple(sorted({s for s in secrets if s}, key=len, reverse=True))

    def text(self, value: str) -> str:
        for secret in self._secrets:
            if secret in value:
                value = value.replace(secret, REDACTED)
        return value
