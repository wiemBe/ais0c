"""The activity that sends alert e-mails (architecture §9, "E-posta bildirimi"; D-22).

`send_email` runs the executor's `EmailSender` (`ais0c_executor.email`). The sender decides
whether the alert goes out and takes the recipients from the `operators` list. It refuses
addresses outside the allowed domains, checks the kill switch right before sending and records
the outcome in `notifications` and `audit_log`. The e-mail goes to the company's SMTP relay
directly: the relay is not a security product, so no gateway is involved (AGENTS.md hard
rule 1), and only the executor writes (hard rule 2).

The activity's name is not in `ais0c_activities.names`: that list must match the activities
the workflows call (services/worker/tests/test_names.py), and no workflow sends e-mail before
T-026.
"""

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ais0c_activities.db import SessionFactory
from ais0c_activities.gateway import utc_now
from ais0c_activities.runtime import RuntimeConfigError
from ais0c_executor.common import KillSwitch
from ais0c_executor.email import (
    EmailOutcome,
    EmailRequest,
    EmailSender,
    EmailTransport,
    InvalidEmail,
    SmtpSettings,
    SmtpTransport,
    TlsMode,
)
from ais0c_storage import create_engine, create_session_factory, database_url

SEND_EMAIL: Final = "send_email"
SMTP_HOST_ENV: Final = "AIS0C_SMTP_HOST"
SMTP_PORT_ENV: Final = "AIS0C_SMTP_PORT"
SMTP_TLS_ENV: Final = "AIS0C_SMTP_TLS"
SMTP_FROM_ENV: Final = "AIS0C_SMTP_FROM"
SMTP_USERNAME_ENV: Final = "AIS0C_SMTP_USERNAME"
SMTP_CA_FILE_ENV: Final = "AIS0C_SMTP_CA_FILE"
SMTP_TIMEOUT_ENV: Final = "AIS0C_SMTP_TIMEOUT"
# Where the executor's secrets are: here `smtp-password`, when the relay wants a login.
EXECUTOR_SECRETS_DIR_ENV: Final = "AIS0C_EXECUTOR_SECRETS_DIR"
DEFAULT_SECRETS_DIR: Final = "/run/secrets"
SMTP_PASSWORD_FILE: Final = "smtp-password"  # noqa: S105 - a file name, not a password


class EmailActivities:
    """The e-mail activity, with the relay it sends through."""

    def __init__(
        self,
        *,
        sessions: SessionFactory,
        transport: EmailTransport,
        kill_switch: KillSwitch | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sender = EmailSender(
            sessions=sessions, transport=transport, kill_switch=kill_switch, clock=clock
        )

    def activities(self) -> list[Callable[..., object]]:
        return [self.send_email]

    @activity.defn(name=SEND_EMAIL)
    async def send_email(self, request: EmailRequest) -> EmailOutcome:
        """Send the alert of `request` once (D-22).

        Returns the outcome: sent, sent by an earlier attempt, not needed, refused for a
        recipient outside the allowed domains, held back by the kill switch, or refused by the
        relay. An e-mail that cannot be built fails without a retry (`InvalidEmail`); a relay
        failure a retry may get past fails the attempt, and Temporal retries it.
        """
        try:
            return await self._sender.send_alert(request)
        except InvalidEmail as error:
            raise ApplicationError(str(error), type="InvalidEmail", non_retryable=True) from None


@dataclass(frozen=True)
class EmailRuntime:
    """The e-mail activity and what it was built from."""

    sessions: SessionFactory
    settings: SmtpSettings
    activities: EmailActivities
    engine: AsyncEngine | None = None
    """The database engine behind `sessions`, when the runtime created it."""

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()


def load_smtp_settings(environ: Mapping[str, str] | None = None) -> SmtpSettings:
    """The relay's settings from `environ` (default `os.environ`).

    | Variable | Meaning | Default |
    |---|---|---|
    | `AIS0C_SMTP_HOST` | The relay's host name or address | none |
    | `AIS0C_SMTP_PORT` | Its port | 587, 465 or 25, by `AIS0C_SMTP_TLS` |
    | `AIS0C_SMTP_TLS` | `starttls`, `implicit` (TLS from the first byte) or `none` | `starttls` |
    | `AIS0C_SMTP_FROM` | The sender address, e.g. `ai-soc@example.com` | none |
    | `AIS0C_SMTP_USERNAME` | The relay login; its password is the secret file `smtp-password` | no login |
    | `AIS0C_SMTP_CA_FILE` | CA certificates (PEM) to check the relay against, instead of the system's | the system's |
    | `AIS0C_SMTP_TIMEOUT` | Seconds each network step may take | 30 |
    | `AIS0C_EXECUTOR_SECRETS_DIR` | Directory of `smtp-password` | `/run/secrets` |

    Raises `RuntimeConfigError` for a missing or invalid setting; without TLS there is no
    login.
    """
    env = os.environ if environ is None else environ
    values: dict[str, object] = {
        "host": _required(env, SMTP_HOST_ENV),
        "sender": _required(env, SMTP_FROM_ENV),
    }
    tls = env.get(SMTP_TLS_ENV, "").strip()
    if tls:
        if tls not in {mode.value for mode in TlsMode}:
            modes = ", ".join(mode.value for mode in TlsMode)
            raise RuntimeConfigError(f"{SMTP_TLS_ENV} must be one of: {modes}")
        values["tls"] = tls
    for name, field in ((SMTP_PORT_ENV, "port"), (SMTP_TIMEOUT_ENV, "timeout")):
        value = env.get(name, "").strip()
        if value:
            values[field] = _number(name, value)
    ca_file = env.get(SMTP_CA_FILE_ENV, "").strip()
    if ca_file:
        values["ca_file"] = ca_file
    username = env.get(SMTP_USERNAME_ENV, "").strip()
    if username:
        secrets_dir = Path(env.get(EXECUTOR_SECRETS_DIR_ENV, "").strip() or DEFAULT_SECRETS_DIR)
        values["username"] = username
        values["password"] = _read_password(secrets_dir / SMTP_PASSWORD_FILE)
    try:
        return SmtpSettings.model_validate(values)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'settings'}: {item['msg']}"
            for item in error.errors(include_url=False, include_input=False, include_context=False)
        )
        raise RuntimeConfigError(f"invalid SMTP settings: {problems}") from None


def load_email_runtime(environ: Mapping[str, str] | None = None) -> EmailRuntime:
    """The e-mail activity built from `environ` (default `os.environ`): the database
    (`AIS0C_DATABASE_URL`) and the relay (`load_smtp_settings`). Nothing is connected yet."""
    env = os.environ if environ is None else environ
    settings = load_smtp_settings(env)
    engine = create_engine(database_url(env))
    sessions = create_session_factory(engine)
    return EmailRuntime(
        sessions=sessions,
        settings=settings,
        activities=EmailActivities(sessions=sessions, transport=SmtpTransport(settings)),
        engine=engine,
    )


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise RuntimeConfigError(f"{name} is not set")
    return value


def _number(name: str, value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise RuntimeConfigError(f"{name} must be a number") from None
    return int(number) if number.is_integer() else number


def _read_password(path: Path) -> SecretStr:
    """The relay password from a secret file: one line, not empty."""
    try:
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
    except (OSError, UnicodeDecodeError):
        raise RuntimeConfigError(f"secret file {path.name} cannot be read") from None
    if not value or "\n" in value or "\r" in value:
        raise RuntimeConfigError(f"secret file {path.name} must hold one password on one line")
    return SecretStr(value)
