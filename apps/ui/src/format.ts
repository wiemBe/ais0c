import { tr, type SlaState } from "./i18n/tr";
import type { CaseSummary } from "./api/types";

// The API speaks UTC; the user reads Europe/Istanbul (api.md "Genel kurallar").
export const DISPLAY_TIME_ZONE = "Europe/Istanbul";

const dateTime = new Intl.DateTimeFormat("tr-TR", {
  timeZone: DISPLAY_TIME_ZONE,
  dateStyle: "short",
  timeStyle: "medium",
});

export function formatTime(value: string | null | undefined): string {
  if (!value) return tr.common.none;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? tr.common.none : dateTime.format(date);
}

export function formatRange(start: string, end: string): string {
  return `${formatTime(start)} – ${formatTime(end)}`;
}

/** Turkey has had a fixed UTC+3 since 2016, so a day boundary is a constant offset away. */
const OFFSET = "+03:00";

/** `2026-10-07` → the instant that day starts in Istanbul (ISO 8601, with its offset). */
export function istanbulDayStart(date: string): string {
  return `${date}T00:00:00${OFFSET}`;
}

/** The instant the day after `date` starts: `to` is exclusive in the API. */
export function istanbulDayEnd(date: string): string {
  const next = new Date(`${date}T00:00:00Z`);
  next.setUTCDate(next.getUTCDate() + 1);
  return `${next.toISOString().slice(0, 10)}T00:00:00${OFFSET}`;
}

/** The SLA state of a queue row (T-029 criterion 4): on time, late, undecided or running. */
export function slaState(row: CaseSummary): SlaState {
  if (row.status === "no_ai_decision") return "undecided";
  if (row.status === "running") return row.sla_overdue ? "late" : "running";
  if (row.decided_at && new Date(row.decided_at) > new Date(row.sla_due_at)) return "late";
  return "on_time";
}
