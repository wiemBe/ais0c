"""Valid, synthetic contract objects for the storage tests.

IPs are from the RFC 5737 ranges and domains are example.com. Each function returns a new
object.
"""

from datetime import UTC, datetime, timedelta

from ais0c_contracts import (
    AgentTask,
    CaseReport,
    Claim,
    EmailMessage,
    EvidenceRef,
    HuntReport,
    HuntRequest,
    ModelRelease,
    NoteContent,
    Recommendation,
    SkillRef,
    ToolIntent,
    TriageResult,
    TuningProposal,
    UrgentEvent,
)

T0 = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
EVIDENCE_ID = "ev_01JB3K7Q9X"
CASE_ID = "case-12345"
OFFENSE_ID = 12345


def claim() -> Claim:
    return Claim(text="203.0.113.7 reached an internal host over SMB.", evidence_ids=[EVIDENCE_ID])


def evidence_ref(evidence_id: str = EVIDENCE_ID) -> EvidenceRef:
    return EvidenceRef.model_validate(
        {
            "evidence_id": evidence_id,
            "source": "qradar",
            "query_hash": "sha256:5d41402abc4b2a76",
            "query_text": "SELECT sourceip, destinationip FROM events LAST 1 HOURS",
            "time_start": T0,
            "time_end": T1,
            "identifiers": {"starttime": "1790935930000", "logsourceid": "112", "qid": "5000123"},
            "excerpt": "Firewall Permit 203.0.113.7 -> 198.51.100.15:445",
            "retrieved_at": T1,
        }
    )


def urgent_event(rank: int = 1) -> UrgentEvent:
    return UrgentEvent.model_validate(
        {
            "rank": rank,
            "time": T0,
            "log_source": "FW-DMZ-01",
            "event_name": "Firewall Permit",
            "qid": 5000123,
            "source": "203.0.113.7",
            "destination": "198.51.100.15",
            "username": "user01",
            "reason": "First inbound SMB connection from this address.",
            "checklist": ["Is there a VPN login for this user at the same time?"],
            "evidence_id": EVIDENCE_ID,
        }
    )


def recommendation() -> Recommendation:
    return Recommendation.model_validate(
        {
            "action_type": "block_ioc_manual",
            "target": "203.0.113.7",
            "rationale": "External address with an IOC match.",
            "evidence_ids": [EVIDENCE_ID],
        }
    )


def _agent_result() -> dict[str, object]:
    return {
        "task_id": "task-1",
        "status": "completed",
        "claims": [claim()],
        "data_gaps": [],
        "injection_suspected": False,
        "usage": {"tokens": 1500, "tool_calls": 2, "seconds": 3.5},
    }


def agent_task(case_id: str | None = CASE_ID, hunt_id: str | None = None) -> AgentTask:
    return AgentTask.model_validate(
        {
            "task_id": "task-1",
            "parent_run_id": "run-0",
            "case_id": case_id,
            "hunt_id": hunt_id,
            "agent_id": "triage",
            "agent_version": "1",
            "objective": "Decide whether offense 12345 is a true positive.",
            "context_refs": [EVIDENCE_ID],
            "time_window": {"start": T0, "end": T1},
            "budget": {"tokens": 20000, "tool_calls": 10, "seconds": 120},
        }
    )


def triage_result(status: str = "completed") -> TriageResult:
    return TriageResult.model_validate(
        _agent_result()
        | {
            "status": status,
            "verdict": "suspicious",
            "confidence": "medium",
            "ai_level": "high",
            "rationale": "Inbound SMB from an address with an IOC match.",
            "needs_investigation": True,
            "investigation_focus": ["Lateral movement from 198.51.100.15"],
        }
    )


def case_report() -> CaseReport:
    return CaseReport.model_validate(
        _agent_result()
        | {
            "summary_tr": "Dış adresten iç sunucuya SMB bağlantısı kabul edilmiş.",
            "verdict": "suspicious",
            "confidence": "medium",
            "notify_level": "high",
            "urgent_events": [urgent_event(1), urgent_event(2)],
            "recommendations": [recommendation()],
        }
    )


def tool_intent(case_id: str | None = CASE_ID) -> ToolIntent:
    return ToolIntent.model_validate(
        {
            # The run the tests record tool calls under.
            "run_id": "run-1",
            "case_id": case_id,
            "agent_id": "triage",
            "toolset_profile": "qradar-triage-read",
            "tool_id": "qradar.ariel_search",
            "tool_schema_version": "1",
            "arguments": {"aql": "SELECT sourceip FROM events LAST 1 HOURS", "limit": 100},
            "reason": "Check other connections from the source.",
            "expected_evidence": "Connections from 203.0.113.7 to other hosts.",
            "time_window": {"start": T0, "end": T1},
            "cost_class": "low",
        }
    )


def model_release(
    alias: str = "soc-fast", engine_version: str | None = "0.11.2", target: str = "lab-model"
) -> ModelRelease:
    return ModelRelease.model_validate(
        {
            "alias": alias,
            "target": target,
            "artifact": "example-org/Model-A",
            "artifact_hash": "3f1c2a9e8b7d6c5b4a39281706f5e4d3c2b1a090",
            "quantization": "fp8",
            "tokenizer": f"example-org/Model-A sha256:{'ab' * 32}",
            "engine_version": engine_version,
            "tool_parser": "example_parser",
            "max_context": 131072,
            "inference_params": {"forced_tool_choice": True, "temperature": 0.2, "seed": 7},
        }
    )


def skill_ref() -> SkillRef:
    return SkillRef(skill_id="dcsync", version="1.0.0", content_hash=f"sha256:{'0f' * 32}")


def note_content(run_marker: str = "7f3a9c", evaluation_no: int = 1) -> NoteContent:
    return NoteContent.model_validate(
        {
            "offense_id": OFFENSE_ID,
            "evaluation_no": evaluation_no,
            "run_marker": run_marker,
            "verdict": "suspicious",
            "confidence": "medium",
            "notify_level": "high",
            "summary_tr": "Dış adresten iç sunucuya SMB bağlantısı kabul edilmiş.",
            "urgent_events": [urgent_event()],
            "recommended_actions": ["investigate_further"],
            "data_gaps": [],
            "case_url": "https://soc.example.com/cases/case-12345",
        }
    )


def email_message(idempotency_key: str = "case-12345:1", kind: str = "case_alert") -> EmailMessage:
    return EmailMessage.model_validate(
        {
            "kind": kind,
            "recipients": ["soc-operators@example.com"],
            "subject": "[high] Şüpheli: Excessive Firewall Accepts",
            "template_id": "case_alert_v1",
            "fields": {"case_id": CASE_ID},
            "attachments": [],
            "idempotency_key": idempotency_key,
        }
    )


def hunt_request() -> HuntRequest:
    return HuntRequest.model_validate(
        {
            "pack_id": "example-actor",
            "pack_version": "1.0.0",
            "window_start": datetime(2026, 4, 1, tzinfo=UTC),
            "window_end": datetime(2026, 10, 1, tzinfo=UTC),
            "scope": {"kind": "all", "values": []},
            "trigger": "manual",
            "requested_by": "hunter01",
        }
    )


def hunt_report() -> HuntReport:
    return HuntReport.model_validate(
        {
            "hunt_id": "hunt-1",
            "summary_tr": "Son 6 ayda destekleyen bulgu bulunamadı.",
            "outcome": "refuted",
            "hypotheses": [
                {
                    "hypothesis_id": "h-1",
                    "outcome": "refuted",
                    "rationale_tr": "Pencere boyunca destekleyen bulgu yok.",
                    "claims": [claim()],
                    "data_gaps": [],
                }
            ],
            "coverage": [],
            "findings": [],
            "detection_proposals": [],
            "versions": {"pack": "1.0.0", "prompts": {}, "models": {}},
        }
    )


def tuning_proposal() -> TuningProposal:
    return TuningProposal.model_validate(
        {
            "cluster_id": "cluster-1",
            "rule_id": 100201,
            "change_type": "reference_set_exception",
            "description_tr": "Tarama sunucularını istisna listesine ekle.",
            "rationale": "All cases in the cluster come from the scanners.",
            "backtest": {"days": 30, "suppressed_offenses": 12, "suppressed_tp_offenses": 0},
            "risk_flag": False,
        }
    )
