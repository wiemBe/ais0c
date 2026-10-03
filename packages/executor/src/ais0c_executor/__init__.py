"""QRadar notes, e-mails and hunt PDFs built from structured data and fixed templates.

No LLM calls. One module per kind of write, and the parts they share:

- `common`: the kill switch, text cleaning and template loading
- `note`: QRadar offense notes
- `email`: e-mails

Import from the module (`from ais0c_executor.common import KillSwitch`). This package
re-exports nothing, so the modules change without touching each other.
"""
