"""Authentication and roles for the analyst API (T-28, T-63 (1), api.md).

Today only `dev` mode exists: the request carries `Authorization: Bearer <token>` and
`AIS0C_API_DEV_USERS_FILE` holds the users, each with the **sha256 of** the token, never the
token itself. The hash is compared in constant time, so a wrong token leaks nothing about how
much of the hash it matched. T-035 replaces this with OIDC.

Roles are nested: `admin` covers `hunter`, `hunter` covers `operator`. `ROLE_RANK` gives the
comparison; `Session.allows` answers it for one role.
"""

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ais0c_api.problems import forbidden, unauthorized

logger = logging.getLogger("ais0c.api")

# The prefix of `Authorization`; the rest is the token, as it is written in the dev stack.
BEARER_PREFIX = "Bearer "


class Role(StrEnum):
    """`api.md`'s three roles, least to most."""

    OPERATOR = "operator"
    HUNTER = "hunter"
    ADMIN = "admin"


# Every role covers itself and the ones below it.
ROLE_RANK: dict[Role, int] = {Role.OPERATOR: 0, Role.HUNTER: 1, Role.ADMIN: 2}


class AuthError(ValueError):
    """The users file is missing, unreadable or does not hold what a user needs."""


@dataclass(frozen=True)
class Session:
    """The authenticated user of one request."""

    subject: str
    display_name: str
    roles: frozenset[Role]

    @property
    def role_names(self) -> list[str]:
        """The roles, ordered from the most to the least, as the UI shows them."""
        return [role.value for role in sorted(self.roles, key=lambda r: -ROLE_RANK[r])]

    def allows(self, lowest: Role) -> bool:
        """True when any of the user's roles covers `lowest`."""
        return any(ROLE_RANK[held] >= ROLE_RANK[lowest] for held in self.roles)


@dataclass(frozen=True)
class DevUser:
    """One user of the dev users file: a token hash, an identity and roles."""

    token_sha256: str
    subject: str
    display_name: str
    roles: frozenset[Role]


def token_sha256(token: str) -> str:
    """The hash a dev users file stores for `token`: lowercase hex of its sha256."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DevAuthenticator:
    """Checks bearer tokens against a dev users file. One instance per process.

    The file is read once at start-up, so a request never touches the disk. A token that matches
    no user is `None`: the caller answers 401 without saying whether the file exists.
    """

    def __init__(self, users: list[DevUser]) -> None:
        if not users:
            raise AuthError("the dev users file names no user")
        # Longest first is not needed for correctness, only so the loop is uniform.
        self._users = sorted(users, key=lambda user: user.subject)
        self._digests = {user.token_sha256: user for user in self._users}

    @classmethod
    def from_file(cls, path: Path) -> "DevAuthenticator":
        """The authenticator of the users in the JSON file `path`.

        The file is `{"users": [{"token_sha256": ..., "subject": ..., "display_name": ...,
        "roles": [...]}]}`. A duplicate hash, an unknown role or a missing field is an
        `AuthError`: the service must not start on a file it only half understands.
        """
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise AuthError(f"dev users file {path} is missing") from None
        except (OSError, UnicodeDecodeError):
            raise AuthError(f"dev users file {path} cannot be read") from None
        except json.JSONDecodeError:
            raise AuthError(f"dev users file {path} is not valid JSON") from None
        if not isinstance(raw, dict) or not isinstance(raw.get("users"), list):
            raise AuthError("dev users file must hold a `users` list")
        seen: set[str] = set()
        users: list[DevUser] = []
        for item in raw["users"]:
            if not isinstance(item, dict):
                raise AuthError("every dev user must be an object")
            digest = _required(item, "token_sha256", path)
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise AuthError("token_sha256 must be a lowercase hex sha256")
            if digest in seen:
                raise AuthError("two dev users share one token hash")
            seen.add(digest)
            subject = _required(item, "subject", path)
            display_name = _required(item, "display_name", path)
            roles = item.get("roles")
            if not isinstance(roles, list) or not roles:
                raise AuthError(f"dev user {subject} has no role")
            parsed: set[Role] = set()
            for role in roles:
                try:
                    parsed.add(Role(role))
                except ValueError:
                    raise AuthError(f"dev user {subject} has an unknown role: {role!r}") from None
            users.append(
                DevUser(
                    token_sha256=digest,
                    subject=subject,
                    display_name=display_name,
                    roles=frozenset(parsed),
                )
            )
        return cls(users)

    def authenticate(self, authorization: str | None) -> Session | None:
        """The session `authorization` names, or None when it names none."""
        if not authorization or not authorization.startswith(BEARER_PREFIX):
            return None
        token = authorization[len(BEARER_PREFIX) :].strip()
        if not token:
            return None
        given = token_sha256(token)
        found: DevUser | None = None
        for digest, user in self._digests.items():
            # Constant time: every candidate is compared, and the loop does not stop early.
            if hmac.compare_digest(digest, given):
                found = user
        if found is None:
            return None
        return Session(subject=found.subject, display_name=found.display_name, roles=found.roles)

    def require(self, authorization: str | None, lowest: Role) -> Session:
        """The session, or the problem to answer: 401 without a user, 403 without the role."""
        session = self.authenticate(authorization)
        if session is None:
            raise unauthorized()
        if not session.allows(lowest):
            raise forbidden()
        return session


def _required(item: dict[str, object], field: str, path: Path) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise AuthError(f"dev users file {path.name}: every user needs a non-empty {field}")
    return value
