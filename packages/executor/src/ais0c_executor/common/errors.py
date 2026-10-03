"""Errors of the executor."""

from datetime import datetime


class ExecutorError(Exception):
    """Base class of the executor's errors."""


class WritesDisabled(ExecutorError):
    """External writes are off, by the kill switch or because the platform runs in shadow mode.
    Nothing was written.

    `reason`, `changed_by` and `changed_at` describe the last change of the flag; all three are
    None while the flag has never been set.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
        changed_by: str | None = None,
        changed_at: datetime | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.changed_by = changed_by
        self.changed_at = changed_at


class TemplateError(ExecutorError):
    """A template is missing or broken, or a value cannot go into it.

    Rendering is deterministic, so a retry fails the same way.
    """
