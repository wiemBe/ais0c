# Image of the MCP Policy Gateway (architecture §13.4, §25; T-018).
#
# The build context is the repository root: the gateway is a member of the uv workspace and
# brings three workspace packages along (contracts, policy, storage).
#
#   docker build -f services/mcp-gateway/Dockerfile -t ais0c-mcp-gateway:dev .
#
# Dockerfile.dockerignore next to this file keeps the context to the workspace metadata and the
# sources those four packages need. Base images are pinned by tag and digest; the dev stack's
# static test checks every FROM line (tests/deploy/test_compose_file.py).
#
# The image holds no configuration and no secret. At run time the gateway reads
# AIS0C_GATEWAY_CONFIG_DIR (connectors/ and policies/), the tokens in /run/secrets and the
# rest of its environment (ais0c_mcp_gateway/settings.py).

FROM ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc AS uv

FROM docker.io/library/python:3.12.15-slim-trixie@sha256:29113dcae7aad06daa8e95260fa09f27d62be33b9687ea3774f771d601a02256 AS build

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY . .
# Exactly the versions in uv.lock, without the dev groups. The workspace packages are installed
# as wheels, not in editable mode, so the image needs no source tree.
RUN uv sync --locked --no-dev --no-editable --package ais0c-mcp-gateway

FROM docker.io/library/python:3.12.15-slim-trixie@sha256:29113dcae7aad06daa8e95260fa09f27d62be33b9687ea3774f771d601a02256

LABEL org.opencontainers.image.title="ais0c-mcp-gateway" \
      org.opencontainers.image.description="ais0c MCP Policy Gateway: the agents' only way to QRadar"

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=build /opt/venv /opt/venv
RUN groupadd --gid 10001 gateway \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin gateway

USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status != 200)"]

ENTRYPOINT ["python", "-m", "ais0c_mcp_gateway"]
