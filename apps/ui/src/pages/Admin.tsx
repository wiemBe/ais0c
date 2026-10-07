import { useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import {
  useAddAsset,
  useCriticalAssets,
  useDeleteAsset,
  usePlatformFlags,
  useRecipients,
  useRoutes,
  useSaveRecipients,
  useSaveRoutes,
  useSetFlag,
} from "../api/hooks";
import { useIsAdmin } from "../api/session";
import type {
  CriticalAssetKind,
  EmailKind,
  Level,
  NotificationRoute,
  PlatformFlagState,
} from "../api/types";
import {
  Badge,
  Dialog,
  Empty,
  EnumSelect,
  ErrorNotice,
  Field,
  Loading,
  Notice,
  enumKeys,
} from "../components/common";
import styles from "../components/ui.module.css";
import { formatTime } from "../format";
import { tr } from "../i18n/tr";

// --- kill switch (architecture §26, T-63) ---------------------------------------------------

function KillSwitch({ flag }: { flag: PlatformFlagState }) {
  const admin = useIsAdmin();
  const set = useSetFlag(flag.name);
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const change = (enabled: boolean) => {
    if (!reason.trim()) return setProblem(tr.common.reasonRequired);
    setProblem(null);
    set.mutate(
      { enabled, reason },
      {
        onSuccess: () => {
          setConfirming(false);
          setReason("");
        },
      },
    );
  };

  return (
    <div className={styles.panel}>
      <p>
        <Badge tone={flag.enabled ? "high" : "good"}>
          {flag.enabled ? tr.common.yes : tr.common.no}
        </Badge>{" "}
        {flag.enabled ? tr.admin.killOn : tr.admin.killOff}
      </p>
      {flag.changed_by && (
        <p className={styles.muted}>
          {tr.admin.changedBy}: {flag.changed_by} · {tr.admin.changedAt}:{" "}
          {formatTime(flag.changed_at)} · {tr.admin.reason}: {flag.reason}
        </p>
      )}
      {admin && (
        <>
          <Field label={tr.admin.reasonLabel}>
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
          {problem && (
            <p role="alert" className={styles.error}>
              {problem}
            </p>
          )}
          <ErrorNotice error={set.error} />
          <p>
            {flag.enabled ? (
              // Closing is one step: it is the emergency stop (T-63).
              <button
                type="button"
                className="danger"
                disabled={set.isPending}
                onClick={() => change(false)}
              >
                {tr.admin.disable}
              </button>
            ) : (
              <button
                type="button"
                disabled={set.isPending}
                onClick={() =>
                  reason.trim()
                    ? (setProblem(null), setConfirming(true))
                    : setProblem(tr.common.reasonRequired)
                }
              >
                {tr.admin.enable}
              </button>
            )}
          </p>
        </>
      )}
      {confirming && (
        <Dialog label={tr.admin.enable}>
          <p>{tr.admin.enableConfirm}</p>
          <div className={styles.row}>
            <button type="button" disabled={set.isPending} onClick={() => change(true)}>
              {tr.common.confirm}
            </button>
            <button type="button" className="secondary" onClick={() => setConfirming(false)}>
              {tr.common.cancel}
            </button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

// --- critical assets --------------------------------------------------------------------------

function CriticalAssets() {
  const admin = useIsAdmin();
  const list = useCriticalAssets();
  const add = useAddAsset();
  const remove = useDeleteAsset();
  const [kind, setKind] = useState<CriticalAssetKind | "">("");
  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");
  const [level, setLevel] = useState<Level | "">("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (kind === "" || level === "" || !value.trim() || !label.trim()) return;
    add.mutate(
      { kind, value, label, level },
      {
        onSuccess: () => {
          setValue("");
          setLabel("");
        },
      },
    );
  };

  return (
    <>
      <ErrorNotice error={list.error} />
      {list.isPending ? (
        <Loading />
      ) : !list.data ? null : list.data.length === 0 ? (
        <Empty />
      ) : (
        <table>
          <thead>
            <tr>
              <th>{tr.admin.assetKind}</th>
              <th>{tr.admin.assetValue}</th>
              <th>{tr.admin.assetLabel}</th>
              <th>{tr.admin.assetLevel}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {list.data.map((asset) => (
              <tr key={asset.id}>
                <td>{tr.criticalAssetKind[asset.kind]}</td>
                <td>{asset.value}</td>
                <td>{asset.label}</td>
                <td>{tr.level[asset.level]}</td>
                <td>
                  {admin && (
                    <button
                      type="button"
                      className="secondary"
                      disabled={remove.isPending}
                      onClick={() => remove.mutate(asset.id)}
                    >
                      {tr.common.remove}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <ErrorNotice error={remove.error} />
      {admin && (
        <form onSubmit={submit} className={styles.filters} style={{ marginTop: "0.75rem" }}>
          <EnumSelect
            label={tr.admin.assetKind}
            value={kind}
            options={enumKeys(tr.criticalAssetKind)}
            labels={tr.criticalAssetKind}
            emptyLabel={tr.common.none}
            onChange={setKind}
          />
          <Field label={tr.admin.assetValue}>
            <input maxLength={200} value={value} onChange={(e) => setValue(e.target.value)} />
          </Field>
          <Field label={tr.admin.assetLabel}>
            <input maxLength={300} value={label} onChange={(e) => setLabel(e.target.value)} />
          </Field>
          <EnumSelect
            label={tr.admin.assetLevel}
            value={level}
            options={enumKeys(tr.level)}
            labels={tr.level}
            emptyLabel={tr.common.none}
            onChange={setLevel}
          />
          <button type="submit" disabled={add.isPending}>
            {tr.common.add}
          </button>
        </form>
      )}
      <ErrorNotice error={add.error} />
    </>
  );
}

// --- recipient groups (admin) -----------------------------------------------------------------

const splitEmails = (text: string) =>
  text
    .split(/[\s,;]+/)
    .map((item) => item.trim())
    .filter(Boolean);

/** What the 422/409 answers name besides their code: the refused domains and the routes' groups. */
function ProblemLists({ error }: { error: unknown }) {
  if (!(error instanceof ApiError) || !error.problem) return null;
  const { domains, list_names: listNames } = error.problem;
  return (
    <>
      {domains && domains.length > 0 && (
        <p className={styles.error}>
          {tr.admin.invalidDomains}: {domains.join(", ")}
        </p>
      )}
      {listNames && listNames.length > 0 && (
        <p className={styles.error}>
          {tr.admin.groupsInUse}: {listNames.join(", ")}
        </p>
      )}
    </>
  );
}

function RecipientGroupEditor({ name, emails }: { name: string; emails: string[] }) {
  const save = useSaveRecipients(name);
  const [text, setText] = useState(emails.join("\n"));
  return (
    <div className={styles.panel}>
      <strong>{name}</strong>
      <Field label={tr.admin.emailsHelp}>
        <textarea
          rows={Math.max(3, emails.length + 1)}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </Field>
      <ErrorNotice error={save.error} />
      <ProblemLists error={save.error} />
      {save.isSuccess && <Notice>{tr.common.saved}</Notice>}
      <button
        type="button"
        disabled={save.isPending}
        onClick={() => save.mutate(splitEmails(text))}
      >
        {tr.admin.saveGroup}
      </button>
    </div>
  );
}

function Recipients() {
  const view = useRecipients(true);
  const [fresh, setFresh] = useState("");
  const [added, setAdded] = useState<string[]>([]);
  if (view.isPending) return <Loading />;
  if (view.error) return <ErrorNotice error={view.error} />;
  const names = view.data.groups.map((group) => group.list_name);
  return (
    <>
      <p className={styles.muted}>
        {tr.admin.allowedDomains}: {view.data.allowed_domains.join(", ") || tr.common.none}
      </p>
      {view.data.groups.map((group) => (
        <RecipientGroupEditor key={group.list_name} name={group.list_name} emails={group.emails} />
      ))}
      {added
        .filter((name) => !names.includes(name))
        .map((name) => (
          <RecipientGroupEditor key={name} name={name} emails={[]} />
        ))}
      <div className={styles.row}>
        <input
          aria-label={tr.admin.newGroup}
          placeholder={tr.admin.newGroup}
          value={fresh}
          onChange={(e) => setFresh(e.target.value)}
        />
        <button
          type="button"
          className="secondary"
          disabled={!fresh.trim()}
          onClick={() => {
            setAdded((current) => [...current, fresh.trim()]);
            setFresh("");
          }}
        >
          {tr.admin.addGroup}
        </button>
      </div>
    </>
  );
}

// --- routing table (admin) --------------------------------------------------------------------

const KIND_ORDER = enumKeys(tr.emailKind);
const LEVEL_ORDER: (Level | "")[] = ["", ...enumKeys(tr.level)];

const cellKey = (kind: EmailKind, level: Level | "") => `${kind}|${level}`;

function Routes() {
  const routes = useRoutes(true);
  const recipients = useRecipients(true);
  const save = useSaveRoutes();
  const [edited, setEdited] = useState<Record<string, string[]> | null>(null);
  if (routes.isPending || recipients.isPending) return <Loading />;
  if (routes.error || recipients.error)
    return <ErrorNotice error={routes.error ?? recipients.error} />;

  const fromServer = (rows: NotificationRoute[]) => {
    const cells: Record<string, string[]> = {};
    for (const row of rows) {
      const key = cellKey(row.kind, row.level ?? "");
      cells[key] = [...(cells[key] ?? []), row.list_name];
    }
    return cells;
  };
  const cells = edited ?? fromServer(routes.data);
  const groups = recipients.data.groups.map((group) => group.list_name);

  const toggle = (key: string, name: string) =>
    setEdited((current) => {
      const base = current ?? fromServer(routes.data);
      const present = base[key] ?? [];
      return {
        ...base,
        [key]: present.includes(name)
          ? present.filter((item) => item !== name)
          : [...present, name],
      };
    });

  const submit = () => {
    const entries = KIND_ORDER.flatMap((kind) =>
      LEVEL_ORDER.flatMap((level) =>
        (cells[cellKey(kind, level)] ?? []).map((list_name) => ({
          kind,
          level: level === "" ? null : level,
          list_name,
        })),
      ),
    );
    save.mutate(entries, { onSuccess: () => setEdited(null) });
  };

  return (
    <>
      <table>
        <thead>
          <tr>
            <th>{tr.admin.routeKind}</th>
            {LEVEL_ORDER.map((level) => (
              <th key={level}>{level === "" ? tr.admin.anyLevel : tr.level[level]}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {KIND_ORDER.map((kind) => (
            <tr key={kind}>
              <td>{tr.emailKind[kind]}</td>
              {LEVEL_ORDER.map((level) => {
                const key = cellKey(kind, level);
                return (
                  <td key={key}>
                    {groups.map((name) => (
                      <label key={name} style={{ display: "block" }}>
                        <input
                          type="checkbox"
                          aria-label={`${tr.emailKind[kind]} ${level === "" ? tr.admin.anyLevel : tr.level[level]} ${name}`}
                          checked={(cells[key] ?? []).includes(name)}
                          onChange={() => toggle(key, name)}
                        />{" "}
                        {name}
                      </label>
                    ))}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <ErrorNotice error={save.error} />
      <ProblemLists error={save.error} />
      {save.isSuccess && edited === null && <Notice>{tr.common.saved}</Notice>}
      <p>
        <button type="button" disabled={save.isPending || edited === null} onClick={submit}>
          {tr.admin.saveRoutes}
        </button>
      </p>
    </>
  );
}

// --- page -------------------------------------------------------------------------------------

export function Admin() {
  const admin = useIsAdmin();
  const flags = usePlatformFlags();
  return (
    <>
      <h1>{tr.admin.title}</h1>
      <h2>{tr.admin.killSwitch}</h2>
      <ErrorNotice error={flags.error} />
      {flags.isPending ? (
        <Loading />
      ) : (
        (flags.data ?? [])
          .filter((flag) => flag.name === "writes_enabled")
          .map((flag) => <KillSwitch key={flag.name} flag={flag} />)
      )}
      <h2>{tr.admin.assets}</h2>
      <CriticalAssets />
      {admin ? (
        <>
          <h2>{tr.admin.recipients}</h2>
          <Recipients />
          <h2>{tr.admin.routes}</h2>
          <Routes />
        </>
      ) : (
        <p className={styles.muted}>{tr.admin.adminOnly}</p>
      )}
    </>
  );
}
