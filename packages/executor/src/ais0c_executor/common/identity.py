"""Layout-safe platform identities and case links shared by notes and e-mails."""

import re
from typing import Final

# Workflow-derived IDs: `case-12345`, `group-<id>`.
CASE_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")
# `G-<key>-<time>` (ais0c_activities.grouping).
GROUP_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
# The platform's case page: http(s), a host, optional port and path.
CASE_URL: Final = re.compile(
    r"https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~%/?#=&+-]*)?"
)
MAX_CASE_URL_LENGTH: Final = 200
MAX_EVALUATION_NO: Final = 99_999
MAX_OFFENSE_ID: Final = 2**63 - 1


def check_case_id(case_id: str) -> None:
    """Raise ValueError unless the case ID is a safe workflow-derived ID."""
    if not CASE_ID.fullmatch(case_id):
        raise ValueError("case_id must be a workflow-derived ID such as case-12345")


def check_group_id(group_id: str) -> None:
    """Raise ValueError unless the group ID can be shown safely on one line."""
    if not GROUP_ID.fullmatch(group_id):
        raise ValueError("group_id must be 1-64 letters, digits, '.', '_', ':' and '-'")


def check_evaluation_no(evaluation_no: int) -> None:
    """Raise ValueError unless the evaluation number is within executor bounds."""
    if not 1 <= evaluation_no <= MAX_EVALUATION_NO:
        raise ValueError(f"evaluation_no must be between 1 and {MAX_EVALUATION_NO}")


def check_offense_id(offense_id: int) -> None:
    """Raise ValueError unless the offense ID is a QRadar offense identifier."""
    if not 0 <= offense_id <= MAX_OFFENSE_ID:
        raise ValueError("offense_id must be a QRadar offense ID")


def check_case_url(case_url: str) -> None:
    """Raise ValueError unless the case URL is a short, plain http(s) link."""
    if len(case_url) > MAX_CASE_URL_LENGTH or not CASE_URL.fullmatch(case_url):
        raise ValueError(
            f"case_url must be an http(s) link of at most {MAX_CASE_URL_LENGTH} characters"
        )
