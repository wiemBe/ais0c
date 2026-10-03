"""What every executor module uses: the kill switch, text cleaning and template loading.

- `KillSwitch`: checked right before every external write; raises `WritesDisabled` when writes
  are off (T-23).
- `clean_text`: one line of plain text, without control or invisible characters, cut to a
  length.
- `Templates`: the fixed templates of a module, filled only with cleaned plain data.
"""

from ais0c_executor.common.errors import ExecutorError, TemplateError, WritesDisabled
from ais0c_executor.common.kill_switch import KillSwitch
from ais0c_executor.common.templates import FieldValue, Templates
from ais0c_executor.common.text import ELLIPSIS, clean_text, is_clean

__all__ = [
    "ELLIPSIS",
    "ExecutorError",
    "FieldValue",
    "KillSwitch",
    "TemplateError",
    "Templates",
    "WritesDisabled",
    "clean_text",
    "is_clean",
]
