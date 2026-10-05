"""Pydantic AI agents, prompt and manifest loading, the gateway client and structured output.

Tools are reached only through the gateway client: no QRadar or Falcon clients, no MCP SDK
and no write tools.
"""

from ais0c_agents.aql import (
    SUGGESTED_AQL_PROFILE,
    AqlRules,
    AqlRulesError,
    SuggestedAqlCheck,
    load_aql_rules,
)
from ais0c_agents.builder import AgentSpec, check_agent_config, create_agent
from ais0c_agents.evidence import (
    check_evidence,
    citable_evidence,
    context_alias,
    evidence_fields,
    render_context_evidence,
)
from ais0c_agents.fake_gateway import FakeGatewayClient
from ais0c_agents.gateway import GatewayClient, GatewayError, GatewayUnavailableError
from ais0c_agents.llm import (
    LITELLM_API_KEY_ENV,
    LITELLM_BASE_URL_ENV,
    MODEL_ALIASES,
    ModelAlias,
    ModelConfigError,
    build_model,
)
from ais0c_agents.manifest import AgentManifest, Budgets, ManifestError, load_manifest
from ais0c_agents.prompts import (
    NO_EVIDENCE_ID,
    KnowledgeItem,
    MaintenanceWindow,
    PromptError,
    PromptTemplate,
    load_agent_prompt,
    load_prompt,
    render_knowledge,
    render_org_context,
)
from ais0c_agents.registry import (
    ModelRegistry,
    ModelRegistryEntry,
    ModelRegistryError,
    load_model_registry,
)
from ais0c_agents.runner import AgentRun, prompt_tool_budget, run_agent, usage_limits
from ais0c_agents.skills import NO_SKILL, SkillEvidence, SkillInput, SkillTelemetry, render_skill
from ais0c_agents.toolset import RunDeps, ToolsetProfile, ToolSpec
from ais0c_agents.triage import TriageAgent, TriageOutput, TriageTask, build_triage_agent

__all__ = [
    "LITELLM_API_KEY_ENV",
    "LITELLM_BASE_URL_ENV",
    "MODEL_ALIASES",
    "NO_EVIDENCE_ID",
    "NO_SKILL",
    "SUGGESTED_AQL_PROFILE",
    "AgentManifest",
    "AgentRun",
    "AgentSpec",
    "AqlRules",
    "AqlRulesError",
    "Budgets",
    "FakeGatewayClient",
    "GatewayClient",
    "GatewayError",
    "GatewayUnavailableError",
    "KnowledgeItem",
    "MaintenanceWindow",
    "ManifestError",
    "ModelAlias",
    "ModelConfigError",
    "ModelRegistry",
    "ModelRegistryEntry",
    "ModelRegistryError",
    "PromptError",
    "PromptTemplate",
    "RunDeps",
    "SkillEvidence",
    "SkillInput",
    "SkillTelemetry",
    "SuggestedAqlCheck",
    "ToolSpec",
    "ToolsetProfile",
    "TriageAgent",
    "TriageOutput",
    "TriageTask",
    "build_model",
    "build_triage_agent",
    "check_agent_config",
    "check_evidence",
    "citable_evidence",
    "context_alias",
    "create_agent",
    "evidence_fields",
    "load_agent_prompt",
    "load_aql_rules",
    "load_manifest",
    "load_model_registry",
    "load_prompt",
    "prompt_tool_budget",
    "render_context_evidence",
    "render_knowledge",
    "render_org_context",
    "render_skill",
    "run_agent",
    "usage_limits",
]
