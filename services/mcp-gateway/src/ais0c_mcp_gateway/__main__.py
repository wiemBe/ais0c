"""Run the gateway: `python -m ais0c_mcp_gateway` (settings: ais0c_mcp_gateway.settings)."""

import uvicorn

from ais0c_mcp_gateway.service import build_service
from ais0c_mcp_gateway.settings import Settings


def main() -> None:
    settings = Settings.from_env()
    service = build_service(settings)
    uvicorn.run(
        service.app,
        host=settings.host,
        port=settings.port,
        # Logging is already set up with secret redaction; uvicorn must not replace it.
        log_config=None,
        # The client address is never taken from X-Forwarded-For (T-007 report, section 5).
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
