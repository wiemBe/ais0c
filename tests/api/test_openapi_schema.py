"""Criterion 12: `services/api/openapi.json` is the schema of the code, not a copy of it.

The schema is generated from the routes and the models with

    uv run python -m ais0c_api.openapi services/api/openapi.json

and this test regenerates it and compares, so the checked-in file cannot drift. T-029 derives the
UI's TypeScript types from that file, so a stale schema would give the UI types that do not match
what the API answers.
"""

import json

from ais0c_api.openapi import SCHEMA_PATH, render, schema

# Every endpoint of T-028, with the method it is reached by. A missing one is a scope change the
# OpenAPI file has to record; an extra one is an endpoint the task did not ask for.
EXPECTED_ENDPOINTS = {
    ("get", "/api/v1/cases"),
    ("get", "/api/v1/cases/{case_id}"),
    ("get", "/api/v1/cases/{case_id}/steps"),
    ("get", "/api/v1/cases/{case_id}/feedback"),
    ("post", "/api/v1/cases/{case_id}/feedback"),
    ("get", "/api/v1/qa"),
    ("post", "/api/v1/qa/{item_id}/resolve"),
    ("get", "/api/v1/groups"),
    ("get", "/api/v1/groups/{group_id}"),
    ("get", "/api/v1/catalog/rules"),
    # Beyond the table of api.md: a read of one catalog entry, for a deep link.
    ("get", "/api/v1/catalog/rules/{rule_id}"),
    ("put", "/api/v1/catalog/rules/{rule_id}"),
    ("post", "/api/v1/catalog/rules/{rule_id}/accept-draft"),
    ("get", "/api/v1/catalog/log-sources"),
    # Beyond the table of api.md, like the rule's.
    ("get", "/api/v1/catalog/log-sources/{log_source_id}"),
    ("put", "/api/v1/catalog/log-sources/{log_source_id}"),
    ("post", "/api/v1/catalog/sync"),
    ("get", "/api/v1/critical-assets"),
    ("post", "/api/v1/critical-assets"),
    ("delete", "/api/v1/critical-assets/{asset_id}"),
    ("get", "/api/v1/notification-recipients"),
    ("put", "/api/v1/notification-recipients/{list_name}"),
    ("get", "/api/v1/notification-routes"),
    ("put", "/api/v1/notification-routes"),
    ("get", "/api/v1/metrics/sla"),
    ("get", "/api/v1/admin/platform-flags"),
    ("put", "/api/v1/admin/platform-flags/{name}"),
    ("get", "/api/v1/me"),
    ("get", "/api/v1/health"),
}


def test_the_checked_in_schema_matches_the_code() -> None:
    assert SCHEMA_PATH.is_file(), (
        "run `uv run python -m ais0c_api.openapi services/api/openapi.json`"
    )
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render(schema()), (
        "services/api/openapi.json is out of date; regenerate it with "
        "`uv run python -m ais0c_api.openapi services/api/openapi.json`"
    )


def test_the_schema_names_the_endpoints_of_this_task() -> None:
    document = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    given = {
        (method, path)
        for path, operations in document["paths"].items()
        for method in operations
        if method in {"get", "post", "put", "delete", "patch"}
    }

    assert given == set(EXPECTED_ENDPOINTS)


def test_the_schema_carries_no_endpoint_that_acts_on_a_security_product() -> None:
    """D-02, D-19: nothing here closes an offense, changes a rule or runs an action."""
    forbidden = ("offense", "action", "tuning", "hunt", "actor", "isolate", "contain")
    document = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    for path in document["paths"]:
        for word in forbidden:
            assert word not in path, f"{path} looks like an action endpoint"


def test_the_schema_says_it_serves_the_ui_with_the_version_prefix() -> None:
    document = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert document["info"]["title"] == "ais0c analyst API"
    assert all(path.startswith("/api/v1") for path in document["paths"])
    # The description is English and names the two rules the UI relies on.
    description = document["info"]["description"]
    assert "RFC 9457" in description
    assert "ISO 8601" in description


def test_no_schema_description_holds_turkish_user_facing_text() -> None:
    """The API produces no user-facing text: the UI builds it from the problem's `title`.

    Only the description and the endpoint summaries are checked; a `summary_tr` in a response
    model is an agent's own text and is expected.
    """
    document = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert document["info"]["description"].isascii()
    # A section mark in a doc reference is not user-facing text; the rest must be ASCII.
    for path, operations in document["paths"].items():
        for method, operation in operations.items():
            for field in ("summary", "description"):
                text = operation.get(field)
                if isinstance(text, str):
                    assert text.replace("§", "").isascii(), (
                        f"{method.upper()} {path} {field} is not ASCII"
                    )
