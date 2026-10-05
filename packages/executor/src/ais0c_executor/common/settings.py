"""Shared identity and secret-directory settings for executor writes (T-33)."""

from typing import Final

# The only actor allowed to perform executor writes, in audit_log and agent_runs.
EXECUTOR_ID: Final = "action-executor"
EXECUTOR_SECRETS_DIR_ENV: Final = "AIS0C_EXECUTOR_SECRETS_DIR"
DEFAULT_SECRETS_DIR: Final = "/run/secrets"
