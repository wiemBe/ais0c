"""The OpenAPI schema of the analyst API, and the command that writes it (criterion 12).

`services/api/openapi.json` is generated from the routes and the models in this package:

    uv run python -m ais0c_api.openapi > services/api/openapi.json

The command takes no database and no settings: the schema is built from the routes alone. A test
in `tests/api/` regenerates it and compares, so the file cannot drift from the code, and T-029
derives the UI's TypeScript types from it with `openapi-typescript`.

FastAPI describes every 422 as its own `HTTPValidationError`; this API answers every error with an
RFC 9457 problem instead (`ais0c_api.problems`). The schema says so: each operation's `default`
response is a `Problem` in `application/problem+json`, and FastAPI's 422 entries are dropped, so
the UI's error type is the one the API sends.
"""

import json
import sys
from pathlib import Path
from typing import Any, Final

from sqlalchemy.ext.asyncio import async_sessionmaker

from ais0c_api.app import build_app
from ais0c_api.auth import DevAuthenticator, DevUser, Role
from ais0c_api.models import Problem
from ais0c_api.problems import PROBLEM_MEDIA_TYPE

# The checked-in schema, next to this package's parent (services/api).
SCHEMA_PATH: Final = Path(__file__).resolve().parents[2] / "openapi.json"


def schema() -> dict[str, Any]:
    """The schema of the routes, without touching a database or Temporal.

    The app is built with a session factory that has no engine and a trigger that raises: only
    the routes and their models shape the schema, so nothing connects while it is generated.
    """
    app = build_app(
        sessions=async_sessionmaker(),
        authenticator=DevAuthenticator(
            [
                DevUser(
                    token_sha256="0" * 64,
                    subject="schema",
                    display_name="schema",
                    roles=frozenset({Role.OPERATOR}),
                )
            ]
        ),
        schedule_trigger=_NoSchedule(),
    )
    document: dict[str, Any] = app.openapi()
    return with_problems(document)


# FastAPI's own 422 body, which this API never sends.
_FASTAPI_ERRORS: Final = ("HTTPValidationError", "ValidationError")

_PROBLEM_RESPONSE: Final = {
    "description": (
        "An RFC 9457 problem. `title` is a machine-readable code (`catalog.rule_not_found`); the "
        "UI builds the message from it."
    ),
    "content": {PROBLEM_MEDIA_TYPE: {"schema": {"$ref": "#/components/schemas/Problem"}}},
}


def with_problems(document: dict[str, Any]) -> dict[str, Any]:
    """`document` with every error answer described as the `Problem` the API sends."""
    schemas: dict[str, Any] = document.setdefault("components", {}).setdefault("schemas", {})
    problem = Problem.model_json_schema(ref_template="#/components/schemas/{model}")
    schemas.update(problem.pop("$defs", {}))
    schemas["Problem"] = problem
    for name in _FASTAPI_ERRORS:
        schemas.pop(name, None)
    for operations in document.get("paths", {}).values():
        for operation in operations.values():
            responses: dict[str, Any] = operation.setdefault("responses", {})
            responses.pop("422", None)
            responses["default"] = _PROBLEM_RESPONSE
    return document


def render(document: dict[str, Any]) -> str:
    """The schema as the file holds it: two-space indent, sorted keys, one trailing newline."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(path: Path = SCHEMA_PATH) -> str:
    """Write the schema to `path` and return what was written."""
    text = render(schema())
    path.write_text(text, encoding="utf-8")
    return text


def main(argv: list[str] | None = None) -> int:
    """Write the schema to stdout, or to the first argument as a path."""
    args = sys.argv[1:] if argv is None else argv
    if args:
        write(Path(args[0]))
        return 0
    sys.stdout.write(render(schema()))
    return 0


class _NoSchedule:
    """A trigger that is never called: building the schema talks to no Temporal."""

    async def trigger(self, schedule_id: str) -> None:
        raise AssertionError(f"the schema build must not trigger {schedule_id}")


if __name__ == "__main__":
    sys.exit(main())
