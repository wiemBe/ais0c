# Image of the analyst UI (T-076): nginx serving the build output of apps/ui over TLS, with
# /api/ passed on to the API service (deploy/images/nginx/ui.conf).
#
# The build context is the repository root:
#
#   docker build -f deploy/images/ui.Dockerfile -t ais0c-ui:dev .
#
# ui.Dockerfile.dockerignore next to this file keeps the context to apps/ui and the nginx
# configuration. Base images are pinned by tag and digest (tests/deploy/test_images.py).
#
# The image holds no certificate and no secret: nginx reads /run/secrets/ui-tls.crt and
# /run/secrets/ui-tls.key at run time. The unprivileged nginx image runs as uid 101 and listens
# on 8443.

FROM docker.io/library/node:22.23.3-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392 AS build

WORKDIR /src/apps/ui
# The pnpm version comes from the packageManager field in package.json.
RUN corepack enable
COPY apps/ui/package.json apps/ui/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/ui ./
RUN pnpm build

FROM docker.io/nginxinc/nginx-unprivileged:1.31.6-alpine@sha256:b9241c6e7b8e9a862f129d8d4199ab64b10390949a78bdd5603379b32c844083

LABEL org.opencontainers.image.title="ais0c-ui" \
      org.opencontainers.image.description="ais0c analyst UI served by nginx over TLS"

COPY --from=build /src/apps/ui/dist /usr/share/nginx/html
COPY deploy/images/nginx/ui.conf /etc/nginx/conf.d/default.conf

USER 101
EXPOSE 8443
