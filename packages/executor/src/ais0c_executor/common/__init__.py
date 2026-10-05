"""What every executor module uses: the kill switch, text cleaning, template loading, and the
names notes and e-mails share.

- `KillSwitch`: checked right before every external write; raises `WritesDisabled` when writes
  are off (T-23).
- `clean_text`: one line of plain text, without control or invisible characters, cut to a
  length.
- `Templates`: the fixed templates of a module, filled only with cleaned plain data.
- `label`, `LABELS`: the Turkish labels of the contract values a note and an e-mail show, so
  both read the same (T-33 (5)). A value without a label is an error.
- `check_case_id`, `check_group_id`, `check_evaluation_no`, `check_offense_id`,
  `check_case_url`: the platform values a note and an e-mail print as they are.
- `EXECUTOR_ID`, `EXECUTOR_SECRETS_DIR_ENV`, `DEFAULT_SECRETS_DIR`: who the executor writes as,
  and where its secret files are.
"""

from ais0c_executor.common.errors import ExecutorError, TemplateError, WritesDisabled
from ais0c_executor.common.identity import (
    check_case_id,
    check_case_url,
    check_evaluation_no,
    check_group_id,
    check_offense_id,
)
from ais0c_executor.common.kill_switch import KillSwitch
from ais0c_executor.common.labels import LABELS, LabelCategory, label
from ais0c_executor.common.settings import (
    DEFAULT_SECRETS_DIR,
    EXECUTOR_ID,
    EXECUTOR_SECRETS_DIR_ENV,
)
from ais0c_executor.common.templates import FieldValue, Templates
from ais0c_executor.common.text import ELLIPSIS, clean_text, is_clean

__all__ = [
    "DEFAULT_SECRETS_DIR",
    "ELLIPSIS",
    "EXECUTOR_ID",
    "EXECUTOR_SECRETS_DIR_ENV",
    "LABELS",
    "ExecutorError",
    "FieldValue",
    "KillSwitch",
    "LabelCategory",
    "TemplateError",
    "Templates",
    "WritesDisabled",
    "check_case_id",
    "check_case_url",
    "check_evaluation_no",
    "check_group_id",
    "check_offense_id",
    "clean_text",
    "is_clean",
    "label",
]
