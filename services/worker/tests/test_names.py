"""The names the workflows call match the names the activities register.

`ais0c_workflows` may not import `ais0c_activities`, so each package spells the names of the
`soc-case` task queue itself; this worker sees both.
"""

from ais0c_activities import names as activity_names
from ais0c_workflows import names as workflow_names


def string_constants(module: object) -> dict[str, str]:
    return {
        name: value
        for name, value in vars(module).items()
        if name.isupper() and isinstance(value, str)
    }


def test_every_shared_name_has_the_same_value() -> None:
    shared = string_constants(activity_names)
    assert shared, "no names found"
    for name, value in shared.items():
        assert string_constants(workflow_names).get(name) == value, name


def test_the_workflows_call_exactly_the_registered_activities() -> None:
    registered = set(string_constants(activity_names).values()) - {
        activity_names.CASE_TASK_QUEUE,
        activity_names.CASE_WORKFLOW,
        activity_names.GROUP_CASE_WORKFLOW,
        activity_names.GROUP_UPDATED,
    }
    assert workflow_names.ACTIVITY_NAMES == registered


def test_case_ids_are_built_the_same_way() -> None:
    assert activity_names.case_workflow_id(12345) == "case-12345"
    assert workflow_names.case_workflow_id(12345) == "case-12345"
    group_id = "G-0123456789ab-20261007T090000Z"
    assert activity_names.group_case_id(group_id) == f"group-{group_id}"
    assert workflow_names.group_case_id(group_id) == f"group-{group_id}"


def test_the_group_case_has_the_names_the_intake_starts_it_with() -> None:
    """T-027: `wake_group_cases` starts GroupCaseWorkflow by name and signals it."""
    assert activity_names.GROUP_CASE_WORKFLOW == workflow_names.GROUP_CASE_WORKFLOW
    assert activity_names.GROUP_UPDATED == workflow_names.GROUP_UPDATED == "group_updated"


def test_signals_have_the_names_of_the_task() -> None:
    assert (workflow_names.OFFENSE_UPDATED, workflow_names.OFFENSE_CLOSED) == (
        "offense_updated",
        "offense_closed",
    )
