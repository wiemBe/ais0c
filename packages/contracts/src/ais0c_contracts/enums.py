"""Enums from the "Enum'lar" table in docs/impl/contracts.md."""

from enum import StrEnum


class Level(StrEnum):
    """Severity level. Values compare as strings, not by severity."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CaseVerdict(StrEnum):
    TP = "tp"
    FP = "fp"
    SUSPICIOUS = "suspicious"


class HuntOutcome(StrEnum):
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"


class RunStatus(StrEnum):
    COMPLETED = "completed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED = "failed"


class CaseSource(StrEnum):
    OFFENSE = "offense"
    HUNT = "hunt"
    GROUP = "group"


class CatalogMode(StrEnum):
    ANALYZE = "analyze"
    SKIP = "skip"


class EvidenceSource(StrEnum):
    QRADAR = "qradar"
    FALCON = "falcon"


class DataGapReason(StrEnum):
    NO_DATA = "no_data"
    NOT_PARSED = "not_parsed"
    NOT_VISIBLE = "not_visible"
    QUERY_FAILED = "query_failed"
    BUDGET_EXHAUSTED = "budget_exhausted"


class ActionType(StrEnum):
    INVESTIGATE_FURTHER = "investigate_further"
    CONTAIN_HOST_MANUAL = "contain_host_manual"
    RESET_CREDENTIALS_MANUAL = "reset_credentials_manual"
    BLOCK_IOC_MANUAL = "block_ioc_manual"
    TUNE_RULE = "tune_rule"
    CLOSE_AS_FP = "close_as_fp"
    NOTIFY_USER = "notify_user"


class QAReason(StrEnum):
    RANDOM_SAMPLE = "random_sample"
    VERIFIER_CONFLICT = "verifier_conflict"
    INJECTION_SUSPECTED = "injection_suspected"
    LOW_CONFIDENCE = "low_confidence"
    FP_WITH_DATA_GAP = "fp_with_data_gap"


class FeedbackReason(StrEnum):
    CORRECT = "correct"
    WAS_TP_NOT_FP = "was_tp_not_fp"
    WAS_FP_NOT_TP = "was_fp_not_tp"
    MISSING_CONTEXT = "missing_context"
    WRONG_URGENT_EVENTS = "wrong_urgent_events"
    OTHER = "other"


class CostClass(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolStatus(StrEnum):
    OK = "ok"
    DENIED = "denied"
    ERROR = "error"


class EmailKind(StrEnum):
    CASE_ALERT = "case_alert"
    GROUP_ALERT = "group_alert"
    HUNT_REPORT = "hunt_report"
    # A platform health alarm (T-23, T-68); not an AI output, so the kill switch does not hold it.
    HEALTH_ALARM = "health_alarm"


class TuningChange(StrEnum):
    REFERENCE_SET_EXCEPTION = "reference_set_exception"
    BUILDING_BLOCK = "building_block"
    THRESHOLD = "threshold"
    TIME_WINDOW = "time_window"
    OTHER = "other"
