"""The FastAPI application of the analyst API (T-028).

`build_app` puts the routes, the error handlers and the three things a request needs (a database
session factory, an authenticator, a Temporal Schedule trigger) on `app.state`, so a test can
build the same app with its own database and its own `DevAuthenticator`.

Errors are RFC 9457 problems (`ais0c_api.problems`): a `Problem` raised in a route becomes its
answer, a body or query that does not parse becomes a 422 with the field paths, an unknown path a
404, and anything else a 500 that says nothing about the failure. A database that cannot be
reached is a 503.

The running service does not serve its OpenAPI schema: the schema is the checked-in
`services/api/openapi.json` (`ais0c_api.openapi`), and an unauthenticated route that describes
every endpoint is one more thing to protect.
"""

import logging
from typing import Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from ais0c_api.auth import DevAuthenticator
from ais0c_api.dependencies import SessionFactory
from ais0c_api.problems import Problem, problem_response
from ais0c_api.routers import api_router
from ais0c_api.temporal import ScheduleTrigger

logger = logging.getLogger("ais0c.api")

# The `info` block of the generated schema (`services/api/openapi.json`).
OPENAPI_TITLE: Final = "ais0c analyst API"
OPENAPI_VERSION: Final = "0.1.0"

API_DESCRIPTION: Final = """\
The analyst UI's API. The UI talks only to this service.

Errors are RFC 9457 problems whose `title` is a machine-readable code (`catalog.rule_not_found`,
`qa.already_resolved`); the Turkish message the user reads is built by the UI from that code.
Times are ISO 8601 in UTC. Lists are cursor-paged with `?cursor=&limit=`.
"""


def build_app(
    *,
    sessions: SessionFactory,
    authenticator: DevAuthenticator,
    schedule_trigger: ScheduleTrigger,
    offense_url_template: str | None = None,
) -> FastAPI:
    """The API as an ASGI app.

    Nothing is connected here: the database engine, the users file and Temporal's client are the
    caller's, and `ais0c_api.service.build_service` reads them from the environment.
    """
    app = FastAPI(
        title=OPENAPI_TITLE,
        version=OPENAPI_VERSION,
        description=API_DESCRIPTION,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.sessions = sessions
    app.state.authenticator = authenticator
    app.state.schedule_trigger = schedule_trigger
    app.state.offense_url_template = offense_url_template

    app.include_router(api_router)
    _add_error_handlers(app)
    return app


def _add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(Problem)
    async def handle_problem(_request: Request, error: Problem) -> JSONResponse:
        return error.response()

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_request: Request, error: RequestValidationError) -> JSONResponse:
        return problem_response(
            422,
            "request.invalid",
            detail="the request does not match the API contract",
            extra={
                "errors": [
                    {
                        "field": ".".join(str(part) for part in item["loc"]) or "body",
                        "message": item["msg"],
                    }
                    for item in error.errors()
                ]
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(_request: Request, error: StarletteHTTPException) -> JSONResponse:
        # A path that is not a route, a method the route does not take.
        code = {404: "request.not_found", 405: "request.method_not_allowed"}.get(
            error.status_code, "request.error"
        )
        # A 405 keeps its `Allow` header (RFC 9110).
        return problem_response(
            error.status_code, code, headers=dict(error.headers) if error.headers else None
        )

    @app.exception_handler(OperationalError)
    async def handle_unavailable(_request: Request, error: OperationalError) -> JSONResponse:
        logger.warning("the database is unavailable: %s", type(error).__name__)
        return problem_response(503, "storage.unavailable")

    @app.exception_handler(InterfaceError)
    async def handle_interface(_request: Request, error: InterfaceError) -> JSONResponse:
        logger.warning("the database connection failed: %s", type(error).__name__)
        return problem_response(503, "storage.unavailable")

    @app.exception_handler(Exception)
    async def handle_unexpected(_request: Request, error: Exception) -> JSONResponse:
        logger.exception("unhandled failure: %s", type(error).__name__)
        return problem_response(500, "internal.error")
