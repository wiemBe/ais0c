"""Pydantic AI agents, prompt and manifest loading, the gateway client and structured output.

Tools are reached only through the gateway client: no QRadar or Falcon clients, no MCP SDK
and no write tools.
"""

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
from ais0c_agents.runner import AgentRun
from ais0c_agents.toolset import RunDeps, ToolsetProfile, ToolSpec
from ais0c_agents.triage import TriageAgent, TriageOutput, TriageTask, build_triage_agent

__all__ = [
    "LITELLM_API_KEY_ENV",
    "LITELLM_BASE_URL_ENV",
    "MODEL_ALIASES",
    "NO_EVIDENCE_ID",
    "AgentManifest",
    "AgentRun",
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
    "ToolSpec",
    "ToolsetProfile",
    "TriageAgent",
    "TriageOutput",
    "TriageTask",
    "build_model",
    "build_triage_agent",
    "load_agent_prompt",
    "load_manifest",
    "load_model_registry",
    "load_prompt",
    "render_knowledge",
    "render_org_context",
]
