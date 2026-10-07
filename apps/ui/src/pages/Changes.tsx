import { useState } from "react";
import {
  useApproveChange,
  useChanges,
  usePendingChanges,
  useRejectChange,
  useWithdrawChange,
} from "../api/hooks";
import { useMe } from "../api/session";
import type { ChangeItem, ChangeStatus } from "../api/types";
import {
  Badge,
  Dialog,
  Empty,
  EnumSelect,
  ErrorNotice,
  Field,
  Loading,
  MoreButton,
  Notice,
  enumKeys,
} from "../components/common";
import styles from "../components/ui.module.css";
import { formatTime } from "../format";
import { tr } from "../i18n/tr";

/** The pending requests by `${object_type}:${object_id}`, for the rows that carry a mark. Empty
 * for anyone but an admin: the endpoint is an admin's. */
export function usePendingMap(enabled: boolean): Map<string, ChangeItem> {
  const pending = usePendingChanges(enabled);
  return new Map((pending.data?.items ?? []).map((item) => [pendingKey(item), item]));
}

export const pendingKey = (item: Pick<ChangeItem, "object_type" | "object_id">) =>
  `${item.object_type}:${item.object_id}`;

/** A row's "waiting for approval" mark. */
export function PendingMark() {
  return <Badge tone="high">{tr.common.pendingMark}</Badge>;
}

const enumLabels = (field: string, value: unknown): string | null => {
  if (typeof value !== "string") return null;
  const tables: Record<string, Record<string, string>> = {
    mode: tr.catalogMode,
    min_level: tr.level,
    criticality: tr.level,
    level: tr.level,
    kind: tr.criticalAssetKind,
  };
  return tables[field]?.[value] ?? null;
};

/** One value of a request as text: booleans as yes/no, lists joined, enums by their label. */
export function formatChangeValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return tr.common.none;
  if (typeof value === "boolean") return value ? tr.common.yes : tr.common.no;
  if (Array.isArray(value)) return value.length === 0 ? tr.common.none : value.join(", ");
  return enumLabels(field, value) ?? String(value);
}

type Side = Record<string, unknown>;

const asSide = (value: unknown): Side =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Side) : {};

