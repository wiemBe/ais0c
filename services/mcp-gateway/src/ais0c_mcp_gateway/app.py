"""The gateway's HTTP interface towards the agents (T-007 report, "T-011 için önerilen yapı").

    POST /v1/tool-calls  Authorization: Bearer <profile token>
                         body: ToolIntent -> 200 ToolResult (status ok, denied or error)
    GET  /v1/tools       Authorization: Bearer <profile token>
                         -> the profile's tools as agents see them (agents ToolsetProfile)
    GET  /healthz        liveness; no token

There is no other endpoint: no generic MCP endpoint and no profile in the path or the body.
The profile comes only from the token (T-007 report, section 2). Client addresses and proxy
headers are not used; the network decides who reaches the gateway (architecture §13.4).

A denial is a ToolResult, not an HTTP error. HTTP errors (application/problem+json, with a
machine-readable `title`) mean no ToolResult can be given: an unknown token (401), a request
that is not a ToolIntent (422, `gateway.invalid_intent`) or whose `run_id` names no recorded
agent run (422, `gateway.unknown_run`), a body over 256 KiB (413) or an unreachable database
(503). The agent run comes only from the ToolIntent's `run_id` (contracts v0.2, T-19); no
request header names or changes it.
"""

from typing import Final

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from ais0c_contracts import SHORT_TEXT_MAX_LENGTH, ToolIntent
from ais0c_mcp_gateway.auth import ProfileAuthenticator
from ais0c_mcp_gateway.pipeline import Gateway, StorageUnavailableError, UnknownRunError
from ais0c_mcp_gateway.upstream import one_line

MAX_BODY_BYTES: Final = 256 * 1024
_PROBLEM_JSON: Final = "application/problem+json"


def create_app(gateway: Gateway, authenticator: ProfileAuthenticator) -> FastAPI:
    app = FastAPI(title="ais0c MCP Policy Gateway", docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/v1/tool-calls")
    async def tool_call(request: Request) -> Response:
        profile = authenticator.authenticate(request.headers.get("authorization"))
        if profile is None or profile not in gateway.registry.profiles:
            return problem(401, "gateway.unauthorized")
        body = await _read_body(request)
        if body is None:
            return problem(413, "gateway.body_too_large")
        try:
            intent = ToolIntent.model_validate_json(body)
        except ValidationError as error:
            return problem(422, "gateway.invalid_intent", _describe(error))
        try:
            result = await gateway.call(profile, intent)
        except UnknownRunError:
            return problem(422, "gateway.unknown_run", "no agent run with this run_id is recorded")
        except StorageUnavailableError:
            return problem(503, "gateway.storage_unavailable")
        return JSONResponse(result.model_dump(mode="json"))

    @app.get("/v1/tools")
    async def tools(request: Request) -> Response:
        profile = authenticator.authenticate(request.headers.get("authorization"))
        if profile is None or profile not in gateway.registry.profiles:
            return problem(401, "gateway.unauthorized")
        return JSONResponse(gateway.registry.profiles[profile].tool_list())

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def problem(status: int, title: str, detail: str | None = None) -> JSONResponse:
    """An RFC 9457 problem; `title` is a code, `detail` never echoes request content."""
    content: dict[str, str | int] = {"type": "about:blank", "title": title, "status": status}
    if detail:
        content["detail"] = detail
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse(content, status_code=status, media_type=_PROBLEM_JSON, headers=headers)


async def _read_body(request: Request) -> bytes | None:
    """The body, or None when it is longer than MAX_BODY_BYTES."""
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
        return None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            return None
    return bytes(body)


def _describe(error: ValidationError) -> str:
    """Field errors without the rejected values."""
    parts = [
        f"{'.'.join(str(part) for part in item['loc']) or 'body'}: {item['msg']}"
        for item in error.errors(include_url=False, include_input=False, include_context=False)
    ]
    return one_line("; ".join(parts), SHORT_TEXT_MAX_LENGTH)
