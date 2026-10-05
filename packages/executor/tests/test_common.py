"""What notes and e-mails share (T-036 criteria 5 and 6; T-33 (5)).

The Turkish label table and the identity checks live in `ais0c_executor.common` and nowhere
else, so a note and the e-mail of the same decision read the same. Each is shown twice: every
name is defined in one file of the package, and what the two texts actually print is the same
line.
"""

import re
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import email_payloads as emails
import note_payloads as notes
import pytest
from pydantic import ValidationError

import ais0c_executor
from ais0c_contracts import ActionType, CaseVerdict, Confidence, DataGap, DataGapReason, Level
from ais0c_executor.common import LABELS, TemplateError, label
from ais0c_executor.email import alert_message, render_body
from ais0c_executor.note import render_note

PACKAGE = Path(ais0c_executor.__file__).parent


def defining_files(start: str) -> list[str]:
    """The package files with a module-level definition that starts with `start`."""
    return sorted(
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        if re.search(rf"^{start}", path.read_text(encoding="utf-8"), re.MULTILINE)
    )


# --- criterion 6: one place per name --------------------------------------------------------


@pytest.mark.parametrize("name", ["EXECUTOR_ID", "EXECUTOR_SECRETS_DIR_ENV", "DEFAULT_SECRETS_DIR"])
def test_a_shared_setting_is_defined_in_one_place(name: str) -> None:
    assert defining_files(rf"{name}(: [^=\n]+)? =") == ["common/settings.py"]


@pytest.mark.parametrize(
    "name",
    [
        "check_case_id",
        "check_case_url",
        "check_evaluation_no",
        "check_group_id",
        "check_offense_id",
    ],
)
def test_an_identity_check_is_defined_in_one_place(name: str) -> None:
    assert defining_files(rf"def {name}\(") == ["common/identity.py"]


def messages(build: Callable[[], object]) -> str:
    """The reasons a request was refused with, without the values it was given."""
    with pytest.raises(ValidationError) as raised:
        build()
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in raised.value.errors(include_input=False, include_context=False)
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"evaluation_no": 0},
        {"evaluation_no": 100_000},
        {"offense_id": -1},
        {"case_url": "javascript:alert(1)"},
        {"case_url": "https://ais0c.example.com/x\nBcc:"},
    ],
    ids=["zero", "too-big", "negative", "script-link", "line-break"],
)
def test_a_note_and_an_alert_check_the_content_the_same_way(changes: dict[str, Any]) -> None:
    assert messages(lambda: notes.evaluation_note(**changes)) == messages(
        lambda: emails.case_alert(content=emails.note_content(**changes))
    )


def test_a_note_and_an_alert_check_the_case_id_the_same_way() -> None:
    assert messages(lambda: notes.evaluation_note(case_id="case 1")) == messages(
        lambda: emails.case_alert(case_id="case 1")
    )


def test_a_group_note_and_a_group_alert_check_the_group_the_same_way() -> None:
    assert messages(lambda: notes.group_note(group_id="G 1")) == messages(
        lambda: emails.group_alert(group_id="G 1")
    )


# --- criterion 5: one label table ------------------------------------------------------------


def alert_body(content: dict[str, Any]) -> str:
    return render_body(
        alert_message(emails.case_alert(content=emails.note_content(**content)), emails.OPERATORS)
    )


def note_text(content: dict[str, Any]) -> str:
    return render_note(notes.evaluation_note(**content))


def line_starting(text: str, prefix: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(prefix))


def test_the_table_covers_every_contract_value() -> None:
    assert set(LABELS["level"]) == {level.value for level in Level}
    assert set(LABELS["level_tag"]) == {level.value for level in Level}
    assert set(LABELS["verdict"]) == {verdict.value for verdict in CaseVerdict}
    assert set(LABELS["verdict_short"]) == {verdict.value for verdict in CaseVerdict}
    assert set(LABELS["confidence"]) == {confidence.value for confidence in Confidence}
    assert set(LABELS["action"]) == {action.value for action in ActionType}
    assert set(LABELS["gap_reason"]) == {reason.value for reason in DataGapReason}


@pytest.mark.parametrize(
    ("category", "value"),
    [
        ("level", "very-high"),
        ("level", "High"),
        ("level_tag", "yüksek"),
        ("verdict", "maybe"),
        ("verdict_short", "maybe"),
        ("confidence", "certain"),
        ("action", "reboot_the_host"),
        ("gap_reason", "no_answer"),
        ("no_such_category", "high"),
    ],
    ids=[
        "level",
        "wrong-case",
        "upper-case-level",
        "verdict",
        "short-verdict",
        "confidence",
        "action",
        "gap-reason",
        "category",
    ],
)
def test_a_value_without_a_label_is_an_error(category: str, value: str) -> None:
    with pytest.raises(TemplateError, match="no Turkish label"):
        label(category, value)  # type: ignore[arg-type]


@pytest.mark.parametrize("level", list(Level))
def test_a_note_and_an_alert_label_the_level_the_same_way(level: Level) -> None:
    assert line_starting(note_text({"notify_level": level}), "Karar:") == line_starting(
        alert_body({"notify_level": level}), "Karar:"
    )
    assert line_starting(note_text({"notify_level": level}), "Karar:").endswith(
        f"Bildirim seviyesi: {label('level', level)}"
    )


@pytest.mark.parametrize("verdict", list(CaseVerdict))
def test_a_note_and_an_alert_label_the_verdict_the_same_way(verdict: CaseVerdict) -> None:
    assert line_starting(note_text({"verdict": verdict}), "Karar:") == line_starting(
        alert_body({"verdict": verdict}), "Karar:"
    )
    assert line_starting(note_text({"verdict": verdict}), "Karar:").startswith(
        f"Karar: {label('verdict', verdict)} ·"
    )


@pytest.mark.parametrize("confidence", list(Confidence))
def test_a_note_and_an_alert_label_the_confidence_the_same_way(confidence: Confidence) -> None:
    assert line_starting(note_text({"confidence": confidence}), "Karar:") == line_starting(
        alert_body({"confidence": confidence}), "Karar:"
    )
    assert f"Güven: {label('confidence', confidence)}" in note_text({"confidence": confidence})


@pytest.mark.parametrize("action", list(ActionType))
def test_a_note_and_an_alert_label_an_action_the_same_way(action: ActionType) -> None:
    assert line_starting(
        note_text({"recommended_actions": [action]}), "Önerilen adımlar:"
    ) == line_starting(alert_body({"recommended_actions": [action]}), "Önerilen adımlar:")
    assert (
        line_starting(note_text({"recommended_actions": [action]}), "Önerilen adımlar:")
        == f"Önerilen adımlar: {label('action', action)}"
    )


@pytest.mark.parametrize("reason", list(DataGapReason))
def test_a_note_and_an_alert_label_a_data_gap_the_same_way(reason: DataGapReason) -> None:
    gap = DataGap(
        source="Proxy",
        period_start=notes.T0 - timedelta(hours=2),
        period_end=notes.T0,
        reason=reason,
    )

    assert line_starting(note_text({"data_gaps": [gap]}), "Veri eksikleri:") == line_starting(
        alert_body({"data_gaps": [gap]}), "Veri eksikleri:"
    )
    assert f"Proxy ({label('gap_reason', reason)}," in note_text({"data_gaps": [gap]})
