"""Which evaluation an agent run belongs to, read from its run ID (decision T-29).

A chain agent's run ID is `<case_id>-<agent>-<evaluation_no>`, with `-retry` for the run that
retries one a model outage ended (D-33); `<agent>` is the agent's manifest ID, which is also the
run's `agent_id`. `agent_runs` has no evaluation column, so the case detail finds the current
evaluation's Verification, and the steps show each run's evaluation, from the ID.

The API does not import `ais0c_workflows` (criterion 8): the format is repeated here, and a test in
`tests/api/` checks it against `ais0c_workflows.names.agent_workflow_id`.
"""

from typing import Final

# `ais0c_workflows.names.agent_workflow_id(..., retry=True)` appends this.
RETRY_SUFFIX: Final = "-retry"


def evaluation_of(run_id: str, *, case_id: str, agent_id: str) -> int | None:
    """The evaluation number in `run_id`, or None when it is not a chain run of `case_id`.

    The platform's own runs (the executor's note run, the catalog sync) have other IDs and give
    None.
    """
    prefix = f"{case_id}-{agent_id}-"
    if not run_id.startswith(prefix):
        return None
    number = run_id.removeprefix(prefix).removesuffix(RETRY_SUFFIX)
    if not number.isascii() or not number.isdigit():
        return None
    return int(number)


def is_retry(run_id: str) -> bool:
    """True for the run that retried one a model outage ended (D-33)."""
    return run_id.endswith(RETRY_SUFFIX)
