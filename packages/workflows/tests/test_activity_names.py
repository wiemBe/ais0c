"""Activity names stay identical on the workflow caller and activity worker sides."""

from ais0c_activities import names as activity_names
from ais0c_workflows import names as workflow_names


def test_activity_names_match_the_workflow_names() -> None:
    activity_values = {
        getattr(activity_names, name)
        for name in dir(workflow_names)
        if name.isupper()
        and isinstance(getattr(workflow_names, name), str)
        and getattr(workflow_names, name) in workflow_names.ACTIVITY_NAMES
    }

    assert activity_values == workflow_names.ACTIVITY_NAMES
    assert activity_names.SKILL_TELEMETRY == workflow_names.SKILL_TELEMETRY
    assert workflow_names.SKILL_TELEMETRY in workflow_names.ACTIVITY_NAMES
