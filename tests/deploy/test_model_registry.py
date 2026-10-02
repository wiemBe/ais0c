"""Model capability registries in config/models/ (T-003 acceptance criterion 7).

registry.dev.yaml and registry.prod.yaml describe, in the format of architecture §8.4, the
target and capabilities of every alias in the LiteLLM configuration of the same environment.
These tests keep each registry in step with its LiteLLM file and keep dev on the prod models
(D-12). Each rule also runs against a broken registry to show that it catches the problem.
"""

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENTS = ["dev", "prod"]
# Field names of architecture §8.4. tool_parser is part of the registry (§8.4 rules);
# reasoning_parser records the other vLLM setting that changes tool calls and structured output.
FIELDS: dict[str, tuple[type, ...]] = {
    "target": (str,),
    "prod_equivalent": (str,),
    "capabilities": (list,),
    "parallel_tool_calls": (bool,),
    "context_window": (int,),
    "tool_parser": (str, type(None)),
    "reasoning_parser": (str, type(None)),
    "turkish_quality": (int, float, type(None)),
}
# Values agent manifests use in required_model_capabilities (architecture §8.1).
CAPABILITIES = {"tool_calling", "structured_output"}


def load_registry(environment: str) -> dict[str, Any]:
    path = REPO_ROOT / f"config/models/registry.{environment}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def litellm_models(environment: str) -> dict[str, str]:
    """Map each alias to its model in the LiteLLM configuration of the environment."""
    path = REPO_ROOT / f"config/litellm/litellm.{environment}.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {entry["model_name"]: entry["litellm_params"]["model"] for entry in config["model_list"]}


def entry_problems(alias: str, entry: object) -> list[str]:
    """Check one registry entry against the §8.4 fields."""
    if not isinstance(entry, dict):
        return [f"{alias}: not a mapping"]
    problems: list[str] = []
    if missing := FIELDS.keys() - entry.keys():
        problems.append(f"{alias}: missing {sorted(missing)}")
    if unknown := entry.keys() - FIELDS.keys():
        problems.append(f"{alias}: unknown {sorted(unknown)}")
    for field, types in FIELDS.items():
        value = entry.get(field)
        # bool is an int subclass; True is not a context window or a score.
        if field in entry and (
            not isinstance(value, types) or (bool not in types and isinstance(value, bool))
        ):
            problems.append(f"{alias}: {field} has type {type(value).__name__}")
    capabilities = entry.get("capabilities")
    if isinstance(capabilities, list):
        if not capabilities or len(set(capabilities)) != len(capabilities):
            problems.append(f"{alias}: capabilities must be a non-empty list without duplicates")
        if unknown_capabilities := set(capabilities) - CAPABILITIES:
            problems.append(f"{alias}: unknown capabilities {sorted(unknown_capabilities)}")
    window = entry.get("context_window")
    if isinstance(window, int) and window <= 0:
        problems.append(f"{alias}: context_window must be positive")
    return problems


def registry_problems(registry: dict[str, Any], models: dict[str, str]) -> list[str]:
    """Check a registry against the LiteLLM models of the same environment."""
    problems: list[str] = []
    if registry.keys() != models.keys():
        problems.append(f"aliases differ from LiteLLM: {sorted(registry.keys() ^ models.keys())}")
    for alias, entry in registry.items():
        problems.extend(entry_problems(alias, entry))
        if isinstance(entry, dict) and alias in models and entry.get("target") != models[alias]:
            problems.append(f"{alias}: target {entry.get('target')!r} is not {models[alias]!r}")
    return problems


def model_name(target: str) -> str:
    """openrouter/deepseek/deepseek-v4-flash and hosted_vllm/deepseek-ai/DeepSeek-V4-Flash
    both name the model deepseek-v4-flash."""
    return target.rsplit("/", 1)[-1].lower()


def dev_prod_mismatches(dev: dict[str, Any], prod: dict[str, Any]) -> list[str]:
    """Return the aliases whose dev model is not the model the alias uses in prod (D-12)."""
    mismatches: list[str] = []
    for alias, entry in dev.items():
        prod_target = prod[alias]["target"]
        if entry["prod_equivalent"] != prod_target:
            mismatches.append(f"{alias}: prod_equivalent is not {prod_target!r}")
        if model_name(entry["target"]) != model_name(prod_target):
            mismatches.append(f"{alias}: dev runs {entry['target']!r}, prod runs {prod_target!r}")
    return mismatches


@pytest.mark.parametrize("environment", ENVIRONMENTS)
def test_registry_describes_every_alias_of_its_litellm_config(environment: str) -> None:
    assert registry_problems(load_registry(environment), litellm_models(environment)) == []


def test_prod_registry_records_the_vllm_settings() -> None:
    for alias, entry in load_registry("prod").items():
        assert entry["prod_equivalent"] == entry["target"], alias
        assert entry["tool_parser"], f"{alias}: vLLM tool parser missing (§8.4)"


def test_dev_registry_runs_the_prod_models() -> None:
    assert dev_prod_mismatches(load_registry("dev"), load_registry("prod")) == []


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda e: e.pop("capabilities"), "soc-fast: missing ['capabilities']"),
        (lambda e: e.update(max_tokens=10), "soc-fast: unknown ['max_tokens']"),
        (lambda e: e.update(capabilities=["vision"]), "soc-fast: unknown capabilities ['vision']"),
        (lambda e: e.update(capabilities=[]), "soc-fast: capabilities must be a non-empty list"),
        (lambda e: e.update(context_window="1M"), "soc-fast: context_window has type str"),
        (lambda e: e.update(context_window=True), "soc-fast: context_window has type bool"),
        (lambda e: e.update(parallel_tool_calls="no"), "soc-fast: parallel_tool_calls has type"),
        (lambda e: e.update(target="openrouter/x/y"), "soc-fast: target 'openrouter/x/y' is not"),
    ],
    ids=[
        "missing-field",
        "unknown-field",
        "unknown-capability",
        "no-capability",
        "window-not-int",
        "window-bool",
        "parallel-not-bool",
        "target-differs",
    ],
)
def test_broken_registry_entry_is_reported(
    change: Callable[[dict[str, Any]], object], expected: str
) -> None:
    registry = copy.deepcopy(load_registry("dev"))
    change(registry["soc-fast"])

    problems = registry_problems(registry, litellm_models("dev"))

    assert any(problem.startswith(expected) for problem in problems), problems


def test_missing_alias_is_reported() -> None:
    registry = copy.deepcopy(load_registry("prod"))
    del registry["soc-report"]

    assert registry_problems(registry, litellm_models("prod")) == [
        "aliases differ from LiteLLM: ['soc-report']"
    ]


def test_dev_model_that_differs_from_prod_is_reported() -> None:
    dev = copy.deepcopy(load_registry("dev"))
    dev["soc-verifier"]["target"] = "openrouter/qwen/qwen3.5-397b-a17b"

    assert dev_prod_mismatches(dev, load_registry("prod")) == [
        "soc-verifier: dev runs 'openrouter/qwen/qwen3.5-397b-a17b', "
        "prod runs 'hosted_vllm/Qwen/Qwen3.5-122B-A10B'"
    ]
