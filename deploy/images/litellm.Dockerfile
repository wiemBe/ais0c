# Image of the LiteLLM proxy for the prod shadow stack (T-077). The same base image as
# docker-compose.dev.yaml, plus the prod configuration, so the release carries it:
#
#   docker build -f deploy/images/litellm.Dockerfile -t ais0c-litellm:dev .
#
# litellm.Dockerfile.dockerignore keeps the context to that one file. The file names the vLLM
# endpoints as os.environ/VLLM_* variables; no endpoint and no key is in the image.

FROM ghcr.io/berriai/litellm:v1.103.2@sha256:f63fb81b831b170ec16851e23c36ac5bf52ef106b271406429524a2ed730bbfd

LABEL org.opencontainers.image.title="ais0c-litellm" \
      org.opencontainers.image.description="LiteLLM proxy with the prod model aliases (config/litellm/litellm.prod.yaml)"

# The compose command is ["--config", "/etc/litellm/litellm.prod.yaml", "--port", "4000"].
COPY config/litellm/litellm.prod.yaml /etc/litellm/litellm.prod.yaml
