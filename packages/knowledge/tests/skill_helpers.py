"""Skill directories for the tests. Everything is synthetic: made-up rule and log source IDs,
team names and offense data."""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

import yaml

from ais0c_contracts import (
    CatalogContext,
    CatalogLogSource,
    CatalogMode,
    CatalogRule,
    EnrichmentContext,
    OffenseSnapshot,
)
from ais0c_knowledge.skills import content_hash

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
SKILLS_DIR: Final = REPO_ROOT / "skills"

WINDOWS_SECURITY: Final = "Microsoft Windows Security Event Log"
FORTIGATE: Final = "Fortinet FortiGate Security Gateway"
# Before every test skill's expiry (2027-10-01).
NOW: Final = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)

INSTRUCTIONS: Final = """## Purpose

Investigate failed logons from one source against many accounts.

## Steps

1. Count the distinct target accounts per source address.
2. Look for a successful logon from the same source.
"""


def manifest_data(**changes: object) -> dict[str, Any]:
    """A valid draft manifest, with `changes` applied on top."""
    data: dict[str, Any] = {
        "id": "test-skill",
        "version": "1.0.0",
        "status": "draft",
        "owner": "soc-engineering",
        "allowed_agent_roles": ["investigation"],
        "triggers": {
            "rule_ids": [100001],
            "log_source_types": [],
            "attack_techniques": ["T1110.003"],
        },
        "required_telemetry": [
            {
                "log_source_type": WINDOWS_SECURITY,
                "events": ["4625 failed logons with the target account and the source address"],
                "required": True,
            }
        ],
        "required_evidence": [
            {"id": "failures", "description": "The failed logons per source address"},
        ],
        "budgets": {"tokens": 50000, "tool_calls": 12, "wall_clock_seconds": 120},
        "output_schema": "InvestigationResult",
        "eval_suites": ["skill-test-skill"],
        "expires_at": date(2027, 10, 1),
        "content_hash": None,
        "approved_by": None,
    }
    data.update(changes)
    return data


def dump(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def write_skill(
    root: Path,
    data: dict[str, Any] | None = None,
    instructions: str = INSTRUCTIONS,
    *,
    approve: bool = False,
    directory: Path | None = None,
) -> Path:
    """Write a skill to `directory`, by default root/<id>/<version>/, and return the directory.

    With `approve`, the skill is approved the way skills/README.md describes: status and
    approved_by set, then content_hash.
    """
    data = manifest_data() if data is None else dict(data)
    if approve:
        data.update(status="approved", approved_by="reviewer-a", content_hash=None)
    skill_dir = directory or root / str(data["id"]) / str(data["version"])
    skill_dir.mkdir(parents=True)
    manifest = dump(data)
    if approve:
        manifest = dump({**data, "content_hash": content_hash(manifest, instructions)})
    (skill_dir / "skill.yaml").write_bytes(manifest.encode())
    (skill_dir / "instructions.md").write_bytes(instructions.encode())
    return skill_dir


def offense(
    *, rule_ids: list[int] | None = None, log_source_ids: list[int] | None = None
) -> OffenseSnapshot:
    return OffenseSnapshot(
        offense_id=4242,
        description="Multiple login failures from one source",
        offense_type="Source IP",
        offense_source="198.51.100.23",
        rule_ids=[] if rule_ids is None else rule_ids,
        rule_names=["TEST - many accounts, few attempts"],
        categories=["Authentication Failure"],
        magnitude=5,
        start_time=NOW,
        last_updated_time=NOW,
        event_count=40,
        log_source_ids=[] if log_source_ids is None else log_source_ids,
        source_ips=["198.51.100.23"],
        destination_ips=["192.0.2.10"],
        usernames=["test.user1"],
    )


def enrichment(
    *,
    log_source_types: Mapping[int, str | None] | None = None,
    rule_techniques: Mapping[int, Sequence[str] | None] | None = None,
) -> EnrichmentContext:
    """An enrichment whose catalog has these log source types and rule techniques."""
    return EnrichmentContext(
        catalog=CatalogContext(
            rules=[
                CatalogRule(
                    rule_id=rule_id,
                    mode=CatalogMode.ANALYZE,
                    attack_techniques=None if techniques is None else list(techniques),
                )
                for rule_id, techniques in (rule_techniques or {}).items()
            ],
            log_sources=[
                CatalogLogSource(log_source_id=log_source_id, type_name=type_name)
                for log_source_id, type_name in (log_source_types or {}).items()
            ],
        ),
        critical_asset_hits=[],
        ioc_hits=[],
        entity_resolutions=[],
    )
