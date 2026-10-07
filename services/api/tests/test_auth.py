"""Criterion 2: authentication and roles (T-63 (1), api.md).

- `AIS0C_API_AUTH` may only be `dev` today; without it, or with an unknown value, the API does not
  start (criterion 2, `test_service_and_problems.py`);
- `dev` mode reads `Authorization: Bearer <token>` from `AIS0C_API_DEV_USERS_FILE`, which holds the
  sha256 of the token and never the token itself; the comparison is constant time;
- roles are nested, `admin` ⊇ `hunter` ⊇ `operator`, and each endpoint's lowest role is the one
  api.md's table names;
- `/me` returns the session's subject, display name and roles; `/health` needs no token and says
  nothing but liveness.

Negative cases: no token 401, a wrong token 401, an operator on an admin endpoint 403, a hunter on
an admin endpoint 403.
"""

from pathlib import Path

import pytest
from api_support import (
    ADMIN_TOKEN,
    CASE_ID,
    OPERATOR_TOKEN,
    DevUsersFile,
    Harness,
    add_catalog,
    decided_case,
)

from ais0c_api.auth import (
    AuthError,
    DevAuthenticator,
    DevUser,
    Role,
    Session,
    token_sha256,
)
from ais0c_api.temporal import KNOWLEDGE_SYNC_SCHEDULE_ID

pytestmark = pytest.mark.anyio

ADMIN_ONLY = [
    ("put", "/catalog/rules/100201", {"mode": "analyze", "has_automated_action": False}),
    ("post", "/catalog/rules/100201/accept-draft", None),
    ("put", "/catalog/log-sources/2001", {"in_scope": True}),
    ("post", "/catalog/sync", None),
    (
        "post",
        "/critical-assets",
        {"kind": "ip", "value": "192.0.2.1", "label": "GW", "level": "high"},
    ),
    ("delete", "/critical-assets/00000000-0000-7000-8000-000000000000", None),
    ("put", "/notification-recipients/operators", {"emails": []}),
    ("put", "/notification-routes", {"routes": []}),
    ("put", "/admin/platform-flags/writes_enabled", {"enabled": True, "reason": "canary"}),
    ("get", "/changes", None),
    ("get", "/changes/00000000-0000-7000-8000-000000000000", None),
    ("post", "/changes/00000000-0000-7000-8000-000000000000/approve", None),
    ("post", "/changes/00000000-0000-7000-8000-000000000000/reject", {}),
    ("post", "/changes/00000000-0000-7000-8000-000000000000/withdraw", None),
]

OPERATOR_READS = [
    ("get", "/cases", None),
    ("get", f"/cases/{CASE_ID}", None),
    ("get", f"/cases/{CASE_ID}/steps", None),
    ("get", "/qa", None),
    ("get", "/groups", None),
    ("get", "/catalog/rules", None),
    ("get", "/catalog/log-sources", None),
    ("get", "/critical-assets", None),
    ("get", "/admin/platform-flags", None),
    ("get", "/me", None),
    ("get", "/metrics/sla", None),
]


async def call(api: Harness, method: str, path: str, body: object, as_role: str) -> int:
    """The status of one request as the given user; the path needs no rows to exist."""
    if method == "get":
        response = await api.get(path, as_role=as_role)
    elif method == "post":
        response = await api.post(path, body, as_role=as_role)
    elif method == "put":
        response = await api.put(path, body, as_role=as_role)
    else:
        response = await api.delete(path, as_role=as_role)
    return response.status_code


# --- the dev users file -------------------------------------------------------------------------


def test_the_file_holds_the_hash_of_the_token_and_never_the_token(tmp_path: Path) -> None:
    users = DevUsersFile.write(tmp_path)

    content = users.path.read_text(encoding="utf-8")

    assert users.operator not in content
    assert users.hunter not in content
    assert users.admin not in content
    assert token_sha256(users.operator) in content
    # The token itself never had to be written, only its hash.
    assert len(token_sha256(users.operator)) == 64


