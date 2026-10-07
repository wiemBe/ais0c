// Aliases over the types generated from services/api/openapi.json (`pnpm gen:api`); nothing here
// is written by hand beyond the names.
import type { components } from "./schema";

export type Schemas = components["schemas"];

export type ApiProblem = Schemas["Problem"];
export type CaseSummary = Schemas["CaseSummary"];
export type CaseDetail = Schemas["CaseDetail"];
export type CaseStep = Schemas["CaseStep"];
export type FeedbackAnswer = Schemas["FeedbackAnswer"];
export type OperatorFeedback = Schemas["OperatorFeedback"];
export type QAItemSummary = Schemas["QAItemSummary"];
export type QAResolveRequest = Schemas["QAResolveRequest"];
export type GroupSummary = Schemas["GroupSummary"];
export type GroupDetail = Schemas["GroupDetail"];
export type CatalogRule = Schemas["CatalogRule"];
export type CatalogRuleUpdate = Schemas["CatalogRuleUpdate"];
export type CatalogLogSource = Schemas["CatalogLogSource"];
export type CatalogLogSourceUpdate = Schemas["CatalogLogSourceUpdate"];
export type CriticalAsset = Schemas["CriticalAsset"];
export type CriticalAssetAdd = Schemas["CriticalAssetAdd"];
export type RecipientsView = Schemas["RecipientsView"];
export type NotificationRoute = Schemas["NotificationRoute"];
export type PlatformFlagState = Schemas["PlatformFlagState"];
export type SLAMetrics = Schemas["SLAMetrics"];
export type UrgentEvent = Schemas["UrgentEvent"];
export type Me = Schemas["Me"];

export type Level = Schemas["Level"];
export type CaseVerdict = Schemas["CaseVerdict"];
export type Confidence = Schemas["Confidence"];
export type CaseStatus = Schemas["CaseStatus"];
export type CaseSource = Schemas["CaseSource"];
export type GroupStatus = Schemas["GroupStatus"];
export type QAReason = Schemas["QAReason"];
export type QAStatus = Schemas["QAStatus"];
export type FeedbackReason = Schemas["FeedbackReason"];
export type ActionType = Schemas["ActionType"];
export type DataGapReason = Schemas["DataGapReason"];
export type EvidenceSource = Schemas["EvidenceSource"];
export type NoteStatus = Schemas["NoteStatus"];
export type NotificationStatus = Schemas["NotificationStatus"];
export type EmailKind = Schemas["EmailKind"];
export type RunStatus = Schemas["RunStatus"];
export type ToolStatus = Schemas["ToolStatus"];
export type PolicyDecision = Schemas["PolicyDecision"];
export type CriticalAssetKind = Schemas["CriticalAssetKind"];
export type CatalogMode = Schemas["CatalogMode"];
export type GroupValueKind = Schemas["GroupValueKind"];
export type OffenseStatus = Schemas["OffenseStatus"];
export type FullAnalysisReason = Schemas["FullAnalysisReason"];
export type Role = Schemas["Role"];

export type Page<T> = { items: T[]; next_cursor?: string | null };
