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
    }
    assert workflow_names.ACTIVITY_NAMES == registered


def test_case_ids_are_built_the_same_way() -> None:
    assert activity_names.case_workflow_id(12345) == "case-12345"
    assert workflow_names.case_workflow_id(12345) == "case-12345"


def test_signals_have_the_names_of_the_task() -> None:
    assert (workflow_names.OFFENSE_UPDATED, workflow_names.OFFENSE_CLOSED) == (
        "offense_updated",
        "offense_closed",
    )