def test_the_file_is_read_once_and_a_user_is_found_by_its_token(tmp_path: Path) -> None:
    users = DevUsersFile.write(tmp_path)

    authenticator = DevAuthenticator.from_file(users.path)

    session = authenticator.authenticate(f"Bearer {users.operator}")
    assert session is not None
    assert (session.subject, session.display_name, session.roles) == (
        "synthetic-operator",
        "Synthetic Operator",
        frozenset({Role.OPERATOR}),
    )
    # The file is gone: an in-flight request still authenticates, because it was read once.
    users.path.unlink()
    assert authenticator.authenticate(f"Bearer {users.operator}") == session


@pytest.mark.parametrize(
    "header",
    [None, "", "Basic dXNlcjpwYXNz", "Bearer", "Bearer ", "bearer " + OPERATOR_TOKEN],
    ids=["none", "empty", "basic", "no-token", "blank-token", "lowercase-scheme"],
)
def test_a_header_that_is_not_a_bearer_token_is_no_user(tmp_path: Path, header: str | None) -> None:
    authenticator = DevAuthenticator.from_file(DevUsersFile.write(tmp_path).path)

    assert authenticator.authenticate(header) is None


def test_a_token_no_user_has_is_no_user(tmp_path: Path) -> None:
    authenticator = DevAuthenticator.from_file(DevUsersFile.write(tmp_path).path)

    assert authenticator.authenticate("Bearer not-a-known-token") is None
    assert authenticator.authenticate(f"Bearer {ADMIN_TOKEN[:-1]}x") is None


def test_a_token_with_anything_around_it_is_a_different_token(tmp_path: Path) -> None:
    """The digest covers the whole token, so trailing text is a different one."""
    users = DevUsersFile.write(tmp_path)
    authenticator = DevAuthenticator.from_file(users.path)

    assert authenticator.authenticate(f"Bearer {users.operator}") is not None
    assert authenticator.authenticate(f"Bearer {users.operator}extra") is None
    assert authenticator.authenticate(f"Bearer {users.operator[:-1]}") is None


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{", "not valid JSON"),
        ('{"users": {}}', "must hold a `users` list"),
        (
            '{"users": [{"token_sha256": "0", "subject": "s", "display_name": "S", "roles": ["admin"]}]}',
            "lowercase hex sha256",
        ),
        (
            '{"users": [{"subject": "s", "display_name": "S", "roles": ["admin"]}]}',
            "non-empty token_sha256",
        ),
        (
            '{"users": [{"token_sha256": "0000000000000000000000000000000000000000000000000000000000000000",'
            ' "subject": "s", "display_name": "S", "roles": []}]}',
            "has no role",
        ),
        (
            '{"users": [{"token_sha256": "0000000000000000000000000000000000000000000000000000000000000000",'
            ' "subject": "s", "display_name": "S", "roles": ["root"]}]}',
            "unknown role",
        ),
        (
            '{"users": [{"token_sha256": "0000000000000000000000000000000000000000000000000000000000000000",'
            ' "subject": "s", "display_name": "S", "roles": ["admin"]},'
            ' {"token_sha256": "0000000000000000000000000000000000000000000000000000000000000000",'
            ' "subject": "t", "display_name": "T", "roles": ["admin"]}]}',
            "share one token hash",
        ),
        ('{"users": []}', "names no user"),
    ],
    ids=[
        "broken",
        "no-list",
        "short-hash",
        "no-hash",
        "no-role",
        "unknown-role",
        "duplicate-hash",
        "empty",
    ],
)
def test_a_users_file_the_api_cannot_understand_is_refused(
    tmp_path: Path, content: str, message: str
) -> None:
    path = tmp_path / "users.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(AuthError, match=message):
        DevAuthenticator.from_file(path)


def test_a_missing_users_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AuthError, match="is missing"):
        DevAuthenticator.from_file(tmp_path / "nope.json")


def test_a_file_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "users.json"
    path.write_bytes(b"\xff\xfe\x00broken")

    with pytest.raises(AuthError, match="cannot be read"):
        DevAuthenticator.from_file(path)


def test_roles_are_nested() -> None:
    def session(*roles: Role) -> Session:
        return Session(subject="s", display_name="S", roles=frozenset(roles))

    assert session(Role.ADMIN).allows(Role.OPERATOR)
    assert session(Role.ADMIN).allows(Role.HUNTER)
    assert session(Role.HUNTER).allows(Role.OPERATOR)
    assert not session(Role.OPERATOR).allows(Role.HUNTER)
    assert not session(Role.HUNTER).allows(Role.ADMIN)


