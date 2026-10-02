"""Model clients for the logical model aliases (decision T-03, architecture §19).

Agents never talk to a model provider. Every request goes to the LiteLLM proxy through its
OpenAI-compatible API, and LiteLLM maps the alias to a model (config/litellm/). This is the only
module that may import the OpenAI-compatible client (AGENTS.md hard rule 3).

Environment:

    LITELLM_BASE_URL  LiteLLM address without the /v1 suffix, e.g. http://127.0.0.1:4000
    LITELLM_API_KEY   key sent to LiteLLM as a bearer token; in the dev stack, the value of
                      LITELLM_MASTER_KEY from deploy/compose/.env
"""

import os
from collections.abc import Mapping
from typing import Final, Literal, get_args
from urllib.parse import urlsplit

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.litellm import LiteLLMProvider
from pydantic_ai.settings import ModelSettings

ModelAlias = Literal["soc-fast", "soc-reasoning", "soc-verifier", "soc-report", "soc-embed"]
MODEL_ALIASES: Final[tuple[ModelAlias, ...]] = get_args(ModelAlias)

LITELLM_BASE_URL_ENV: Final = "LITELLM_BASE_URL"
LITELLM_API_KEY_ENV: Final = "LITELLM_API_KEY"


class ModelConfigError(ValueError):
    """The model client cannot be built: unknown alias, or LiteLLM settings missing or invalid."""


def build_model(
    alias: ModelAlias,
    *,
    settings: ModelSettings | None = None,
    environ: Mapping[str, str] | None = None,
) -> Model:
    """Return a model that sends requests for `alias` to LiteLLM.

    `environ` defaults to the process environment. `settings` apply to every request made with
    the model, e.g. the model registry's `parallel_tool_calls`.
    """
    if alias not in MODEL_ALIASES:
        raise ModelConfigError(
            f"unknown model alias {alias!r}; known aliases: {', '.join(MODEL_ALIASES)}"
        )
    env = os.environ if environ is None else environ
    base_url = _base_url(env.get(LITELLM_BASE_URL_ENV, ""))
    api_key = env.get(LITELLM_API_KEY_ENV, "")
    if not api_key.strip():
        raise ModelConfigError(f"{LITELLM_API_KEY_ENV} is not set")
    provider = LiteLLMProvider(api_base=f"{base_url}/v1", api_key=api_key)
    return OpenAIChatModel(alias, provider=provider, settings=settings)


def _base_url(value: str) -> str:
    """Check LITELLM_BASE_URL and return it without a trailing slash."""
    value = value.strip()
    if not value:
        raise ModelConfigError(f"{LITELLM_BASE_URL_ENV} is not set")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ModelConfigError(f"{LITELLM_BASE_URL_ENV} must be an http(s) URL with a host")
    # Credentials belong in LITELLM_API_KEY: a URL ends up in logs and error messages.
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ModelConfigError(
            f"{LITELLM_BASE_URL_ENV} must not contain credentials, a query or a fragment"
        )
    base_url = value.rstrip("/")
    if base_url.endswith("/v1"):
        raise ModelConfigError(f"{LITELLM_BASE_URL_ENV} must not end with /v1; it is added")
    return base_url
