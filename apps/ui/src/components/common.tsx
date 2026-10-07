import { useState, type ReactNode } from "react";
import { errorMessage } from "../api/errors";
import { tr } from "../i18n/tr";
import styles from "./ui.module.css";

export function Loading() {
  return <p className={styles.muted}>{tr.common.loading}</p>;
}

export function Empty() {
  return <p className={styles.muted}>{tr.common.empty}</p>;
}

/** A failed request, in Turkish; the API's `detail` is never shown. */
export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className={styles.error}>
      {errorMessage(error)}
    </p>
  );
}

export function Notice({ children }: { children: ReactNode }) {
  return (
    <p role="status" className={styles.ok}>
      {children}
    </p>
  );
}

export function Badge({
  children,
  tone,
}: {
  children: ReactNode;
  tone?: "critical" | "high" | "good";
}) {
  const toneClass = tone ? styles[tone] : "";
  return <span className={`${styles.badge} ${toneClass}`}>{children}</span>;
}

export function LevelBadge({ level }: { level: keyof typeof tr.level | null | undefined }) {
  if (!level) return <>{tr.common.none}</>;
  const tone = level === "critical" ? "critical" : level === "high" ? "high" : undefined;
  return <Badge tone={tone}>{tr.level[level]}</Badge>;
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className={styles.field}>
      <span>{label}</span>
      {children}
    </label>
  );
}

/** A select over an enum whose labels come from the i18n table; the empty choice is "all". */
export function EnumSelect<T extends string>({
  label,
  value,
  options,
  labels,
  onChange,
  emptyLabel = tr.common.all,
  required = false,
}: {
  label: string;
  value: T | "";
  options: readonly T[];
  labels: Record<T, string>;
  onChange: (value: T | "") => void;
  emptyLabel?: string;
  required?: boolean;
}) {
  return (
    <Field label={label}>
      <select
        value={value}
        required={required}
        onChange={(e) => onChange(e.target.value as T | "")}
      >
        <option value="">{emptyLabel}</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {labels[option]}
          </option>
        ))}
      </select>
    </Field>
  );
}

export function MoreButton({
  hasNext,
  fetching,
  onMore,
}: {
  hasNext: boolean;
  fetching: boolean;
  onMore: () => void;
}) {
  if (!hasNext) return null;
  return (
    <p>
      <button type="button" className="secondary" disabled={fetching} onClick={onMore}>
        {tr.common.more}
      </button>
    </p>
  );
}

/** Copies text to the clipboard; the text itself is shown by the caller as plain text. */
export function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="secondary"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => setDone(true));
      }}
    >
      {done ? tr.common.copied : tr.common.copy}
    </button>
  );
}

export function Dialog({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div className={styles.dialog}>
      <div role="dialog" aria-modal="true" aria-label={label} className={styles.dialogBody}>
        {children}
      </div>
    </div>
  );
}

export function enumKeys<T extends string>(labels: Record<T, string>): T[] {
  return Object.keys(labels) as T[];
}
