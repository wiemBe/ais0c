"""LiteLLM configurations in config/litellm/ (T-003 acceptance criteria 4-6).

Code calls models only by alias (T-03): litellm.dev.yaml sends the aliases to OpenRouter,
litellm.prod.yaml to the on-prem vLLM servers. These tests keep cloud providers out of the prod
file (D-11), keep soc-reasoning and soc-verifier on different models (D-21) and keep keys out
of both files. Each rule also runs against a broken configuration to show that it catches the
violation.
"""

import copy
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENTS = ["dev", "prod"]
# soc-embed is not needed in phase 0 (T-003).
ALIASES = {"soc-fast", "soc-reasoning", "soc-verifier", "soc-report"}
ON_PREM_PREFIX = "hosted_vllm/"
ENV_REFERENCE = re.compile(r"os\.environ/(?P<name>[A-Z][A-Z0-9_]*)")
# Environment variables the prod file may read: vLLM endpoints and keys, and LiteLLM's own key.
PROD_ENV_NAME = re.compile(r"VLLM_[A-Z0-9_]+_(API_BASE|API_KEY)|LITELLM_MASTER_KEY")
SECRET_SETTING = re.compile(r"(^|_)(key|secret|password|token)$")
# LiteLLM settings that can send a request for one alias to another alias's models.
ROUTING_SETTINGS = [
    "fallbacks",
    "context_window_fallbacks",
    "content_policy_fallbacks",
    "default_fallbacks",
    "model_group_alias",
]


def load_config(environment: str) -> dict[str, Any]:
    path = REPO_ROOT / f"config/litellm/litellm.{environment}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def models_by_alias(config: dict[str, Any]) -> dict[str, set[str]]:
    """Map each alias to the models of its deployments."""
    models: dict[str, set[str]] = {}
    for entry in config["model_list"]:
        models.setdefault(entry["model_name"], set()).add(entry["litellm_params"]["model"])
    return models


def on_prem_violations(config: dict[str, Any]) -> list[str]:
    """Describe every deployment that is not an on-prem vLLM server read from the environment."""
    violations: list[str] = []
    for entry in config["model_list"]:
        alias, params = entry["model_name"], entry["litellm_params"]
        model = params.get("model")
        if not (isinstance(model, str) and model.startswith(ON_PREM_PREFIX)):
            violations.append(f"{alias}: model {model!r} does not start with {ON_PREM_PREFIX!r}")
        provider = params.get("custom_llm_provider")
        if provider not in (None, "hosted_vllm"):
            violations.append(f"{alias}: custom_llm_provider {provider!r} overrides the prefix")
        api_base = params.get("api_base")
        if not (isinstance(api_base, str) and ENV_REFERENCE.fullmatch(api_base)):
            violations.append(f"{alias}: api_base {api_base!r} is not read from the environment")
    return violations


def foreign_env_names(config: dict[str, Any]) -> set[str]:
    """Return the environment variables a prod configuration reads that are not vLLM's or LiteLLM's."""
    names = {match["name"] for match in ENV_REFERENCE.finditer(yaml.safe_dump(config))}
    return {name for name in names if not PROD_ENV_NAME.fullmatch(name)}


def shared_models(config: dict[str, Any], first: str, second: str) -> set[str]:
    by_alias = models_by_alias(config)
    return by_alias.get(first, set()) & by_alias.get(second, set())


def routing_indirections(config: dict[str, Any]) -> list[str]:
    """Return the settings that could route one alias to another alias's models."""
    return [
        f"{section}.{setting}"
        for section in ("litellm_settings", "router_settings")
        for setting in ROUTING_SETTINGS
        if (config.get(section) or {}).get(setting)
    ]


def literal_secrets(node: object, path: str = "") -> list[str]:
    """Return the paths of key, secret, password and token settings not read from the environment."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            is_secret = SECRET_SETTING.search(str(key).lower()) is not None
            if is_secret and not (isinstance(value, str) and ENV_REFERENCE.fullmatch(value)):
                found.append(child)
            found.extend(literal_secrets(value, child))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(literal_secrets(item, f"{path}[{index}]"))
    return found


def prod_config_with(**params: object) -> dict[str, Any]:
    """Return the prod configuration with soc-fast's litellm_params updated; None removes a key."""
    config = copy.deepcopy(load_config("prod"))
    entry = next(e for e in config["model_list"] if e["model_name"] == "soc-fast")
    for key, value in params.items():
        if value is None:
            entry["litellm_params"].pop(key, None)
        else:
            entry["litellm_params"][key] = value
    return config


