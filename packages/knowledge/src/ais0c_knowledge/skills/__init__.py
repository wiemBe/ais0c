"""Skills: versioned investigation methods (architecture §7, "Skill'ler"; decision T-21).

A skill lives in skills/<id>/<version>/ as skill.yaml (the manifest) and instructions.md (English
instructions that become part of an agent's prompt). It tells an agent how to investigate one
kind of event and grants nothing: an agent's permissions are its toolset, the workflow policy
and the gateway policy.

- `load_skills(root, mode=...)` loads and checks every skill: manifest, layout, content hash,
  injection scan. Mode "prod" leaves drafts out.
- `candidate_skills(...)` is the deterministic router: approved skills in prod, and approved
  or draft skills in dev, when unexpired and allowed for an agent role (T-58).
- `python -m ais0c_knowledge.skills hash <dir>` prints the content hash an approver records.
"""

from ais0c_knowledge.skills.errors import (
    SkillError,
    SkillInjectionError,
    SkillIntegrityError,
    SkillPermissionError,
)
from ais0c_knowledge.skills.loader import (
    INSTRUCTIONS_FILE,
    MANIFEST_FILE,
    Mode,
    Skill,
    SkillRegistry,
    content_hash,
    load_skill,
    load_skills,
    read_content_hash,
)
from ais0c_knowledge.skills.manifest import (
    AGENT_RESULT_SCHEMAS,
    AgentRole,
    EvidenceRequirement,
    SkillBudgets,
    SkillManifest,
    SkillStatus,
    SkillTriggers,
    TelemetryRequirement,
    parse_manifest,
)
from ais0c_knowledge.skills.router import candidate_skills
from ais0c_knowledge.skills.scan import Finding, scan_instructions, scan_text

__all__ = [
    "AGENT_RESULT_SCHEMAS",
    "INSTRUCTIONS_FILE",
    "MANIFEST_FILE",
    "AgentRole",
    "EvidenceRequirement",
    "Finding",
    "Mode",
    "Skill",
    "SkillBudgets",
    "SkillError",
    "SkillInjectionError",
    "SkillIntegrityError",
    "SkillManifest",
    "SkillPermissionError",
    "SkillRegistry",
    "SkillStatus",
    "SkillTriggers",
    "TelemetryRequirement",
    "candidate_skills",
    "content_hash",
    "load_skill",
    "load_skills",
    "parse_manifest",
    "read_content_hash",
    "scan_instructions",
    "scan_text",
]
