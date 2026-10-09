"""T-067: the Orchestrator's candidate carries the summary of the skill's manifest."""

from pathlib import Path

from ais0c_activities.skills import candidate_skill
from ais0c_knowledge.skills import load_skills

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_candidate_carries_the_skill_summary() -> None:
    skills = {skill.manifest.id: skill for skill in load_skills(REPO_ROOT / "skills", mode="dev")}
    skill = skills["web-sql-injection"]

    candidate = candidate_skill(skill, agent_role="investigation")

    assert candidate.summary == skill.manifest.summary
    assert candidate.summary.startswith("SQL injection against a web application")
