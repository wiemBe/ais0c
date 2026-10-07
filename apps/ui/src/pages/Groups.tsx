import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useGroup, useGroups } from "../api/hooks";
import type { GroupStatus } from "../api/types";
import {
  Badge,
  EnumSelect,
  Empty,
  ErrorNotice,
  LevelBadge,
  Loading,
  MoreButton,
  enumKeys,
} from "../components/common";
import styles from "../components/ui.module.css";
import { formatRange, formatTime } from "../format";
import { tr } from "../i18n/tr";

export function GroupList() {
  const [status, setStatus] = useState<GroupStatus | "">("");
  const list = useGroups({ status });
  const rows = list.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <>
      <h1>{tr.groups.title}</h1>
      <div className={styles.filters}>
        <EnumSelect
          label={tr.groups.filterStatus}
          value={status}
          options={enumKeys(tr.groupStatus)}
          labels={tr.groupStatus}
          onChange={setStatus}
        />
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
              <th>{tr.groups.cols.group}</th>
              <th>{tr.groups.cols.status}</th>
              <th>{tr.groups.cols.offenses}</th>
              <th>{tr.groups.cols.window}</th>
              <th>{tr.groups.cols.case}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.group_id}>
                <td>
                  <Link to={`/groups/${encodeURIComponent(row.group_id)}`}>{row.group_id}</Link>
                </td>
                <td>
                  <Badge tone={row.status === "storm" ? "critical" : undefined}>
                    {tr.groupStatus[row.status]}
                  </Badge>
                </td>
                <td>{row.offense_count}</td>
                <td>{formatRange(row.window_start, row.window_end)}</td>
                <td>{row.case_id ?? tr.common.none}</td>
              </tr>
            ))}
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

export function GroupDetail() {
  const { groupId = "" } = useParams();
  const query = useGroup(groupId);
  if (query.isPending) return <Loading />;
  if (query.error) return <ErrorNotice error={query.error} />;
  const detail = query.data;
  const digest = detail.summary;
  return (
    <>
      <p>
        <Link to="/groups">{tr.groups.title}</Link>
      </p>
      <h1>
        {tr.groups.detail} {detail.group.group_id}
      </h1>
      <div className={styles.panel}>
        <Badge tone={detail.group.status === "storm" ? "critical" : undefined}>
          {tr.groupStatus[detail.group.status]}
        </Badge>{" "}
        {formatRange(detail.group.window_start, detail.group.window_end)}
      </div>

      <h2>{tr.groups.decision}</h2>
      <div className={styles.panel}>
        {detail.case_id ? (
          <>
            <div className={styles.row}>
              <LevelBadge level={detail.notify_level} />
              <Badge>{detail.verdict ? tr.verdict[detail.verdict] : tr.common.none}</Badge>
              {detail.case_status && <Badge>{tr.caseStatus[detail.case_status]}</Badge>}
            </div>
            {detail.report && <p style={{ whiteSpace: "pre-wrap" }}>{detail.report.summary_tr}</p>}
            <Link to={`/cases/${encodeURIComponent(detail.case_id)}`}>{tr.groups.openCase}</Link>
          </>
        ) : (
          <p className={styles.muted}>{tr.groups.noCase}</p>
        )}
      </div>

      <h2>{tr.groups.summary}</h2>
      <div className={styles.panel}>
        <p>
          {tr.groups.offenseCount}: {digest.offense_count}
        </p>
        <p>
          {tr.groups.range}:{" "}
          {digest.first_seen_at && digest.last_seen_at
            ? formatRange(digest.first_seen_at, digest.last_seen_at)
            : tr.common.none}
        </p>
        <p>
          {tr.groups.rules}: {digest.rule_ids?.join(", ") || tr.common.none}
        </p>
        {digest.values?.map((kind) => (
          <div key={kind.kind}>
            <strong>{tr.groupValueKind[kind.kind]}</strong>{" "}
            <span className={styles.muted}>
              {kind.distinct} {tr.groups.distinct}
            </span>
            <div>
              {tr.groups.top}:{" "}
              {kind.top?.map((item) => `${item.value} (${item.offenses})`).join(", ")}
            </div>
          </div>
        ))}
      </div>

      <h2>{tr.groups.offenses}</h2>
      {detail.offenses?.length ? (
        <table>
          <thead>
            <tr>
              <th>{tr.groups.cols2.offense}</th>
              <th>{tr.groups.cols2.description}</th>
              <th>{tr.groups.cols2.status}</th>
              <th>{tr.groups.cols2.reason}</th>
            </tr>
          </thead>
          <tbody>
            {detail.offenses.map((offense) => (
              <tr key={offense.offense_id}>
                <td>
                  {offense.qradar_offense_url?.startsWith("https://") ? (
                    <a href={offense.qradar_offense_url} target="_blank" rel="noopener noreferrer">
                      {offense.offense_id}
                    </a>
                  ) : (
                    offense.offense_id
                  )}
                  <div className={styles.muted}>{formatTime(offense.first_seen_at)}</div>
                </td>
                <td>{offense.description}</td>
                <td>{tr.offenseStatus[offense.status]}</td>
                <td>
                  {offense.full_analysis_reason
                    ? tr.fullAnalysisReason[offense.full_analysis_reason]
                    : tr.groups.noReason}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty />
      )}
    </>
  );
}
