import { useState } from "react";
import { Link } from "react-router-dom";
import { useQaItems, useResolveQa } from "../api/hooks";
import { ApiError } from "../api/client";
import type { QAItemSummary, QAReason, QAStatus } from "../api/types";
import {
  Dialog,
  EnumSelect,
  Empty,
  ErrorNotice,
  Loading,
  MoreButton,
  Notice,
  enumKeys,
} from "../components/common";
import { FeedbackForm } from "../components/FeedbackFields";
import styles from "../components/ui.module.css";
import { formatTime } from "../format";
import { tr } from "../i18n/tr";

function ResolveDialog({ item, onClose }: { item: QAItemSummary; onClose: () => void }) {
  const resolve = useResolveQa(item.id);
  const conflict = resolve.error instanceof ApiError && resolve.error.status === 409;
  return (
    <Dialog label={tr.qa.resolveTitle}>
      <h2>{tr.qa.resolveTitle}</h2>
      <p className={styles.muted}>{item.case_id}</p>
      {resolve.isSuccess ? (
        <>
          <Notice>{tr.qa.resolved}</Notice>
          <button type="button" onClick={onClose}>
            {tr.common.cancel}
          </button>
        </>
      ) : (
        <>
          <FeedbackForm
            submitLabel={tr.qa.submit}
            pending={resolve.isPending}
            error={resolve.error}
            onSubmit={(values) => resolve.mutate(values)}
          />
          <button type="button" className="secondary" onClick={onClose}>
            {conflict ? tr.common.close : tr.common.cancel}
          </button>
        </>
      )}
    </Dialog>
  );
}

export function QaQueue() {
  const [status, setStatus] = useState<QAStatus | "">("open");
  const [reason, setReason] = useState<QAReason | "">("");
  const [resolving, setResolving] = useState<QAItemSummary | null>(null);
  const list = useQaItems({ status, reason });
  const rows = list.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <>
      <h1>{tr.qa.title}</h1>
      <div className={styles.filters}>
        <EnumSelect
          label={tr.qa.filters.status}
          value={status}
          options={enumKeys(tr.qaStatus)}
          labels={tr.qaStatus}
          onChange={setStatus}
        />
        <EnumSelect
          label={tr.qa.filters.reason}
          value={reason}
          options={enumKeys(tr.qaReason)}
          labels={tr.qaReason}
          onChange={setReason}
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
              <th>{tr.qa.cols.case}</th>
              <th>{tr.qa.cols.reason}</th>
              <th>{tr.qa.cols.ai}</th>
              <th>{tr.qa.cols.summary}</th>
              <th>{tr.qa.cols.status}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => (
              <tr key={item.id}>
                <td>
                  <Link to={`/cases/${encodeURIComponent(item.case_id)}`}>{item.case_id}</Link>
                </td>
                <td>{tr.qaReason[item.reason]}</td>
                <td>{item.case_verdict ? tr.verdict[item.case_verdict] : tr.common.none}</td>
                <td>{item.case_summary_tr}</td>
                <td>
                  {tr.qaStatus[item.status]}
                  {item.resolved_by && (
                    <div className={styles.muted}>
                      {tr.qa.resolvedBy}: {item.resolved_by} · {formatTime(item.resolved_at)}
                    </div>
                  )}
                </td>
                <td>
                  {item.status === "open" && (
                    <button type="button" onClick={() => setResolving(item)}>
                      {tr.qa.resolve}
                    </button>
                  )}
                </td>
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
      {resolving && <ResolveDialog item={resolving} onClose={() => setResolving(null)} />}
    </>
  );
}
