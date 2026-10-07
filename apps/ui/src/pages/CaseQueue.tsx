import { useState } from "react";
import { Link } from "react-router-dom";
import { useCases } from "../api/hooks";
import type { CaseSource, CaseStatus, CaseVerdict, Level } from "../api/types";
import {
  Badge,
  EnumSelect,
  Empty,
  ErrorNotice,
  Field,
  LevelBadge,
  Loading,
  MoreButton,
  enumKeys,
} from "../components/common";
import styles from "../components/ui.module.css";
import { formatTime, istanbulDayEnd, istanbulDayStart, slaState } from "../format";
import { tr } from "../i18n/tr";

export type QueueFilters = {
  status: CaseStatus | "";
  notifyLevel: Level | "";
  verdict: CaseVerdict | "";
  source: CaseSource | "";
  ruleId: string;
  from: string;
  to: string;
};

export const NO_FILTERS: QueueFilters = {
  status: "",
  notifyLevel: "",
  verdict: "",
  source: "",
  ruleId: "",
  from: "",
  to: "",
};

/** The `GET /cases` query of a filter set; a day is an Istanbul day (`to` is exclusive). */
export function casesQuery(filters: QueueFilters) {
  const ruleId = Number.parseInt(filters.ruleId, 10);
  return {
    status: filters.status,
    notify_level: filters.notifyLevel,
    verdict: filters.verdict,
    source: filters.source,
    rule_id: Number.isNaN(ruleId) ? undefined : ruleId,
    from: filters.from ? istanbulDayStart(filters.from) : undefined,
    to: filters.to ? istanbulDayEnd(filters.to) : undefined,
  };
}

const SLA_TONE = {
  on_time: "good",
  late: "critical",
  undecided: "high",
  running: undefined,
} as const;

export function CaseQueue() {
  const [filters, setFilters] = useState<QueueFilters>(NO_FILTERS);
  const set = <K extends keyof QueueFilters>(key: K, value: QueueFilters[K]) =>
    setFilters((current) => ({ ...current, [key]: value }));
  const list = useCases(casesQuery(filters));
  const rows = list.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <>
      <h1>{tr.queue.title}</h1>
      <div className={styles.filters}>
        <EnumSelect
          label={tr.queue.filters.status}
          value={filters.status}
          options={enumKeys(tr.caseStatus)}
          labels={tr.caseStatus}
          onChange={(v) => set("status", v)}
        />
        <EnumSelect
          label={tr.queue.filters.notifyLevel}
          value={filters.notifyLevel}
          options={enumKeys(tr.level)}
          labels={tr.level}
          onChange={(v) => set("notifyLevel", v)}
        />
        <EnumSelect
          label={tr.queue.filters.verdict}
          value={filters.verdict}
          options={enumKeys(tr.verdict)}
          labels={tr.verdict}
          onChange={(v) => set("verdict", v)}
        />
        <EnumSelect
          label={tr.queue.filters.source}
          value={filters.source}
          options={enumKeys(tr.caseSource)}
          labels={tr.caseSource}
          onChange={(v) => set("source", v)}
        />
        <Field label={tr.queue.filters.rule}>
          <input
            inputMode="numeric"
            value={filters.ruleId}
            onChange={(e) => set("ruleId", e.target.value)}
          />
        </Field>
        <Field label={tr.queue.filters.from}>
          <input type="date" value={filters.from} onChange={(e) => set("from", e.target.value)} />
        </Field>
        <Field label={tr.queue.filters.to}>
          <input type="date" value={filters.to} onChange={(e) => set("to", e.target.value)} />
        </Field>
      </div>
      <ErrorNotice error={list.error} />
      {list.isPending ? (
        <Loading />
      ) : rows.length === 0 ? (
        <Empty />
      ) : (
        <table>
          <thead>
            <tr>
              <th>{tr.queue.cols.case}</th>
              <th>{tr.queue.cols.offense}</th>
              <th>{tr.queue.cols.rule}</th>
              <th>{tr.queue.cols.level}</th>
              <th>{tr.queue.cols.verdict}</th>
              <th>{tr.queue.cols.confidence}</th>
              <th>{tr.queue.cols.sla}</th>
              <th>{tr.queue.cols.status}</th>
              <th>{tr.queue.cols.created}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const sla = slaState(row);
              return (
                <tr key={row.case_id}>
                  <td>
                    <Link to={`/cases/${encodeURIComponent(row.case_id)}`}>{row.case_id}</Link>
                  </td>
                  <td>
                    {row.offense_id ?? tr.common.none}
                    {row.offense_description && (
                      <div className={styles.muted}>{row.offense_description}</div>
                    )}
                  </td>
                  <td>{row.rule_ids?.join(", ") || tr.common.none}</td>
                  <td>
                    <LevelBadge level={row.notify_level} />
                  </td>
                  <td>{row.verdict ? tr.verdict[row.verdict] : tr.common.none}</td>
                  <td>{row.confidence ? tr.confidence[row.confidence] : tr.common.none}</td>
                  <td>
                    <Badge tone={SLA_TONE[sla]}>{tr.sla[sla]}</Badge>
                  </td>
                  <td>{tr.caseStatus[row.status]}</td>
                  <td>{formatTime(row.created_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <MoreButton
        hasNext={list.hasNextPage}
        fetching={list.isFetchingNextPage}
        onMore={() => void list.fetchNextPage()}
      />
    </>
  );
}
