# Image of the platform's Python services (T-076): the case, batch and executor workers
# (ais0c_worker) and the analyst API (ais0c_api) share this one image. The command picks the
# service. The image carries the approved content (config/agents, config/models, prompts,
# skills), so one image is one release.
#
# The build context is the repository root:
#
#   docker build -f deploy/images/platform.Dockerfile -t ais0c-platform:dev .
#
# platform.Dockerfile.dockerignore next to this file keeps the context to the workspace metadata,
# the package sources and the approved content. Base images are pinned by tag and digest; the
# static tests check every FROM line (tests/deploy/test_images.py).
#
# The image holds no secret and no environment configuration: not .env, not deploy/compose/secrets
# and not config/litellm (LiteLLM is a separate service). At run time the services read their
# settings from the environment and the tokens from /run/secrets (ais0c_activities.runtime,
# ais0c_api.settings).

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
# as wheels, not in editable mode, so the image needs no source tree. Both services go into one
# virtual environment.
RUN uv sync --locked --no-dev --no-editable --package ais0c-worker --package ais0c-api

FROM docker.io/library/python:3.12.15-slim-trixie@sha256:29113dcae7aad06daa8e95260fa09f27d62be33b9687ea3774f771d601a02256

LABEL org.opencontainers.image.title="ais0c-platform" \
      org.opencontainers.image.description="ais0c workers and analyst API with the approved agent configuration"

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AIS0C_WORKER_ROOT=/app

COPY --from=build /opt/venv /opt/venv
RUN groupadd --gid 10001 ais0c \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin ais0c

WORKDIR /app
COPY config/agents /app/config/agents
COPY config/models /app/config/models
COPY config/policies /app/config/policies
COPY config/sigma /app/config/sigma
COPY config/telemetry /app/config/telemetry
COPY prompts /app/prompts
COPY skills /app/skills

USER 10001:10001

# No HEALTHCHECK: it differs per service; the compose file gives it.
# The compose command is ["ais0c_worker", "batch"], ["ais0c_worker", "executor"], ["ais0c_api"].
ENTRYPOINT ["python", "-m"]
CMD ["ais0c_worker"]