/** The fields a request touches, with their values before and after, side by side. */
function ChangeValues({ change }: { change: ChangeItem }) {
  const before = asSide(change.change.before);
  const after = asSide(change.change.after);
  const fields = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  const reason = change.change.reason;
  return (
    <table>
      <thead>
        <tr>
          <th>{tr.admin.changes.field}</th>
          <th>{tr.admin.changes.before}</th>
          <th>{tr.admin.changes.after}</th>
        </tr>
      </thead>
      <tbody>
        {fields.map((field) => (
          <tr key={field}>
            <td>{tr.admin.changes.fields[field] ?? field}</td>
            <td>{formatChangeValue(field, before[field])}</td>
            <td>{formatChangeValue(field, after[field])}</td>
          </tr>
        ))}
        {typeof reason === "string" && (
          <tr>
            <td>{tr.admin.changes.fields.reason}</td>
            <td />
            <td>{reason}</td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

const actionOf = (change: ChangeItem): string => {
  const action = change.change.action;
  return typeof action === "string" ? (tr.admin.changes.actions[action] ?? action) : tr.common.none;
};

const statusText = (change: ChangeItem): string =>
  change.reason
    ? `${tr.changeStatus[change.status]}: ${tr.changeRejectReason[change.reason]}`
    : tr.changeStatus[change.status];

function ChangeDetail({ change, onClose }: { change: ChangeItem; onClose: () => void }) {
  const me = useMe().data;
  const approve = useApproveChange(change.id);
  const reject = useRejectChange(change.id);
  const withdraw = useWithdrawChange(change.id);
  const [comment, setComment] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const pending = change.status === "pending";
  // The requester never decides their own request (D-36): no approve or reject button for them.
  const mine = me?.subject === change.requested_by;
  const busy = approve.isPending || reject.isPending || withdraw.isPending;
  const finish = (message: string) => ({ onSuccess: () => setDone(message) });

  return (
    <Dialog label={tr.admin.changes.detail}>
      <h2>{tr.admin.changes.detail}</h2>
      <p>
        {tr.changeObjectType[change.object_type]} · {change.object_id} · {actionOf(change)}
      </p>
      <p className={styles.muted}>
        {tr.admin.changes.cols.requestedBy}: {change.requested_by} ·{" "}
        {formatTime(change.requested_at)} · {statusText(change)}
      </p>
      <ChangeValues change={change} />
      {change.decided_by && (
        <p className={styles.muted}>
          {tr.admin.changes.decidedBy}: {change.decided_by} · {formatTime(change.decided_at)}
        </p>
      )}
      {change.comment && (
        <p>
          {tr.admin.changes.comment}: {change.comment}
        </p>
      )}
      {done && <Notice>{done}</Notice>}
      <ErrorNotice error={approve.error ?? reject.error ?? withdraw.error} />
      {pending && !done && (
        <>
          {mine ? (
            <p className={styles.muted}>{tr.admin.changes.ownRequest}</p>
          ) : (
            <>
              <Field label={tr.admin.changes.commentLabel}>
                <textarea
                  rows={2}
                  maxLength={500}
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                />
              </Field>
              <div className={styles.row}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => approve.mutate(undefined, finish(tr.admin.changes.approved))}
                >
                  {tr.admin.changes.approve}
                </button>
                <button
                  type="button"
                  className="danger"
                  disabled={busy}
                  onClick={() => reject.mutate(comment, finish(tr.admin.changes.rejected))}
                >
                  {tr.admin.changes.reject}
                </button>
              </div>
            </>
          )}
          {mine && (
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={() => withdraw.mutate(undefined, finish(tr.admin.changes.withdrawn))}
            >
              {tr.admin.changes.withdraw}
            </button>
          )}
        </>
      )}
      <p>
        <button type="button" className="secondary" onClick={onClose}>
          {tr.admin.changes.close}
        </button>
      </p>
    </Dialog>
  );
}

/** Admin: the requests waiting for a second admin, with a detail to approve, reject or take back. */
export function PendingChanges() {
  const [status, setStatus] = useState<ChangeStatus | "">("pending");
  const [open, setOpen] = useState<ChangeItem | null>(null);
  const list = useChanges({ status });
  const rows = list.data?.pages.flatMap((page) => page.items) ?? [];
  // The detail shows the freshest copy of the row; once a decision moves it out of the filtered
  // list, the copy the dialog opened with stays.
  const selected = open === null ? null : (rows.find((row) => row.id === open.id) ?? open);
  return (
    <>
      <p className={styles.muted}>{tr.admin.changes.help}</p>
      <div className={styles.filters}>
        <EnumSelect
          label={tr.admin.changes.status}
          value={status}
          options={enumKeys(tr.changeStatus)}
          labels={tr.changeStatus}
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
              <th>{tr.admin.changes.cols.type}</th>
              <th>{tr.admin.changes.cols.object}</th>
              <th>{tr.admin.changes.cols.action}</th>
              <th>{tr.admin.changes.cols.requestedBy}</th>
              <th>{tr.admin.changes.cols.requestedAt}</th>
              <th>{tr.admin.changes.cols.status}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{tr.changeObjectType[row.object_type]}</td>
                <td>{row.object_id}</td>
                <td>{actionOf(row)}</td>
                <td>{row.requested_by}</td>
                <td>{formatTime(row.requested_at)}</td>
                <td>{statusText(row)}</td>
                <td>
                  <button type="button" className="secondary" onClick={() => setOpen(row)}>
                    {tr.admin.changes.detail}
                  </button>
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
      {selected && <ChangeDetail change={selected} onClose={() => setOpen(null)} />}
    </>
  );
}