# --- both configurations ------------------------------------------------------------------


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_config_defines_each_alias_once(environment: str) -> None:
    config = load_config(environment)
    counts = Counter(entry["model_name"] for entry in config["model_list"])

    assert set(counts) == ALIASES  # no wildcard route, no extra model
    assert set(counts.values()) == {1}  # the registry describes one model per alias


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_reasoning_and_verifier_use_different_models(environment: str) -> None:
    config = load_config(environment)

    assert shared_models(config, "soc-reasoning", "soc-verifier") == set()
    # A fallback or group alias could still send verifier requests to the reasoning model.
    assert routing_indirections(config) == []


def test_shared_model_between_reasoning_and_verifier_is_detected() -> None:
    config = copy.deepcopy(load_config("prod"))
    models = models_by_alias(config)
    for entry in config["model_list"]:
        if entry["model_name"] == "soc-verifier":
            entry["litellm_params"]["model"] = next(iter(models["soc-reasoning"]))

    assert shared_models(config, "soc-reasoning", "soc-verifier") == models["soc-reasoning"]


@pytest.mark.parametrize("section", ["litellm_settings", "router_settings"])
def test_fallback_between_aliases_is_detected(section: str) -> None:
    config = copy.deepcopy(load_config("dev"))
    config[section] = {"fallbacks": [{"soc-verifier": ["soc-reasoning"]}]}

    assert routing_indirections(config) == [f"{section}.fallbacks"]


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_keys_come_from_the_environment(environment: str) -> None:
    config = load_config(environment)

    assert literal_secrets(config) == []
    assert config["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"


def test_literal_key_is_detected() -> None:
    config = prod_config_with(api_key="placeholder-key")

    assert literal_secrets(config) == ["model_list[0].litellm_params.api_key"]


# --- prod: on-prem vLLM only (D-11) ------------------------------------------------------


def test_prod_config_uses_only_on_prem_vllm() -> None:
    assert on_prem_violations(load_config("prod")) == []


def test_prod_config_reads_no_cloud_credentials() -> None:
    assert foreign_env_names(load_config("prod")) == set()


@pytest.mark.parametrize(
    "model",
    [
        "openrouter/deepseek/deepseek-v4-flash",
        "openai/deepseek-ai/DeepSeek-V4-Flash",
        "deepseek/deepseek-chat",
        "anthropic/claude-sonnet",
        "azure/deployment",
        "gemini/gemini-pro",
        "bedrock/model",
        "together_ai/Qwen/Qwen3.5-122B-A10B",
        "deepseek-ai/DeepSeek-V4-Flash",  # no provider prefix at all
    ],
)
def test_cloud_model_in_prod_is_rejected(model: str) -> None:
    violations = on_prem_violations(prod_config_with(model=model))

    assert violations == [f"soc-fast: model {model!r} does not start with 'hosted_vllm/'"]


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"api_base": "http://192.0.2.10:8000/v1"}, "api_base 'http://192.0.2.10:8000/v1'"),
        ({"api_base": None}, "api_base None"),
        ({"custom_llm_provider": "openrouter"}, "custom_llm_provider 'openrouter'"),
    ],
    ids=["literal-api-base", "missing-api-base", "provider-override"],
)
def test_prod_endpoint_outside_the_environment_is_rejected(
    params: dict[str, object], expected: str
) -> None:
    violations = on_prem_violations(prod_config_with(**params))

    assert len(violations) == 1
    assert violations[0].startswith(f"soc-fast: {expected}")


def test_cloud_credential_in_prod_is_rejected() -> None:
    config = prod_config_with(api_key="os.environ/OPENROUTER_API_KEY")

    assert foreign_env_names(config) == {"OPENROUTER_API_KEY"}


def test_prod_config_fails_closed_without_an_api_base() -> None:
    # LiteLLM sends a hosted_vllm request with an empty api_base to HOSTED_VLLM_API_BASE, then
    # to the public OpenAI API. The prod file pins that fallback to a host that never resolves.
    # test_dev_stack.py shows the behaviour with the pinned LiteLLM image.
    variables = load_config("prod").get("environment_variables") or {}
    fallback = variables.get("HOSTED_VLLM_API_BASE")

    assert isinstance(fallback, str), "litellm.prod.yaml must set HOSTED_VLLM_API_BASE"
    assert str(urlsplit(fallback).hostname).endswith(".invalid")


# --- dev: OpenRouter (D-10) ---------------------------------------------------------------


def test_dev_config_reaches_every_alias_through_openrouter() -> None:
    for entry in load_config("dev")["model_list"]:
        params = entry["litellm_params"]

        assert params["model"].startswith("openrouter/"), entry["model_name"]
        assert params["api_key"] == "os.environ/OPENROUTER_API_KEY"
        # Providers must honour every parameter (tools, response format) and keep no prompts.
        assert params["extra_body"]["provider"] == {
            "require_parameters": True,
            "data_collection": "deny",
        }
