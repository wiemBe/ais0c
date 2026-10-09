"""Run a worker or the ``migrate`` and ``preflight`` deployment commands.

Settings: `ais0c_worker.main`.
"""

import sys

from ais0c_worker.main import main

if __name__ == "__main__":
    sys.exit(main())
