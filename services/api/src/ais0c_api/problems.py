"""RFC 9457 problem answers (`application/problem+json`), the API's only error format.

`title` is a machine-readable code (`catalog.rule_not_found`, `qa.already_resolved`), never a
sentence: the UI builds the Turkish message from its own code table (api.md). `detail` is
optional, English, and never carries user-facing text or request content.

A 4xx writes neither a change nor an audit row: the routers raise `Problem` before they open the
transaction, or the transaction is rolled back (criterion 3).
"""

from typing import Any, Final

from fastapi.responses import JSONResponse

PROBLEM_MEDIA_TYPE: Final = "application/problem+json"
# RFC 9457's default for a code-only answer.
PROBLEM_TYPE: Final = "about:blank"


class Problem(Exception):
    """A problem answer. `status` is the HTTP status, `title` the machine-readable code."""

    def __init__(
        self,
        status: int,
        title: str,
        *,
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(title)
        self.status = status
        self.title = title
        self.detail = detail
        self.extra = extra or {}
        self.headers = headers

    def response(self) -> JSONResponse:
        return problem_response(
            self.status, self.title, detail=self.detail, extra=self.extra, headers=self.headers
        )


def problem_response(
    status: int,
    title: str,
    *,
    detail: str | None = None,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """An RFC 9457 answer; `title` is a code, `detail` never echoes request content."""
    content: dict[str, Any] = {"type": PROBLEM_TYPE, "title": title, "status": status}
    if detail:
        content["detail"] = detail
    content.update(extra or {})
    all_headers = dict(headers or {})
    if status == 401:
        # RFC 6750: tell the client which scheme it should present.
        all_headers.setdefault("WWW-Authenticate", "Bearer")
    return JSONResponse(
        content, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=all_headers
    )


def unauthorized() -> Problem:
    """No token, or one that names no user."""
    return Problem(401, "auth.unauthorized")


def forbidden() -> Problem:
    """The user's roles do not cover the endpoint's lowest role."""
    return Problem(403, "auth.forbidden")


def not_found(title: str) -> Problem:
    """The object the path names does not exist."""
    return Problem(404, title)


def invalid_cursor() -> Problem:
    """`?cursor=` is not a cursor this API wrote."""
    return Problem(400, "pagination.invalid_cursor", detail="the cursor is not readable")


def invalid_request(detail: str, *, title: str = "request.invalid") -> Problem:
    """The body or the query does not parse or breaks a rule; nothing was written."""
    return Problem(422, title, detail=detail)
