"""Run the analyst API: `python -m ais0c_api` (settings: `ais0c_api.settings`).

Settings come from the environment; see `ais0c_api.settings` for the table. With no
`AIS0C_API_AUTH`, or with a value this build does not know, the process prints the reason and
exits with status 2 without binding a port (T-63 (1)). The database URL and the Temporal address
are only read, not checked, at start-up: `/health` answers even when the database is down.
"""

import logging
import sys

import uvicorn

from ais0c_api.service import ServiceError, build_service
from ais0c_api.settings import SettingsError

# A setting, a file or a secret the service needs is missing or invalid.
EXIT_CONFIG_ERROR = 2

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def main(argv: list[str] | None = None) -> int:
    """Start the service; returns the exit status."""
    del argv  # the service takes no argument; its settings are environment variables
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    try:
        service = build_service()
    except (ServiceError, SettingsError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    settings = service.settings
    uvicorn.run(
        service.app,
        host=settings.host,
        port=settings.port,
        # Logging is already set up; uvicorn must not replace it.
        log_config=None,
        # The client address is never taken from X-Forwarded-For (T-007 report, section 5).
        proxy_headers=False,
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
