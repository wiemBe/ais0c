"""The eval harness's runner (T-030, docs/agent-harness.md §2, §5-§8).

The runner plays a suite's scenarios against an agent built as the case worker builds it, in
`fixture` mode: the real model through LiteLLM, the gateway's tool profile, and a fixture
gateway that answers with the scenario's tool results (decision T-64). It scores every run with
deterministic checks, judges the security suites with pass^k and writes a versioned report
with the hard gate table. `gate` compares two reports (the model gate, B2) and `releases` lists
the model releases that changed since the last recorded runs.

Run it as `python -m ais0c_harness.eval` (cli.py).
"""

from ais0c_harness.eval.adapter import (
    AgentAdapter,
    Attempt,
    EvaluatorIdentity,
    RecordingModel,
    infra_failure,
)
from ais0c_harness.eval.config import (
    AgentConfig,
    ConfigError,
    litellm_model,
    load_agent_config,
)
from ais0c_harness.eval.evaluate import Check, Evaluation, RunMetrics
from ais0c_harness.eval.fixture_gateway import UNSCRIPTED_RESULT, FixtureGateway, GatewayExchange
from ais0c_harness.eval.gate import GateResult, compare_reports
from ais0c_harness.eval.orchestrator import (
    OrchestratorAdapter,
    OrchestratorExpectation,
    OrchestratorInput,
    OrchestratorScenario,
    orchestrator_checks,
)
from ais0c_harness.eval.releases import AgentSuites, agents_by_alias, describe_release_changes
from ais0c_harness.eval.report import (
    HardGate,
    Report,
    RunEnvelope,
    RunFile,
    RunRecord,
    ScenarioReport,
    SuiteReport,
    load_report,
    write_report,
)
from ais0c_harness.eval.reporting import (
    ReportingAdapter,
    ReportingExpectation,
    ReportingInput,
    ReportingScenario,
    reporting_checks,
)
from ais0c_harness.eval.runner import EvalRun, Job, RunOptions, run_eval, run_jobs
from ais0c_harness.eval.scenario import Expectation, ScenarioBase
from ais0c_harness.eval.scripted import scripted_model
from ais0c_harness.eval.suites import (
    ScenarioFile,
    Suite,
    SuiteError,
    load_scenario,
    load_suite,
    load_suites,
)
from ais0c_harness.eval.triage import (
    TriageAdapter,
    TriageExpectation,
    TriageInput,
    TriageScenario,
    evaluate_triage,
)
from ais0c_harness.eval.turkish import (
    EvaluatorError,
    EvaluatorScores,
    TurkishQualityAdapter,
    TurkishQualityScenario,
    evaluator_identity,
    run_evaluator,
    score_checks,
    turkish_checks,
)

__all__ = [
    "UNSCRIPTED_RESULT",
    "AgentAdapter",
    "AgentConfig",
    "AgentSuites",
    "Attempt",
    "Check",
    "ConfigError",
    "EvalRun",
    "Evaluation",
    "EvaluatorError",
    "EvaluatorIdentity",
    "EvaluatorScores",
    "Expectation",
    "FixtureGateway",
    "GateResult",
    "GatewayExchange",
    "HardGate",
    "Job",
    "OrchestratorAdapter",
    "OrchestratorExpectation",
    "OrchestratorInput",
    "OrchestratorScenario",
    "RecordingModel",
    "Report",
    "ReportingAdapter",
    "ReportingExpectation",
    "ReportingInput",
    "ReportingScenario",
    "RunEnvelope",
    "RunFile",
    "RunMetrics",
    "RunOptions",
    "RunRecord",
    "ScenarioBase",
    "ScenarioFile",
    "ScenarioReport",
    "Suite",
    "SuiteError",
    "SuiteReport",
    "TriageAdapter",
    "TriageExpectation",
    "TriageInput",
    "TriageScenario",
    "TurkishQualityAdapter",
    "TurkishQualityScenario",
    "agents_by_alias",
    "compare_reports",
    "describe_release_changes",
    "evaluate_triage",
    "evaluator_identity",
    "infra_failure",
    "litellm_model",
    "load_agent_config",
    "load_report",
    "load_scenario",
    "load_suite",
    "load_suites",
    "orchestrator_checks",
    "reporting_checks",
    "run_eval",
    "run_evaluator",
    "run_jobs",
    "score_checks",
    "scripted_model",
    "turkish_checks",
    "write_report",
]
