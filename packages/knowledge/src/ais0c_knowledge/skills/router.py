"""The skill router (architecture §7, "Seçim"; decisions T-21 and T-26).

It lists the candidate skills for one offense. The Orchestrator may choose only from this list,
and with an empty list the investigation runs without a skill. The workflow later checks the
chosen skill's role, status, version and budget (T-026).
"""

from datetime import datetime

from ais0c_contracts import EnrichmentContext, OffenseSnapshot, SkillRef
from ais0c_knowledge.skills.loader import SkillRegistry
from ais0c_knowledge.skills.manifest import AgentRole


def candidate_skills(
    registry: SkillRegistry,
    offense: OffenseSnapshot,
    enrichment: EnrichmentContext,
    *,
    agent_role: AgentRole,
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

    The log source types and ATT&CK techniques come from the offense's Analysis Catalog entries
    in `enrichment`: the `type_name` of its log sources and the `attack_techniques` of its
    rules (T-26). Techniques are compared exactly. Catalog entries of rules or log sources that
    are not the offense's are ignored.

    Deterministic: the list depends only on the arguments. Nothing is read from the clock, the
    disk or the network; `now` must be timezone-aware.
    """
    if now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    rule_ids = frozenset(offense.rule_ids)
    log_source_ids = frozenset(offense.log_source_ids)
    types = frozenset(
        source.type_name
        for source in enrichment.catalog.log_sources
        if source.type_name is not None and source.log_source_id in log_source_ids
    )
    techniques = frozenset(
        technique
        for rule in enrichment.catalog.rules
        if rule.rule_id in rule_ids
        for technique in rule.attack_techniques or ()
    )
    return [
        skill.ref
        for skill in registry.latest_approved()
        if skill.usable_by(agent_role, now)
        and skill.manifest.triggers.matches(
            rule_ids=rule_ids, log_source_types=types, attack_techniques=techniques
        )
    ]
