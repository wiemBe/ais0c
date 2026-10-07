"""Turn a recorded dev chain run into a scenario file (T-053 criterion 5).

    python -m ais0c_harness.eval scenario --run case-35-reporting-1 \\
        --suite reporting-gold --id rep-01-dcsync-tp \\
        --out harness/suites/reporting-gold/rep-01-dcsync-tp.yaml

The command reads the dev database (AIS0C_DATABASE_URL, read-only) and writes one scenario
draft. The database keeps each chain run's contract AgentTask and its result, and the evidence
rows the run cited — not the offense snapshot and not the enrichment. The scenario's offense is
therefore rebuilt: identifiers from the case row and the triage run's evidence excerpts (the
tool results' masked JSON), times from the runs' windows, the rest from the triage result's
claims. Every value the database cannot hold becomes a `TODO(author)` note at the top of the
file; the notes are the author's checklist before the scenario joins its suite.

Addresses are anonymized as T-052/T-70 do it: every IPv4 outside the RFC 5737 documentation
ranges maps deterministically to a `198.51.100.x` address (then `192.0.2.x`, then
`203.0.113.x` on an octet collision) and every other IPv6 to `2001:db8::n`, the same input
address always to the same output. Lab DNS-shaped names map to `example.com` names; the lab's
synthetic account and host names (svc_backup, DC-LAB-01) stay, as the task allows. The
anonymizer sees the whole scenario dict, claims and excerpts included, so a claim that quotes
an address is anonymized with it. An anonymized text may still hold a shorthand such as
".101" (for the address named a few words earlier); that is not an IP address, and the repo
test that scans the scenarios for addresses (test_scenario_addresses.py) does not flag it.

The draft is a starting point: an author reviews the TODO notes, writes the `expect` block and
the title, and only then does the file load as part of its suite.
"""

import ipaddress
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_activities.settings import CaseSettings
from ais0c_contracts import (
    CaseReport,
    CatalogContext,
    CatalogLogSource,
    CatalogRule,
    EnrichmentContext,
    InvestigationResult,
    OffenseSnapshot,
    TriageResult,
    UrgentEvent,
)
from ais0c_storage.models import (
    AgentRunRow,
    CaseRow,
    CatalogLogSourceRow,
    CatalogRuleRow,
    EvidenceRow,
    ToolCallRow,
)

ALLOWED_V4_PREFIXES: Final[tuple[str, ...]] = ("192.0.2.", "198.51.100.", "203.0.113.")
"""The RFC 5737 documentation ranges an address may keep."""
V4_POOLS: Final[tuple[str, ...]] = ("198.51.100.", "192.0.2.", "203.0.113.")
"""The documentation prefixes an anonymized address is mapped to, in order."""
DOCUMENTATION_V6: Final = ipaddress.IPv6Network("2001:db8::/32")
IPv4: Final = re.compile(r"(?<![\w.])(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?![\w.])")
# Anything made of hex digits and at least two colons; `ipaddress` decides whether it
# is an address, so a clock time (20:14:18) stays.
IPv6: Final = re.compile(r"(?<![\w:.])(?=[0-9A-Fa-f]*:[0-9A-Fa-f]*:)[0-9A-Fa-f:]{3,}(?![\w:.])")
LAB_DOMAIN: Final = re.compile(
    r"(?<![\w.-])([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)\.((?:bank|lab|local|internal|test)\.example"
    r"|example\.(?:com|org|net))(?![\w.-])",
    re.IGNORECASE,
)
TODO_PREFIX: Final = "TODO(author): "
MAX_NOTES: Final = 40
MAX_EXCERPT: Final = 500
"""The contract's EvidenceRef excerpt limit; the DB row may hold more."""


def anonymize_text(text: str, mapping: dict[str, str]) -> str:
    """`text` with its addresses and lab domains replaced, deterministically.

    `mapping` collects what was replaced, so a suite's scenarios stay consistent with each
    other and the same input address always maps to the same output.
    """

    def address(match: re.Match[str]) -> str:
        found = match.group(0)
        if found in mapping:
            return mapping[found]
        if ":" in found:
            try:
                parsed = ipaddress.IPv6Address(found)
            except ValueError:
                return found
            if parsed in DOCUMENTATION_V6:
                return found
            replacement = f"2001:db8::{len(mapping) + 1:x}"
        elif any(found.startswith(prefix) for prefix in ALLOWED_V4_PREFIXES):
            return found
        else:
            replacement = _fresh_v4(found.rsplit(".", 1)[-1], mapping)
        mapping[found] = replacement
        return replacement

    def domain(match: re.Match[str]) -> str:
        return mapping.setdefault(match.group(0), f"{match.group(1)}.example.com")

    return IPv4.sub(address, IPv6.sub(address, LAB_DOMAIN.sub(domain, text)))


