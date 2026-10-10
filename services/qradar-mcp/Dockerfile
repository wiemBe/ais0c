# Platform image of the qradar-mcp fork (see README.md, "Platform fork").
#
# Upstream's image ran server.py with per-request QRadar credentials. This image runs the
# fork's entry point instead: streamable HTTP on /mcp, QRadar token from the environment
# or a secret file, API version negotiated at startup. There is deliberately no default
# profile: the container exits unless it is started with --profile.
#
#   docker run ... qradar-mcp-fork:<tag> --profile qradar-read
FROM docker.io/library/python:3.11.17-slim@sha256:45037981b62b34b44602584fccbc4d884d5f7dc92c7ee86bb38a698a79fe1e51

LABEL org.opencontainers.image.title="qradar-mcp-fork" \
      org.opencontainers.image.description="IBM QRadar MCP server, platform fork: profiles, version pinning, streamable HTTP" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/app-root

# Runtime dependencies from the pinned lock file, then the package itself without resolving
# anything again, so the image contains exactly the versions in requirements.txt.
COPY requirements.txt ./requirements.txt
RUN pip install --requirement requirements.txt

COPY . ./src/
RUN pip install --no-deps ./src/ \
    && rm -rf ./src \
    && useradd --system --uid 1001 --no-create-home --shell /usr/sbin/nologin appuser

USER 1001
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=3).status != 200)"]

ENTRYPOINT ["qradar-mcp-fork", "--host", "0.0.0.0", "--port", "5000"]
