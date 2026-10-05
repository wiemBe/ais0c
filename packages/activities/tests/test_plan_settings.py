"""The plan budget in CaseSettings (T-044 criterion 5, decision T-41)."""

import pytest

from ais0c_activities import CaseSettings
from ais0c_contracts import Budget

PLAN_VARIABLES = ("AIS0C_PLAN_TOKENS", "AIS0C_PLAN_TOOL_CALLS", "AIS0C_PLAN_SECONDS")


def test_the_plan_budget_defaults_to_the_task_values() -> None:
    settings = CaseSettings.from_env({})

    assert settings == CaseSettings()
    assert (settings.plan_tokens, settings.plan_tool_calls, settings.plan_seconds) == (
        250000,
        40,
        480,
    )
    assert settings.plan_budget == Budget(tokens=250000, tool_calls=40, seconds=480)


def test_the_plan_budget_comes_from_the_environment() -> None:
    settings = CaseSettings.from_env(
        {
            "AIS0C_PLAN_TOKENS": "300000",
            "AIS0C_PLAN_TOOL_CALLS": " 50 ",
            "AIS0C_PLAN_SECONDS": "600",
        }
    )

    assert settings.plan_budget == Budget(tokens=300000, tool_calls=50, seconds=600)
    # The other settings keep their defaults.
    assert settings.max_concurrent_cases == CaseSettings().max_concurrent_cases


def test_an_unset_or_blank_plan_variable_keeps_its_default() -> None:
    settings = CaseSettings.from_env({"AIS0C_PLAN_TOKENS": "  ", "AIS0C_PLAN_SECONDS": "120"})

    assert settings.plan_budget == Budget(tokens=250000, tool_calls=40, seconds=120)


@pytest.mark.parametrize("value", ["0", "-40", "forty", "1.5", "1e5"])
@pytest.mark.parametrize("name", PLAN_VARIABLES)
def test_a_plan_value_that_is_not_a_positive_integer_is_rejected(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=f"{name} must be a positive integer"):
        CaseSettings.from_env({name: value})


def test_settings_reject_a_plan_budget_that_is_not_positive() -> None:
    with pytest.raises(ValueError, match="plan budget must be positive"):
        CaseSettings(plan_tokens=0)
    with pytest.raises(ValueError, match="plan budget must be positive"):
        CaseSettings(plan_tool_calls=-1)
    with pytest.raises(ValueError, match="plan budget must be positive"):
        CaseSettings(plan_seconds=0)