def _fresh_v4(host: str, mapping: dict[str, str]) -> str:
    taken = set(mapping.values())
    for prefix in V4_POOLS:
        candidate = f"{prefix}{host}"
        if candidate not in taken:
            return candidate
    return f"198.51.100.{1 + len(mapping) % 254}"


def anonymize(value: JsonValue, mapping: dict[str, str]) -> JsonValue:
    """Every string of a JSON value, at any depth, through `anonymize_text`."""
    if isinstance(value, str):
        return anonymize_text(value, mapping)
    if isinstance(value, list):
        return [anonymize(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: anonymize(item, mapping) for key, item in value.items()}
    return value


# --- what one chain holds ---------------------------------------------------------------------


@dataclass(frozen=True)
class ChainRecord:
    """The runs of one case and the evidence the Reporting run cited."""

    case: CaseRow
    triage: AgentRunRow
    reporting: AgentRunRow
    investigation: AgentRunRow | None
    evidence: list[EvidenceRow]
    """The reporting run's context evidence, in its `context_refs` order."""
    triage_evidence: list[EvidenceRow]
    """The evidence the triage run's tool calls returned, in call order."""


async def collect(session: AsyncSession, run_id: str) -> ChainRecord | None:
    """The chain of `run_id` (a triage, orchestrator, investigation or reporting run)."""
    run = await session.get(AgentRunRow, run_id)
    if run is None or run.case_id is None:
        return None
    case = await session.get(CaseRow, run.case_id)
    if case is None:
        return None
    runs = (
        (await session.execute(select(AgentRunRow).where(AgentRunRow.case_id == run.case_id)))
        .scalars()
        .all()
    )
    triage = next((item for item in runs if item.agent_id == "triage"), None)
    if triage is None:
        return None
    investigation = next((item for item in runs if item.agent_id == "investigation"), None)
    reporting = next((item for item in runs if item.agent_id == "reporting"), runs[-1])
    return ChainRecord(
        case=case,
        triage=triage,
        reporting=reporting,
        investigation=investigation,
        evidence=await _evidence_of(session, reporting.task.context_refs),
        triage_evidence=await _run_evidence(session, triage.run_id),
    )


async def _evidence_of(session: AsyncSession, evidence_ids: Sequence[str]) -> list[EvidenceRow]:
    if not evidence_ids:
        return []
    rows = (
        (
            await session.execute(
                select(EvidenceRow).where(EvidenceRow.evidence_id.in_(evidence_ids))
            )
        )
        .scalars()
        .all()
    )
    found = {row.evidence_id: row for row in rows}
    return [found[evidence_id] for evidence_id in evidence_ids if evidence_id in found]


async def _run_evidence(session: AsyncSession, run_id: str) -> list[EvidenceRow]:
    """The evidence the run's tool calls returned, in call order."""
    calls = (
        (
            await session.execute(
                select(ToolCallRow)
                .where(ToolCallRow.run_id == run_id, ToolCallRow.evidence_id.is_not(None))
                .order_by(ToolCallRow.created_at)
            )
        )
        .scalars()
        .all()
    )
    return await _evidence_of(session, [call.evidence_id for call in calls if call.evidence_id])


# --- the rebuilt offense and the enrichment -----------------------------------------------------


def offense_of(chain: ChainRecord, notes: list[str]) -> OffenseSnapshot:
    """The offense as the database lets it be rebuilt; every guess is a TODO note.

    The snapshot the agents saw is workflow state, not a database row. What the database holds:
    the case's offense ID, the triage evidence's masked excerpts (the tool results' JSON, cut at
    500 characters), the runs' windows and the triage result's claims. The claim texts and the
    excerpts carry the identifiers; the window of the run after triage starts at the offense's
    start time, unless the offense was younger than a minute then.
    """
    result = _triage_result(chain)
    excerpts = "\n".join(row.excerpt for row in chain.triage_evidence)
    claims = "\n".join(claim.text for claim in result.claims)
    offense_excerpt = "\n".join(
        row.excerpt for row in chain.triage_evidence if row.identifiers.get("tool") == "get_offense"
    )
    rule_rows = [row for row in chain.triage_evidence if row.identifiers.get("tool") == "get_rule"]
    start, start_note = _offense_start(chain)
    if start_note:
        notes.append(start_note)
    addresses = list(dict.fromkeys(IPv4.findall(f"{claims}\n{excerpts}")))
    if not addresses:
        notes.append("the offense's addresses: none found in the claims or excerpts; fill them in.")
    users = list(
        dict.fromkeys(re.findall(r"(?:user(?:name)?|account) '([A-Za-z0-9_.-]+)'", claims))
    )
    event_count = (
        _number(offense_excerpt, r'"event_count":\s*(\d+)')
        or _number(claims, r"event_count(?: of)? (\d+)")
        or 0
    )
    if event_count == 0:
        notes.append("event_count: not in the excerpts or claims; read it from the offense.")
    magnitude = _number(offense_excerpt, r'"magnitude":\s*(\d+)')
    if magnitude is None:
        magnitude = _number(claims, r"magnitude (\d+)") or 0
        notes.append(
            "magnitude: from the claims where they say so, else 0; it is informational only (D-24)."
        )
    description = _string(offense_excerpt, "description")
    if not description:
        notes.append("description: the excerpt is cut before it; copy it from the offense.")
    rule_ids = [
        int(row.identifiers["rule_id"]) for row in rule_rows if "rule_id" in row.identifiers
    ]
    rule_names = [name for row in rule_rows if (name := _string(row.excerpt, "name"))]
    if not rule_names:
        notes.append("rule_names: not in the excerpts; copy them from the offense.")
    log_source_ids = [
        int(row.identifiers["log_source_id"])
        for row in chain.triage_evidence
        if row.identifiers.get("tool") == "get_log_source" and "log_source_id" in row.identifiers
    ]
    if not log_source_ids:
        log_source_ids = _numbers(claims, r"[Ll]og source (\d+)")
        if not log_source_ids:
            notes.append("log_source_ids: not in the excerpts or claims; fill them in.")
    categories = _string_list(offense_excerpt, "categories")
    if not categories:
        notes.append("categories: the excerpt is cut before them; copy them from the offense.")
    notes.append("offense_type: not stored; copy it from the offense.")
    return OffenseSnapshot(
        offense_id=chain.case.offense_id or 0,
        description=description,
        offense_type="",
        offense_source=users[0] if users else "",
        rule_ids=rule_ids,
        rule_names=rule_names,
        categories=categories,
        magnitude=magnitude,
        start_time=start,
        last_updated_time=chain.reporting.task.time_window.start,
        event_count=event_count,
        log_source_ids=log_source_ids,
        source_ips=addresses,
        destination_ips=addresses,
        usernames=users,
    )


async def enrichment_of(
    chain: ChainRecord, session: AsyncSession, notes: list[str]
) -> EnrichmentContext:
    """The catalog's current rows for the offense's rules and log sources.

    The enrichment the run saw is workflow state; the catalog tables hold the operators' current
    answers, and the recorded runs are recent enough to share them. The floor level is the
    enrichment's own computation, not stored: it is None and the author re-derives it from the
    catalog rows (T-015).
    """
    rule_ids = [
        int(row.identifiers["rule_id"])
        for row in chain.triage_evidence
        if row.identifiers.get("tool") == "get_rule" and "rule_id" in row.identifiers
    ]
    source_ids = [
        int(row.identifiers["log_source_id"])
        for row in chain.triage_evidence
        if row.identifiers.get("tool") == "get_log_source" and "log_source_id" in row.identifiers
    ]
    rule_rows = (
        (
            await session.execute(
                select(CatalogRuleRow).where(CatalogRuleRow.rule_id.in_(rule_ids or [0]))
            )
        )
        .scalars()
        .all()
    )
    source_rows = (
        (
            await session.execute(
                select(CatalogLogSourceRow).where(
                    CatalogLogSourceRow.log_source_id.in_(source_ids or [0])
                )
            )
        )
        .scalars()
        .all()
    )
    notes.append("floor_level: recompute it from the catalog rows and the critical assets (T-015).")
    return EnrichmentContext(
        catalog=CatalogContext(
            rules=[
                CatalogRule(
                    rule_id=row.rule_id,
                    mode=row.mode,
                    min_level=row.min_level,
                    context_note=row.context_note,
                    attack_techniques=list(row.attack_techniques) or None,
                )
                for row in rule_rows
            ],
            log_sources=[
                CatalogLogSource(
                    log_source_id=row.log_source_id,
                    type_name=row.type_name,
                    description=row.description,
                    criticality=row.criticality,
                    context_note=row.context_note,
                )
                for row in source_rows
            ],
        ),
        critical_asset_hits=[],
        ioc_hits=[],
        entity_resolutions=[],
        floor_level=None,
    )


def _triage_result(chain: ChainRecord) -> TriageResult:
    result = chain.triage.result
    if not isinstance(result, TriageResult):
        raise ValueError(f"{chain.triage.run_id} has no TriageResult")
    return result


def _offense_start(chain: ChainRecord) -> tuple[datetime, str]:
    """The offense's start time: the window of the run after triage starts there
    (evaluation_window), and it does unless the offense was younger than a minute then."""
    after = chain.investigation or chain.reporting
    candidate = after.task.time_window.start
    if candidate > chain.triage.task.time_window.end:
        return (
            candidate,
            "start_time: the offense may have been under a minute old when this window opened, "
            "which caps the window start at then minus one minute; verify against the offense.",
        )
    return candidate, ""


def _number(text: str, pattern: str) -> int | None:
    found = re.search(pattern, text)
    return int(found.group(1)) if found else None


def _numbers(text: str, pattern: str) -> list[int]:
    return [int(found) for found in re.findall(pattern, text)]


def _string(text: str, field_name: str) -> str:
    found = re.search(rf'"{field_name}":\s*"((?:[^"\\]|\\.)*)"', text)
    return found.group(1).replace("\\n", " ").replace('\\"', '"') if found else ""


def _string_list(text: str, field_name: str) -> list[str]:
    found = re.search(rf'"{field_name}":\s*\[([^\]]*)\]', text)
    if not found:
        return []
    return re.findall(r'"([^"]*)"', found.group(1))


# --- the scenario drafts ------------------------------------------------------------------------


def orchestrator_scenario(
    chain: ChainRecord, *, root: Path, suite: str, scenario_id: str, notes: list[str]
) -> dict[str, Any]:
    """A scenario draft for the Orchestrator, from the case's triage run (T-053 criterion 1)."""
    result = _triage_result(chain)
    notes.append("candidates: the recorded run had none; a skill scenario is written by hand.")
    return {
        "id": scenario_id,
        "suite": suite,
        "agent": "orchestrator",
        "title": TODO_PREFIX + "one line",
        "description": TODO_PREFIX + "what the scenario holds and what it expects.",
        "input": {
            "triage": {
                "verdict": result.verdict.value,
                "confidence": result.confidence.value,
                "ai_level": result.ai_level.value,
                "needs_investigation": result.needs_investigation,
                "investigation_focus": list(result.investigation_focus),
                "data_gaps": [gap.model_dump(mode="json") for gap in result.data_gaps],
                "injection_suspected": result.injection_suspected,
            },
            "offense": offense_of(chain, notes).model_dump(mode="json"),
            "candidates": [],
            "agents": _plan_agents(root),
            "plan_budget": _plan_budget(),
            "evaluated_at": _after_window(chain),
        },
        "expect": {
            "expected_agents": ["investigation", "verification"],
            "injection_suspected": result.injection_suspected,
        },
    }


def reporting_scenario(
    chain: ChainRecord, *, suite: str, scenario_id: str, agent: str, notes: list[str]
) -> dict[str, Any]:
    """A scenario draft for the Reporting agent (or the Turkish suite), from the case's runs."""
    result = chain.reporting.result
    if not isinstance(result, CaseReport):
        raise ValueError(f"{chain.reporting.run_id} has no CaseReport result")
    candidates: list[UrgentEvent] = []
    if chain.investigation is not None and isinstance(
        chain.investigation.result, InvestigationResult
    ):
        candidates = list(chain.investigation.result.urgent_event_candidates)
    else:
        notes.append(
            "urgent_event_candidates: no recorded investigation result; write them by hand."
        )
    return {
        "id": scenario_id,
        "suite": suite,
        "agent": agent,
        "title": TODO_PREFIX + "one line",
        "description": TODO_PREFIX + "what the scenario holds and what it expects.",
        "input": {
            "decision": {
                "verdict": result.verdict.value,
                "confidence": result.confidence.value,
                "notify_level": result.notify_level.value,
            },
            "claims": [claim.model_dump(mode="json") for claim in result.claims],
            "evidence": [_evidence_ref(row) for row in chain.evidence],
            "urgent_event_candidates": [event.model_dump(mode="json") for event in candidates],
            "data_gaps": [gap.model_dump(mode="json") for gap in result.data_gaps],
            "offense": offense_of(chain, notes).model_dump(mode="json"),
            "enrichment": _enrichment_placeholder(),
            "evaluated_at": _after_window(chain),
        },
        "expect": {},
    }


async def build_scenario(
    session: AsyncSession,
    *,
    root: Path,
    run_id: str,
    suite: str,
    scenario_id: str,
    kind: str,
) -> tuple[str, str]:
    """Collect the chain of `run_id` and render its scenario draft.

    Returns the file text and a one-line summary. The draft is validated against its suite's
    scenario model before it is rendered; writing the file is the caller's (the CLI's) job.
    """
    chain = await collect(session, run_id)
    if chain is None:
        raise ValueError(f"no chain run {run_id} in the database")
    agent = {"orchestrator": "orchestrator", "turkish": "turkish-quality"}.get(kind, "reporting")
    notes: list[str] = []
    if agent == "orchestrator":
        scenario = orchestrator_scenario(
            chain, root=root, suite=suite, scenario_id=scenario_id, notes=notes
        )
    else:
        enrichment = await enrichment_of(chain, session, notes)
        scenario = reporting_scenario(
            chain, suite=suite, scenario_id=scenario_id, agent=agent, notes=notes
        )
        scenario["input"]["enrichment"] = enrichment.model_dump(mode="json")
    mapping: dict[str, str] = {}
    data = anonymize(scenario, mapping)
    # The draft must load as its suite's scenario before it is rendered.
    from ais0c_harness.eval.suites import adapter_type

    adapter_type(agent).scenario_type.model_validate(data)
    text = render(scenario, run_id=run_id, mapping=mapping, notes=notes)
    return text, f"{len(notes)} TODO notes, {len(mapping)} values anonymized"


def render(
    scenario: dict[str, Any], *, run_id: str, mapping: dict[str, str], notes: list[str]
) -> str:
    """The YAML with the provenance and the TODO notes as header comments.

    `scenario` is written as it was built; `mapping` says what the written file anonymized.
    """
    body = yaml.safe_dump(
        anonymize(scenario, dict(mapping)),
        sort_keys=False,
        allow_unicode=True,
        width=100,
        default_flow_style=False,
    )
    provenance = [
        f"# Scenario draft from the recorded dev run {run_id} (T-053 criterion 5).",
        "# Addresses and lab domains are anonymized in the file below:",
    ]
    provenance += [f"#   {old} -> {new}" for old, new in mapping.items()]
    if not mapping:
        provenance.append("#   (nothing needed anonymizing)")
    lines = ["\n".join(provenance), ""]
    lines += [f"# {TODO_PREFIX}{note}" for note in notes[:MAX_NOTES]]
    if len(notes) > MAX_NOTES:
        lines.append(f"# ... and {len(notes) - MAX_NOTES} more notes")
    return "\n".join(lines) + "\n\n" + body


# --- the pieces the drafts share -----------------------------------------------------------------


def _enrichment_placeholder() -> dict[str, Any]:
    """A placeholder the caller replaces with the catalog's rows; the draft validates either way."""
    return EnrichmentContext(
        catalog=CatalogContext(rules=[], log_sources=[]),
        critical_asset_hits=[],
        ioc_hits=[],
        entity_resolutions=[],
        floor_level=None,
    ).model_dump(mode="json")


def _plan_agents(root: Path) -> list[dict[str, Any]]:
    """The plan agents' manifest budgets, from the repository's own manifests (T-41)."""
    from ais0c_agents import load_manifest, load_model_registry

    registry = load_model_registry(root / "config" / "models" / "registry.dev.yaml")
    return [
        {
            "agent_id": (
                manifest := load_manifest(root / "config" / "agents" / f"{name}.yaml", registry)
            ).id,
            "budgets": {
                "tokens": manifest.budgets.tokens,
                "tool_calls": manifest.budgets.tool_calls,
                "wall_clock_seconds": manifest.budgets.wall_clock_seconds,
            },
        }
        for name in ("investigation", "verification")
    ]


def _plan_budget() -> dict[str, int]:
    """The plan budget (CaseSettings defaults; decision T-41)."""
    settings = CaseSettings.from_env({"AIS0C_CASE_URL_BASE": "http://localhost"})
    budget = settings.plan_budget
    return {"tokens": budget.tokens, "tool_calls": budget.tool_calls, "seconds": budget.seconds}


def _evidence_ref(row: EvidenceRow) -> dict[str, Any]:
    return {
        "evidence_id": row.evidence_id,
        "source": row.source.value,
        "query_hash": row.query_hash,
        "query_text": row.query_text,
        "time_start": row.time_start.isoformat(),
        "time_end": row.time_end.isoformat(),
        "identifiers": dict(row.identifiers),
        # The contract's EvidenceRef carries at most this much excerpt (the DB row may hold more).
        "excerpt": row.excerpt[:MAX_EXCERPT],
        "retrieved_at": row.retrieved_at.isoformat(),
    }


def _after_window(chain: ChainRecord) -> str:
    """`evaluated_at`: the moment the run after triage ran (its window end)."""
    after = chain.investigation or chain.reporting
    return after.task.time_window.end.isoformat()
