"""T-025 criterion 8: the Reporting agent against the dev stack, once, on a synthetic case.

Opt-in, because it needs the running stack's LiteLLM with the `soc-report` alias. From the
repository root:

    docker compose -f deploy/compose/docker-compose.dev.yaml up -d --wait litellm
    set -a; . deploy/compose/.env; set +a
    AIS0C_DEV_STACK=1 uv run pytest packages/agents/tests/test_reporting_dev_stack.py -s

The input is the synthetic case of `test_reporting.py`; nothing is read from a QRadar and
nothing is written anywhere. What the test prints is what the PR reports: the run's token use,
its duration and the summary itself (criterion 8).

The alias `soc-report` resolves through config/models/registry.dev.yaml; the registry's
`forced_tool_choice` decides whether the structured output is asked for with "required" or with
"auto". If the alias cannot produce the structured output, decision D-43 allows moving it to
the prod reporting model; the measurement and the reason go in the PR.
"""

import json
import os
from typing import Final

import pytest
from pydantic_ai.models import Model

from ais0c_agents import build_model, load_manifest, load_model_registry, load_prompt
from ais0c_agents.reporting import build_reporting_agent
from ais0c_contracts import CaseVerdict, Level, RunStatus
from ais0c_policy import new_nonce

from .helpers import REPO_ROOT, registry
from .test_reporting import REPORTING_PROMPT_PATH, SHARED_RULES, reporting_task, run_reporting

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("AIS0C_DEV_STACK") != "1",
        reason="dev stack test: start deploy/compose/docker-compose.dev.yaml's litellm service "
        "and set AIS0C_DEV_STACK=1",
    ),
]

ALIAS: Final = "soc-report"
MANIFEST_PATH = REPO_ROOT / "config/agents/reporting.yaml"
REGISTRY_PATH = REPO_ROOT / "config/models/registry.dev.yaml"
SUMMARY_MAX_LENGTH: Final = 400
"""`NoteContent.summary_tr`'s limit (contracts.md); the same summary goes into the note."""


def stack_model() -> Model:
    """The `soc-report` model of the running LiteLLM; skips when LiteLLM is not configured."""
    entry = load_model_registry(REGISTRY_PATH).get(ALIAS)
    if entry is None:
        pytest.skip(f"dev stack test: {ALIAS} is not in config/models/registry.dev.yaml")
    try:
        return build_model(ALIAS, forced_tool_choice=entry.forced_tool_choice)
    except Exception as error:  # noqa: BLE001 - the settings may be missing or invalid
        pytest.skip(f"dev stack test: LiteLLM is not configured ({type(error).__name__}: {error})")


def test_the_report_comes_back_in_turkish_within_the_limits() -> None:
    """Criterion 8: the real model behind the alias, one run, on the synthetic input."""
    agent = build_reporting_agent(
        manifest=load_manifest(MANIFEST_PATH, registry()),
        prompt=load_prompt(REPO_ROOT, REPORTING_PROMPT_PATH, shared_rules=SHARED_RULES),
        model=stack_model(),
    )

    run = run_reporting(agent, reporting_task(), nonce=new_nonce())

    print(
        json.dumps(
            {
                "status": run.status.value,
                "prompt_version": run.prompt_version,
                "prompt_hash": run.prompt_hash,
                "tokens": run.usage.tokens,
                "tool_calls": run.usage.tool_calls,
                "seconds": round(run.usage.seconds, 2),
                "summary_tr": None if run.result is None else run.result.summary_tr,
                "summary_characters": 0 if run.result is None else len(run.result.summary_tr),
                "urgent_events": 0 if run.result is None else len(run.result.urgent_events),
                "recommendations": 0 if run.result is None else len(run.result.recommendations),
                "error": run.error,
            },
            indent=2,
            ensure_ascii=False,
        )
    )

    assert run.status is RunStatus.COMPLETED, run.error
    assert run.result is not None
    # The report fits the QRadar note and the e-mail that carry it (T-045).
    assert 0 < len(run.result.summary_tr) <= SUMMARY_MAX_LENGTH
    # The decision is the workflow's, whatever the model answered.
    assert run.result.verdict is CaseVerdict.SUSPICIOUS
    assert run.result.notify_level is Level.HIGH
    # The agent has no tools, so a model request is the whole cost of a run.
    assert run.usage.tool_calls == 0
