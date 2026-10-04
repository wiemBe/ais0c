"""Errors of the e-mail module."""

from ais0c_executor.common import ExecutorError


class InvalidEmail(ExecutorError):
    """The request or the message cannot be made into an e-mail: an identifier, the case link, a
    field or the template is not usable. Nothing was sent; a retry fails the same way."""


class EmailTransportError(ExecutorError):
    """The SMTP relay could not be reached, or it did not take the e-mail.

    `retryable` says whether a later attempt may succeed: a lost connection or a 4xx reply may;
    a 5xx reply, a refused login, a certificate that does not verify or a relay without
    STARTTLS does not.
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable
