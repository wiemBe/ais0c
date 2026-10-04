"""Valid, synthetic JSON payloads for every contract model.

Each function returns a fresh dict, so a test can change it freely. IPs are from RFC 5737
ranges and domains are example.com.
"""

from collections.abc import Callable

from ais0c_contracts import (
    AgentResult,
    AgentTask,
    Backtest,
    Budget,
    CasePlan,
    CaseReport,
    CatalogContext,
    CatalogLogSource,
    CatalogRule,
    Claim,
    ContractModel,
    CoverageEntry,
    CriticalAssetHit,
    DataGap,
    DetectionProposal,
    Disagreement,
    EmailMessage,
    EnrichmentContext,
    EntityResolution,
    EvidenceRef,
    HuntReport,
    HuntRequest,
    HuntScope,
    HuntVersions,
    HypothesisResult,
    InvestigationHypothesis,
    InvestigationResult,
    IocHit,
    ModelRelease,
    NoteContent,
    OffenseSnapshot,
    OperatorFeedback,
    PlanStep,
    Recommendation,
    SkillRef,
    TimelineEntry,
    TimeWindow,
    ToolCoverage,
    ToolIntent,
    ToolResult,
    TriageResult,
    TuningProposal,
    UrgentEvent,
    Usage,
    VerificationResult,
)

Payload = dict[str, object]

START = "2026-10-02T10:00:00Z"
END = "2026-10-02T11:00:00Z"
EVIDENCE_ID = "ev_01JB3K7Q9X"
CONTENT_HASH = "sha256:" + "0123456789abcdef" * 4


def time_window() -> Payload:
    return {"start": START, "end": END}


def budget() -> Payload:
    return {"tokens": 20000, "tool_calls": 10, "seconds": 120}


def usage() -> Payload:
    return {"tokens": 1500, "tool_calls": 2, "seconds": 3.5}


def evidence_ref() -> Payload:
    return {
        "evidence_id": EVIDENCE_ID,
        "source": "qradar",
        "query_hash": "sha256:5d41402abc4b2a76",
        "query_text": "SELECT sourceip, destinationip FROM events LAST 1 HOURS",
        "time_start": START,
        "time_end": END,
        "identifiers": {"starttime": "1790935930000", "logsourceid": "112", "qid": "5000123"},
        "excerpt": "Firewall Permit 203.0.113.7 -> 198.51.100.15:445",
        "retrieved_at": END,
    }


def claim() -> Payload:
    return {"text": "203.0.113.7 reached an internal host over SMB.", "evidence_ids": [EVIDENCE_ID]}


def data_gap() -> Payload:
    return {
        "source": "Microsoft Windows Security Event Log",
        "period_start": START,
        "period_end": END,
        "reason": "no_data",
    }


def recommendation() -> Payload:
    return {
        "action_type": "block_ioc_manual",
        "target": "203.0.113.7",
        "rationale": "External address with a USTA IOC match.",
        "evidence_ids": [EVIDENCE_ID],
    }


def urgent_event(rank: int = 1) -> Payload:
    return {
        "rank": rank,
        "time": START,
        "log_source": "FW-DMZ-01",
        "event_name": "Firewall Permit",
        "qid": 5000123,
        "source": "203.0.113.7",
        "destination": "198.51.100.15",
        "username": "user01",
        "reason": "First inbound SMB connection from this address.",
        "checklist": ["Is there a VPN login for this user at the same time?"],
        "aql": "SELECT * FROM events WHERE sourceip = '203.0.113.7' LAST 1 HOURS",
        "evidence_id": EVIDENCE_ID,
    }


def offense_snapshot() -> Payload:
    return {
        "offense_id": 12345,
        "description": "Excessive Firewall Accepts From Single Source",
        "offense_type": "Source IP",
        "offense_source": "203.0.113.7",
        "rule_ids": [100201],
        "rule_names": ["Excessive Firewall Accepts"],
        "categories": ["Firewall Permit"],
        "magnitude": 6,
        "start_time": START,
        "last_updated_time": END,
        "event_count": 42,
        "log_source_ids": [112],
        "source_ips": ["203.0.113.7"],
        "destination_ips": ["198.51.100.15"],
        "usernames": ["user01"],
    }


def catalog_rule() -> Payload:
    return {
        "rule_id": 100201,
        "mode": "analyze",
        "min_level": "medium",
        "context_note": "Fires often from the vulnerability scanners on Tuesday nights.",
        "attack_techniques": ["T1046", "T1595.002"],
    }


def catalog_log_source() -> Payload:
    return {
        "log_source_id": 112,
        "type_name": "Cisco ASA",
        "description": "DMZ firewall",
        "criticality": "high",
        "context_note": "Owned by the network team.",
    }


def catalog_context() -> Payload:
    return {"rules": [catalog_rule()], "log_sources": [catalog_log_source()]}


def critical_asset_hit() -> Payload:
    return {"value": "198.51.100.15", "label": "Core banking DB", "level": "critical"}


def ioc_hit() -> Payload:
    return {"value": "203.0.113.7", "type": "ip", "source": "usta", "confidence": "high"}


def entity_resolution() -> Payload:
    return {"ip": "198.51.100.15", "time": START, "host": "db01.example.com", "user": "user01"}


def enrichment_context() -> Payload:
    return {
        "catalog": catalog_context(),
        "critical_asset_hits": [critical_asset_hit()],
        "ioc_hits": [ioc_hit()],
        "entity_resolutions": [entity_resolution()],
        "group_id": "G-0001",
        "floor_level": "high",
    }


def agent_task() -> Payload:
    return {
        "task_id": "task-1",
        "parent_run_id": "run-1",
        "case_id": "case-12345",
        "hunt_id": None,
        "agent_id": "triage",
        "agent_version": "1",
        "objective": "Decide whether offense 12345 is a true positive.",
        "context_refs": [EVIDENCE_ID],
        "time_window": time_window(),
        "budget": budget(),
    }


def agent_result() -> Payload:
    return {
        "task_id": "task-1",
        "status": "completed",
        "claims": [claim()],
        "data_gaps": [data_gap()],
        "injection_suspected": False,
        "usage": usage(),
    }


def triage_result() -> Payload:
    return agent_result() | {
        "verdict": "suspicious",
        "confidence": "medium",
        "ai_level": "high",
        "rationale": "Inbound SMB from an address with an IOC match.",
        "needs_investigation": True,
        "investigation_focus": ["Lateral movement from 198.51.100.15"],
    }


def plan_step() -> Payload:
    return {
        "agent_id": "investigation",
        "skill_id": "lateral-movement-smb",
        "skill_version": "1.0.0",
        "objective": "Build a timeline around the SMB connection.",
        "time_window": time_window(),
        "budget": budget(),
    }


def case_plan() -> Payload:
    return agent_result() | {"steps": [plan_step()]}


def timeline_entry() -> Payload:
    return {"time": START, "description": "Inbound SMB accepted.", "evidence_ids": [EVIDENCE_ID]}


def investigation_hypothesis() -> Payload:
    return {"text": "The host was used for lateral movement.", "status": "open"}


def investigation_result() -> Payload:
    return agent_result() | {
        "verdict": "suspicious",
        "confidence": "medium",
        "ai_level": "high",
        "timeline": [timeline_entry()],
        "hypotheses": [investigation_hypothesis()],
        "urgent_event_candidates": [urgent_event()],
    }


def disagreement() -> Payload:
    return {
        "claim_text": "203.0.113.7 reached an internal host over SMB.",
        "reason": "The evidence shows a denied connection.",
    }


def verification_result() -> Payload:
    return agent_result() | {
        "agrees": False,
        "verdict": "suspicious",
        "confidence": "low",
        "disagreements": [disagreement()],
        "checked_evidence_ids": [EVIDENCE_ID],
    }


def case_report() -> Payload:
    return agent_result() | {
        "summary_tr": "Dış adresten iç sunucuya SMB bağlantısı kabul edilmiş.",
        "verdict": "suspicious",
        "confidence": "medium",
        "notify_level": "high",
        "urgent_events": [urgent_event(1), urgent_event(2)],
        "recommendations": [recommendation()],
    }


def note_content() -> Payload:
    return {
        "offense_id": 12345,
        "evaluation_no": 1,
        "run_marker": "7f3a9c",
        "verdict": "suspicious",
        "confidence": "medium",
        "notify_level": "high",
        "summary_tr": "Dış adresten iç sunucuya SMB bağlantısı kabul edilmiş.",
        "urgent_events": [urgent_event()],
        "recommended_actions": ["investigate_further", "block_ioc_manual"],
        "data_gaps": [data_gap()],
        "case_url": "https://soc.example.com/cases/case-12345",
        "group_id": None,
    }


def email_message() -> Payload:
    return {
        "kind": "case_alert",
        "recipients": ["soc-operators@example.com"],
        "subject": "[high] Şüpheli: Excessive Firewall Accepts",
        "template_id": "case_alert_v1",
        "fields": {"case_id": "case-12345", "summary_tr": "Özet"},
        "attachments": [],
        "idempotency_key": "case-12345:1",
    }


def tool_intent() -> Payload:
    return {
        "run_id": "case-12345-investigation-1",
        "case_id": "case-12345",
        "hunt_id": None,
        "agent_id": "investigation",
        "toolset_profile": "qradar-investigate-read",
        "tool_id": "qradar.ariel_search",
        "tool_schema_version": "1",
        "arguments": {"aql": "SELECT sourceip FROM events LAST 1 HOURS", "limit": 100},
        "reason": "Check other connections from the source.",
        "hypothesis_id": "h-1",
        "expected_evidence": "Connections from 203.0.113.7 to other hosts.",
        "time_window": time_window(),
        "cost_class": "low",
    }


def tool_coverage() -> Payload:
    return {"complete": False, "gaps": [data_gap()]}


def tool_result() -> Payload:
    return {
        "status": "ok",
        "deny_reason": None,
        "evidence_id": EVIDENCE_ID,
        "data": [{"sourceip": "203.0.113.7", "count": 3, "seen": True, "tags": None}],
        "truncated": False,
        "coverage": tool_coverage(),
    }


def model_release() -> Payload:
    return {
        "alias": "soc-reasoning",
        "target": "onprem-reasoning",
        "artifact": "example-reasoning-122b-instruct",
        "artifact_hash": CONTENT_HASH,
        "quantization": "fp8",
        "tokenizer": f"example-reasoning-tokenizer {CONTENT_HASH}",
        "engine_version": "vllm 0.11.0",
        "tool_parser": "hermes",
        "max_context": 131072,
        "inference_params": {"temperature": 0.2, "max_tokens": 4096, "stream": False, "seed": "x"},
    }


def skill_ref() -> Payload:
    return {"skill_id": "lateral-movement-smb", "version": "1.0.0", "content_hash": CONTENT_HASH}


def hunt_scope() -> Payload:
    return {"kind": "all", "values": []}


def hunt_request() -> Payload:
    return {
        "pack_id": "example-actor",
        "pack_version": "1.0.0",
        "hypothesis_ids": None,
        "window_start": "2026-04-01T00:00:00Z",
        "window_end": "2026-10-01T00:00:00Z",
        "scope": hunt_scope(),
        "trigger": "manual",
        "requested_by": "hunter01",
    }


def hypothesis_result(outcome: str = "refuted") -> Payload:
    return {
        "hypothesis_id": "h-1",
        "outcome": outcome,
        "rationale_tr": "Pencere boyunca destekleyen bulgu yok.",
        "claims": [claim()],
        "data_gaps": [],
    }


def coverage_entry() -> Payload:
    return {
        "month": "2026-04",
        "log_source_type": "Microsoft Windows Security Event Log",
        "status": "covered",
    }


def detection_proposal() -> Payload:
    return {"title": "SMB from external address", "sigma_yaml": "title: SMB from external\n"}


def hunt_versions() -> Payload:
    return {
        "pack": "1.0.0",
        "prompts": {"external_hunter": "v1"},
        "models": {"external_hunter": "soc-reasoning"},
    }


def hunt_report() -> Payload:
    return {
        "hunt_id": "hunt-1",
        "summary_tr": "Son 6 ayda destekleyen bulgu bulunamadı.",
        "outcome": "refuted",
        "hypotheses": [hypothesis_result("refuted")],
        "coverage": [coverage_entry()],
        "findings": [],
        "detection_proposals": [detection_proposal()],
        "versions": hunt_versions(),
    }


def backtest() -> Payload:
    return {"days": 30, "suppressed_offenses": 12, "suppressed_tp_offenses": 0}


def tuning_proposal() -> Payload:
    return {
        "cluster_id": "cluster-1",
        "rule_id": 100201,
        "change_type": "reference_set_exception",
        "description_tr": "Tarama sunucularını kural için istisna listesine ekle.",
        "rationale": "All 12 offenses in the cluster came from the scanners.",
        "backtest": backtest(),
        "risk_flag": False,
    }


def operator_feedback() -> Payload:
    return {
        "case_id": "case-12345",
        "verdict": "fp",
        "reason": "was_fp_not_tp",
        "comment": "Bilinen tarama.",
    }


# One valid payload per exported model.
VALID: dict[type[ContractModel], Callable[[], Payload]] = {
    AgentResult: agent_result,
    AgentTask: agent_task,
    Backtest: backtest,
    Budget: budget,
    CasePlan: case_plan,
    CaseReport: case_report,
    CatalogContext: catalog_context,
    CatalogLogSource: catalog_log_source,
    CatalogRule: catalog_rule,
    Claim: claim,
    CoverageEntry: coverage_entry,
    CriticalAssetHit: critical_asset_hit,
    DataGap: data_gap,
    DetectionProposal: detection_proposal,
    Disagreement: disagreement,
    EmailMessage: email_message,
    EnrichmentContext: enrichment_context,
    EntityResolution: entity_resolution,
    EvidenceRef: evidence_ref,
    HuntReport: hunt_report,
    HuntRequest: hunt_request,
    HuntScope: hunt_scope,
    HuntVersions: hunt_versions,
    HypothesisResult: hypothesis_result,
    InvestigationHypothesis: investigation_hypothesis,
    InvestigationResult: investigation_result,
    IocHit: ioc_hit,
    ModelRelease: model_release,
    NoteContent: note_content,
    OffenseSnapshot: offense_snapshot,
    OperatorFeedback: operator_feedback,
    PlanStep: plan_step,
    Recommendation: recommendation,
    SkillRef: skill_ref,
    TimelineEntry: timeline_entry,
    TimeWindow: time_window,
    ToolCoverage: tool_coverage,
    ToolIntent: tool_intent,
    ToolResult: tool_result,
    TriageResult: triage_result,
    TuningProposal: tuning_proposal,
    UrgentEvent: urgent_event,
    Usage: usage,
    VerificationResult: verification_result,
}
