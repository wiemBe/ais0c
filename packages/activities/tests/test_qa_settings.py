"""The QA sample rates in CaseSettings (T-026 criterion 7, decision T-42 (5))."""

import pytest

from ais0c_activities import CaseSettings

QA_VARIABLES = ("AIS0C_QA_SAMPLE_PERCENT", "AIS0C_QA_UNDEFINED_SAMPLE_PERCENT")


def test_the_rates_default_to_ten_and_thirty_percent() -> None:
    settings = CaseSettings.from_env({})

    assert (settings.qa_sample_percent, settings.qa_undefined_sample_percent) == (10, 30)


def test_the_rates_come_from_the_environment() -> None:
    settings = CaseSettings.from_env(
        {"AIS0C_QA_SAMPLE_PERCENT": " 0 ", "AIS0C_QA_UNDEFINED_SAMPLE_PERCENT": "100"}
    )

    assert (settings.qa_sample_percent, settings.qa_undefined_sample_percent) == (0, 100)


@pytest.mark.parametrize("value", ["-1", "101", "ten", "12.5"])
@pytest.mark.parametrize("name", QA_VARIABLES)
def test_a_rate_that_is_not_a_percentage_is_rejected(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=f"{name} must be a whole percentage from 0 to 100"):
        CaseSettings.from_env({name: value})


def test_settings_reject_a_rate_outside_zero_to_hundred() -> None:
    with pytest.raises(ValueError, match="percentages from 0 to 100"):
        CaseSettings(qa_sample_percent=101)
