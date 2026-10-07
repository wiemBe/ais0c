import { useState, type FormEvent } from "react";
import type { CaseVerdict, FeedbackReason } from "../api/types";
import { tr } from "../i18n/tr";
import { EnumSelect, ErrorNotice, Field, enumKeys } from "./common";

export const COMMENT_MAX = 500;

export type FeedbackValues = { verdict: CaseVerdict; reason: FeedbackReason; comment?: string };

/** The verdict, the required reason and the optional comment (T-029 criteria 5 and 6). The
 * reason is shown as "geri bildirim nedeni" (T-66 (4)). */
export function FeedbackForm({
  submitLabel,
  pending,
  error,
  onSubmit,
}: {
  submitLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: (values: FeedbackValues) => void;
}) {
  const [verdict, setVerdict] = useState<CaseVerdict | "">("");
  const [reason, setReason] = useState<FeedbackReason | "">("");
  const [comment, setComment] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (verdict === "") return setProblem(tr.feedback.verdictRequired);
    if (reason === "") return setProblem(tr.feedback.reasonRequired);
    if (comment.length > COMMENT_MAX) return setProblem(tr.feedback.commentTooLong);
    setProblem(null);
    onSubmit({ verdict, reason, comment: comment.trim() ? comment : undefined });
  };

  return (
    <form onSubmit={submit}>
      <div className="row" style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
        <EnumSelect
          label={tr.feedback.verdict}
          value={verdict}
          options={enumKeys(tr.verdict)}
          labels={tr.verdict}
          emptyLabel={tr.feedback.choose}
          onChange={setVerdict}
        />
        <EnumSelect
          label={tr.feedback.reason}
          value={reason}
          options={enumKeys(tr.feedbackReason)}
          labels={tr.feedbackReason}
          emptyLabel={tr.feedback.choose}
          onChange={setReason}
        />
      </div>
      <Field label={tr.feedback.comment}>
        <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} />
      </Field>
      {problem && (
        <p role="alert" style={{ color: "var(--danger)" }}>
          {problem}
        </p>
      )}
      <ErrorNotice error={error} />
      <p>
        <button type="submit" disabled={pending}>
          {submitLabel}
        </button>
      </p>
    </form>
  );
}
