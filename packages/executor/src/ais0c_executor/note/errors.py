"""Errors of the note module."""

from ais0c_executor.common import ExecutorError


class InvalidNote(ExecutorError):
    """The request cannot be made into a note: an identifier, the case link or a field is not
    usable, or the text does not fit. Nothing was read or written; a retry fails the same way."""


class OffenseNotesError(ExecutorError):
    """QRadar's notes of an offense could not be read, or the note could not be added.

    `retryable` says whether a later attempt may succeed: an unreachable gateway or a failed
    QRadar call may, a call the gateway refused does not.
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable
