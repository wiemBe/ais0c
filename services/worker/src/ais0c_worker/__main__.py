"""Run a worker: `python -m ais0c_worker` (the case worker), `python -m ais0c_worker batch`.

Settings: `ais0c_worker.main`.
"""

import sys

from ais0c_worker.main import main

if __name__ == "__main__":
    sys.exit(main())
