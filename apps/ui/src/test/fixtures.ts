// Synthetic data only: RFC 5737 addresses and example.com names (AGENTS.md hard rule 6).
import type {
  CaseDetail,
  CaseSummary,
  GroupDetail,
  Me,
  QAItemSummary,
  UrgentEvent,
} from "../api/types";

export const OPERATOR: Me = { subject: "op-1", display_name: "Test Operatör", roles: ["operator"] };
export const ADMIN: Me = {
  subject: "ad-1",
  display_name: "Test Admin",
  roles: ["admin", "hunter", "operator"],
};

export function caseSummary(overrides: Partial<CaseSummary> = {}): CaseSummary {
  return {
    case_id: "case-100",
    source: "offense",
    status: "decided",
    verdict: "suspicious",
    confidence: "medium",
    ai_level: "high",
    floor_level: "medium",
    notify_level: "high",
    evaluation_no: 1,
    sla_due_at: "2026-10-02T10:30:00Z",
    decided_at: "2026-10-02T10:20:00Z",
    created_at: "2026-10-02T10:00:00Z",
    offense_id: 100,
    offense_description: "Excessive Firewall Accepts",
    rule_ids: [100201],
    sla_overdue: false,
    qradar_offense_url: "https://192.0.2.10/console/ui/offenses/100",
    ...overrides,
  };
}

export function urgentEvent(overrides: Partial<UrgentEvent> = {}): UrgentEvent {
  return {
    rank: 1,
    event_name: "SMB connection accepted",
    time: "2026-10-02T09:58:00Z",
    log_source: "FW-DMZ-01",
    username: "svc-backup",
    source: "203.0.113.7",
    destination: "198.51.100.20",
    reason: "Dış adresten iç sunucuya SMB bağlantısı.",
    evidence_id: "ev_1",
    checklist: ["Kaynak IP'yi doğrula", "Hesabın son girişlerine bak"],
    aql: "SELECT * FROM events WHERE sourceip = '203.0.113.7'",
    ...overrides,
  };
}

export function caseDetail(overrides: Partial<CaseDetail> = {}): CaseDetail {
  const event = urgentEvent();
  return {
    case: caseSummary(),
    evaluation_no: 1,
    report: {
      task_id: "t-1",
      status: "completed",
      verdict: "suspicious",
      confidence: "medium",
      notify_level: "high",
      summary_tr: "Dış adresten iç sunucuya SMB bağlantısı kabul edilmiş.",
      claims: [],
      urgent_events: [event],
      recommendations: [],
      data_gaps: [],
      injection_suspected: false,
      usage: { seconds: 12, tokens: 1000, tool_calls: 3 },
    },
    urgent_events: [event],
    recommendations: [
      {
        action_type: "investigate_further",
        target: "198.51.100.20",
        rationale: "Hedef sunucuda oturumları incele.",
        evidence_ids: ["ev_1"],
      },
    ],
    verification: {
      task_id: "t-2",
      status: "completed",
      verdict: "suspicious",
      confidence: "medium",
      agrees: true,
      claims: [],
      checked_evidence_ids: ["ev_1"],
      data_gaps: [],
      disagreements: [],
      injection_suspected: false,
      usage: { seconds: 5, tokens: 400, tool_calls: 1 },
    },
    data_gaps: [
      {
        source: "FW-DMZ-01",
        reason: "not_parsed",
        period_start: "2026-10-02T09:00:00Z",
        period_end: "2026-10-02T10:00:00Z",
      },
    ],
    evidence: [
      {
        evidence_id: "ev_1",
        source: "qradar",
        tool_id: "qradar.ariel_search",
        cited: true,
        query_hash: "abc123",
        query_text: "SELECT * FROM events",
        identifiers: {},
        time_start: "2026-10-02T09:00:00Z",
        time_end: "2026-10-02T10:00:00Z",
        retrieved_at: "2026-10-02T10:05:00Z",
      },
    ],
    notes: [
      {
        offense_id: 100,
        evaluation_no: 1,
        run_marker: "7f3a9c",
        status: "written",
        error: null,
        written_at: "2026-10-02T10:21:00Z",
      },
    ],
    notifications: [
      {
        kind: "case_alert",
        level: "high",
        recipients: ["soc@example.com"],
        sent_at: "2026-10-02T10:22:00Z",
        status: "sent",
        subject: "Yüksek seviye vaka",
        error: null,
      },
    ],
    ...overrides,
  };
}

export function qaItem(overrides: Partial<QAItemSummary> = {}): QAItemSummary {
  return {
    id: "6f1c1d0e-0000-4000-8000-000000000001",
    case_id: "case-100",
    evaluation_no: 1,
    reason: "low_confidence",
    status: "open",
    resolved_at: null,
    resolved_by: null,
    case_status: "decided",
    case_verdict: "suspicious",
    case_notify_level: "high",
    case_summary_tr: "Özet metni",
    ...overrides,
  };
}

export function groupDetail(overrides: Partial<GroupDetail> = {}): GroupDetail {
  return {
    group: {
      group_id: "group-g1",
      rule_set_hash: "hash",
      window_start: "2026-10-02T10:00:00Z",
      window_end: "2026-10-02T10:10:00Z",
      offense_count: 2,
      status: "storm",
      case_id: "group-g1",
    },
    offenses: [
      {
        offense_id: 5001,
        description: "Excessive Firewall Accepts",
        status: "done",
        first_seen_at: "2026-10-02T10:00:00Z",
        full_analysis_reason: "novelty",
        qradar_offense_url: "https://192.0.2.10/console/ui/offenses/5001",
      },
      {
        offense_id: 5002,
        description: "Excessive Firewall Accepts",
        status: "grouped",
        first_seen_at: "2026-10-02T10:04:00Z",
      },
    ],
    summary: {
      offense_count: 2,
      first_seen_at: "2026-10-02T10:00:00Z",
      last_seen_at: "2026-10-02T10:04:00Z",
      rule_ids: [100201],
      values: [
        {
          kind: "source_ip",
          distinct: 2,
          top: [{ value: "198.51.100.7", offenses: 2 }],
        },
      ],
    },
    case_id: "group-g1",
    case_status: "decided",
    verdict: "suspicious",
    notify_level: "high",
    ...overrides,
  };
}

export const problem = (title: string, status: number, extra: object = {}) => ({
  type: "about:blank",
  title,
  status,
  detail: "DETAIL-THE-USER-MUST-NOT-SEE",
  ...extra,
});
