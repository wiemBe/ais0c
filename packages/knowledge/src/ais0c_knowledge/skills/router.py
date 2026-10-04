"""The skill router (architecture §7, "Seçim"; decision T-21).

It lists the candidate skills for one offense. The Orchestrator may choose only from this list,
and with an empty list the investigation runs without a skill. The workflow later checks the
chosen skill's role, status, version and budget (T-026).
"""

from collections.abc import Collection, Mapping
from datetime import datetime

from ais0c_contracts import OffenseSnapshot, SkillRef
from ais0c_knowledge.skills.loader import SkillRegistry
from ais0c_knowledge.skills.manifest import AgentRole


def candidate_skills(
    registry: SkillRegistry,
    offense: OffenseSnapshot,
    *,
    agent_role: AgentRole,
    log_source_types: Mapping[int, str],
    attack_techniques: Collection[str],
    now: datetime,
) -> list[SkillRef]:
    """The skills an agent in `agent_role` may use on `offense`, sorted by skill ID.

    A skill is a candidate when the latest approved version of it
    - allows `agent_role`,
    - has not expired at `now`, and
    - has a trigger the offense matches: one of the offense's rule IDs, the type of one of its
      log sources, or one of its ATT&CK techniques. Each kind of trigger is enough on its own.

    Drafts are never candidates. A newer approved version replaces every older one, so when it
    cannot be used the skill is left out rather than falling back to an older version.

    `log_source_types` maps log source IDs to QRadar log source type names (the catalog keeps
    them in catalog_log_sources.type_name); the types of log sources that are not the offense's
    are ignored. `attack_techniques` are the ATT&CK technique IDs the offense is tagged with,
    compared exactly. OffenseSnapshot and EnrichmentContext carry neither yet, so the caller
    passes them.

    Deterministic: the list depends only on the arguments. Nothing is read from the clock, the
    disk or the network; `now` must be timezone-aware.
    """
    if now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    rule_ids = frozenset(offense.rule_ids)
    types = frozenset(
        log_source_types[log_source_id]
        for log_source_id in offense.log_source_ids
        if log_source_id in log_source_types
    )
    techniques = frozenset(attack_techniques)
    return [
        skill.ref
        for skill in registry.latest_approved()
        if skill.usable_by(agent_role, now)
        and skill.manifest.triggers.matches(
            rule_ids=rule_ids, log_source_types=types, attack_techniques=techniques
        )
    ]
