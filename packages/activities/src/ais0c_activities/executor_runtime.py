"""What the executor worker runs with: the note and e-mail activities, nothing else (T-045).

The executor's activities run in their own process on the `soc-executor` task queue (T-33 (1)),
so their runtime is built apart from the case worker's. It reads only the executor's own
secrets: `AIS0C_EXECUTOR_SECRETS_DIR` with the note profile's gateway token and the SMTP
password, and the `AIS0C_SMTP_*` settings. It holds no agent token, reads no model registry and
talks to no model; the case worker, the other way around, holds no executor secret.

`load_note_runtime` asks the gateway for the note profile's tools, so the gateway must be up; a
missing token or SMTP setting, or an unreachable gateway, is a `RuntimeConfigError`, and the
worker process stops with it rather than run with less.
"""

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ais0c_activities.email import EmailRuntime, load_email_runtime
from ais0c_activities.note import NoteRuntime, load_note_runtime


@dataclass(frozen=True)
class ExecutorRuntime:
    """The two activities of the `soc-executor` task queue, ready to register on a worker."""

    note: NoteRuntime
    email: EmailRuntime

    def activities(self) -> list[Callable[..., object]]:
        """`write_offense_note` and `send_email`, and nothing else (criterion 1)."""
        return [self.note.activities.write_offense_note, self.email.activities.send_email]

    async def close(self) -> None:
        await self.note.close()
        await self.email.close()


async def load_executor_runtime(environ: Mapping[str, str] | None = None) -> ExecutorRuntime:
    """Build the runtime from `environ` (default `os.environ`).

    | Variable | Meaning | Default |
    |---|---|---|
    | `AIS0C_DATABASE_URL` | Application database | none |
    | `AIS0C_GATEWAY_URL` | MCP Policy Gateway | none |
    | `AIS0C_EXECUTOR_SECRETS_DIR` | Directory of `gateway-token-qradar-note-write` and `smtp-password` | `/run/secrets` |
    | `AIS0C_SMTP_*` | The relay (`load_smtp_settings`) | none |
    """
    env = os.environ if environ is None else environ
    note = await load_note_runtime(env)
    try:
        email = load_email_runtime(env)
    except BaseException:
        await note.close()
        raise
    return ExecutorRuntime(note=note, email=email)