# --- the endpoints' lowest roles -----------------------------------------------------------------


async def test_health_needs_no_token_and_says_only_that_it_is_alive(api: Harness) -> None:
    response = await api.raw("GET", "/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    # No version, no database detail, no settings.
    body = response.text
    for word in ("version", "database", "auth", "temporal", "0.1.0"):
        assert word not in body.lower()


@pytest.mark.parametrize(
    ("method", "path", "body"), OPERATOR_READS, ids=lambda value: str(value)[-40:]
)
async def test_an_operator_may_read_everything_an_operator_may(
    api: Harness, method: str, path: str, body: object
) -> None:
    await decided_case(api.sessions)
    await add_catalog(api.sessions)

    assert await call(api, method, path, body, "operator") == 200


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ONLY, ids=lambda value: str(value)[-40:])
async def test_an_operator_may_not_change_anything_that_needs_an_admin(
    api: Harness, method: str, path: str, body: object
) -> None:
    await decided_case(api.sessions)
    await add_catalog(api.sessions)

    assert await call(api, method, path, body, "operator") == 403


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ONLY, ids=lambda value: str(value)[-40:])
async def test_a_hunter_may_not_reach_an_admin_endpoint_either(
    api: Harness, method: str, path: str, body: object
) -> None:
    await decided_case(api.sessions)
    await add_catalog(api.sessions)

    assert await call(api, method, path, body, "hunter") == 403


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ONLY, ids=lambda value: str(value)[-40:])
async def test_an_admin_may_reach_every_admin_endpoint(
    api: Harness, method: str, path: str, body: object
) -> None:
    """An admin passes the role check; whether the request succeeds is the endpoint's business."""
    await decided_case(api.sessions)
    await add_catalog(api.sessions)
    await api.seed("INSERT INTO allowed_email_domains (domain) VALUES ('example.com')")

    assert await call(api, method, path, body, "admin") != 403


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/cases"), ("get", f"/cases/{CASE_ID}"), ("get", "/qa"), ("get", "/me")],
    ids=["cases", "case", "qa", "me"],
)
async def test_no_token_is_401(api: Harness, method: str, path: str) -> None:
    response = await api.raw(method, f"/api/v1{path}")

    assert response.status_code == 401
    assert response.json()["title"] == "auth.unauthorized"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_a_wrong_token_is_401(api: Harness) -> None:
    response = await api.raw(
        "GET", "/api/v1/cases", headers={"Authorization": "Bearer not-the-token"}
    )

    assert response.status_code == 401
    assert response.json()["title"] == "auth.unauthorized"


async def test_me_returns_the_session_of_the_token(api: Harness) -> None:
    response = await api.get("/me", as_role="hunter")

    assert response.json() == {
        "subject": "synthetic-hunter",
        "display_name": "Synthetic Hunter",
        "roles": ["hunter"],
    }


def test_a_session_with_several_roles_lists_them_most_privileged_first() -> None:
    authenticator = DevAuthenticator(
        [
            DevUser(
                token_sha256=token_sha256(ADMIN_TOKEN),
                subject="synthetic-admin",
                display_name="Synthetic Admin",
                roles=frozenset({Role.OPERATOR, Role.ADMIN}),
            )
        ]
    )

    session = authenticator.authenticate(f"Bearer {ADMIN_TOKEN}")

    assert session is not None
    assert session.ranked_roles == ["admin", "operator"]
    assert session.allows(Role.OPERATOR)


async def test_the_catalog_sync_needs_the_schedule_id_the_workflows_package_names(
    api: Harness,
) -> None:
    """Criterion 8's other half: the API's constant is the one Temporal knows."""
    from ais0c_workflows.names import KNOWLEDGE_SYNC_SCHEDULE_ID as WORKFLOWS_ID

    assert KNOWLEDGE_SYNC_SCHEDULE_ID == WORKFLOWS_ID

    response = await api.post("/catalog/sync", as_role="admin")

    assert response.status_code == 202
    assert api.trigger.triggered == [KNOWLEDGE_SYNC_SCHEDULE_ID]
