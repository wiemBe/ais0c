"""Profile tokens (T-011 criterion 1): every toolset profile has its own token.

The caller's profile comes from its bearer token and from nothing else: not from the path,
the body or any header the caller could set (T-007 report, section 2). An unknown or missing
token gets no profile.
"""

import hmac
from collections.abc import Mapping

from pydantic import SecretStr

from ais0c_mcp_gateway.settings import MIN_TOKEN_LENGTH, SettingsError


class ProfileAuthenticator:
    def __init__(self, tokens: Mapping[str, SecretStr]) -> None:
        """`tokens` maps each enabled profile to its token."""
        if not tokens:
            raise SettingsError("no profile has a token; every call would be refused")
        seen: set[str] = set()
        for token in tokens.values():
            value = token.get_secret_value()
            if len(value) < MIN_TOKEN_LENGTH:
                raise SettingsError(
                    f"profile tokens must be at least {MIN_TOKEN_LENGTH} characters"
                )
            if value in seen:
                raise SettingsError("two profiles share a token")
            seen.add(value)
        self._tokens = [
            (profile, token.get_secret_value().encode("utf-8")) for profile, token in tokens.items()
        ]

    @property
    def profiles(self) -> frozenset[str]:
        return frozenset(profile for profile, _ in self._tokens)

    def secrets(self) -> list[str]:
        """The token values, for log redaction."""
        return [token.decode("utf-8") for _, token in self._tokens]

    def authenticate(self, authorization: str | None) -> str | None:
        """The profile whose token is in `Authorization: Bearer <token>`, or None."""
        if authorization is None:
            return None
        scheme, _, credentials = authorization.partition(" ")
        if scheme.lower() != "bearer":
            return None
        presented = credentials.strip().encode("utf-8")
        found: str | None = None
        # Every token is compared, so the time taken does not reveal which one matched.
        for profile, token in self._tokens:
            if hmac.compare_digest(presented, token):
                found = profile
        return found
