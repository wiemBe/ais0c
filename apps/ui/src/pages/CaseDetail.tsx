import { Link, useParams } from "react-router-dom";
import { useCase, useCaseFeedback, useCaseSteps, usePostFeedback } from "../api/hooks";
import type { CaseDetail as CaseDetailData, UrgentEvent } from "../api/types";
import {
  Badge,
  CopyButton,
  Empty,
  ErrorNotice,
  LevelBadge,
  Loading,
  Notice,
} from "../components/common";
import { FeedbackForm } from "../components/FeedbackFields";
import styles from "../components/ui.module.css";
import { formatRange, formatTime, slaState } from "../format";
import { tr } from "../i18n/tr";

/** The console link is shown only if it is an https URL (the API validates its template too). */
function QradarLink({ url }: { url: string | null | undefined }) {
  if (!url || !url.startsWith("https://")) return null;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer">
      {tr.common.openInQradar}
    </a>
  );
}

function UrgentEventCard({ event }: { event: UrgentEvent }) {
  return (
    <div className={styles.panel}>
      <strong>
        #{event.rank} {event.event_name}
      </strong>{" "}
      <span className={styles.muted}>{formatTime(event.time)}</span>
      <p>{event.reason}</p>
      <p className={styles.muted}>
        {tr.case.logSource}: {event.log_source}
        {event.username && ` · ${tr.case.user}: ${event.username}`}
        {event.source && ` · ${tr.case.source}: ${event.source}`}
        {event.destination && ` · ${tr.case.destination}: ${event.destination}`}
        {` · ${tr.case.evidenceIds}: ${event.evidence_id}`}
      </p>
      {event.checklist.length > 0 && (
        <>
          <strong>{tr.case.checklist}</strong>
          <ul className={styles.checklist}>
            {event.checklist.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </>
      )}
      {event.aql && (
        <>
          <strong>{tr.case.aql}</strong> <CopyButton text={event.aql} />
          <pre>{event.aql}</pre>
        </>
      )}
    </div>
  );
}

function Summary({ detail }: { detail: CaseDetailData }) {
  const { report, case: row } = detail;
  if (report) {
    return (
      <>
        {report.injection_suspected && (
          <p role="alert" className={styles.error}>
            {tr.case.injection}
          </p>
        )}
        <p style={{ whiteSpace: "pre-wrap" }}>{report.summary_tr}</p>
      </>
    );
  }
  return (
    <p className={styles.muted}>
      {row.status === "no_ai_decision" ? tr.case.undecided : tr.case.noReport}
    </p>
  );
}

function Steps({ caseId }: { caseId: string }) {
  const steps = useCaseSteps(caseId);
  if (steps.isPending) return <Loading />;
  if (steps.error) return <ErrorNotice error={steps.error} />;
  if (steps.data.length === 0) return <Empty />;
  return (
    <table>
      <thead>
        <tr>
          <th>{tr.case.agent}</th>
          <th>{tr.case.model}</th>
          <th>{tr.queue.cols.status}</th>
          <th>{tr.case.tokens}</th>
          <th>{tr.case.duration}</th>
          <th>{tr.case.toolCalls}</th>
        </tr>
      </thead>
      <tbody>
        {steps.data.map(({ step, tool_calls }) => (
          <tr key={step.run_id}>
            <td>
              {step.agent_id} <span className={styles.muted}>{step.agent_version}</span>
            </td>
            <td>{step.model_alias}</td>
            <td>{step.status ? tr.runStatus[step.status] : tr.common.none}</td>
            <td>{step.tokens}</td>
            <td>
              {step.duration_seconds == null
                ? tr.common.none
                : `${step.duration_seconds.toFixed(1)} ${tr.case.seconds}`}
            </td>
            <td>
              {tool_calls.length === 0
                ? tr.common.none
                : tool_calls.map((call, index) => (
                    <div key={index}>
                      {call.tool_id} · {tr.toolStatus[call.status]} ·{" "}
                      {tr.policyDecision[call.policy_decision]}
                      {` · ${call.latency_ms} ms`}
                      {call.deny_reason && ` · ${tr.case.denyReason}: ${call.deny_reason}`}
                    </div>
                  ))}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Feedback({ caseId }: { caseId: string }) {
  const past = useCaseFeedback(caseId);
  const send = usePostFeedback(caseId);
  return (
    <>
      <FeedbackForm
        submitLabel={tr.feedback.submit}
        pending={send.isPending}
        error={send.error}
        onSubmit={(values) => send.mutate({ case_id: caseId, ...values })}
      />
      {send.isSuccess && <Notice>{tr.feedback.sent}</Notice>}
      {past.data && past.data.length > 0 && (
        <>
          <h3>{tr.feedback.past}</h3>
          <ul>
            {past.data.map((item, index) => (
              <li key={index}>
                {formatTime(item.created_at)} · {item.user_subject} · {tr.verdict[item.verdict]} ·{" "}
                {tr.feedbackReason[item.reason]}
                {item.comment && ` · ${item.comment}`}
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

export function CaseDetail() {
  const { caseId = "" } = useParams();
  const query = useCase(caseId);
  if (query.isPending) return <Loading />;
  if (query.error) return <ErrorNotice error={query.error} />;
  const detail = query.data;
  const row = detail.case;
  const sla = slaState(row);
  const verification = detail.verification;

  return (
    <>
      <p>
        <Link to="/">{tr.case.back}</Link>
      </p>
      <h1>
        {tr.case.title} {row.case_id}
      </h1>
      <div className={styles.panel}>
        <div className={styles.row}>
          <LevelBadge level={row.notify_level} />
          <Badge>{row.verdict ? tr.verdict[row.verdict] : tr.common.none}</Badge>
          <Badge>{row.confidence ? tr.confidence[row.confidence] : tr.common.none}</Badge>
          <Badge tone={sla === "late" ? "critical" : sla === "on_time" ? "good" : undefined}>
            {tr.sla[sla]}
          </Badge>
          <Badge>{tr.caseStatus[row.status]}</Badge>
        </div>
        <p className={styles.muted}>
          {tr.case.evaluation}: {detail.evaluation_no} · {tr.case.slaDue}:{" "}
          {formatTime(row.sla_due_at)} · {tr.case.decidedAt}: {formatTime(row.decided_at)}
        </p>
        {row.offense_id != null && (
          <p>
            {tr.case.qradarOffense}: {row.offense_id} <QradarLink url={row.qradar_offense_url} />
            {row.offense_description && (
              <span className={styles.muted}> · {row.offense_description}</span>
            )}
          </p>
        )}
        {row.group_id && (
          <p>
            {tr.case.group}:{" "}
            <Link to={`/groups/${encodeURIComponent(row.group_id)}`}>{row.group_id}</Link>
          </p>
        )}
      </div>

      <h2>{tr.case.summary}</h2>
      <div className={styles.panel}>
        <Summary detail={detail} />
      </div>

      <h2>{tr.case.urgentEvents}</h2>
      {detail.urgent_events?.length ? (
        detail.urgent_events.map((event) => <UrgentEventCard key={event.rank} event={event} />)
      ) : (
        <Empty />
      )}

      <h2>{tr.case.recommendations}</h2>
      {detail.recommendations?.length ? (
        <ul>
          {detail.recommendations.map((item, index) => (
            <li key={index}>
              <strong>{tr.actionType[item.action_type]}</strong>
              {item.target && ` — ${tr.case.target}: ${item.target}`}
              <div>{item.rationale}</div>
            </li>
          ))}
        </ul>
      ) : (
        <Empty />
      )}

      <h2>{tr.case.verification}</h2>
      {verification ? (
        <div className={styles.panel}>
          <Badge tone={verification.agrees ? "good" : "critical"}>
            {verification.agrees ? tr.case.agrees : tr.case.disagrees}
          </Badge>{" "}
          {tr.verdict[verification.verdict]} · {tr.confidence[verification.confidence]} ·{" "}
          {tr.runStatus[verification.status]}
          {verification.injection_suspected && (
            <p role="alert" className={styles.error}>
              {tr.case.injection}
            </p>
          )}
          {verification.disagreements.length > 0 && (
            <>
              <strong>{tr.case.disagreements}</strong>
              <ul>
                {verification.disagreements.map((item, index) => (
                  <li key={index}>
                    {item.claim_text} — {item.reason}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      ) : (
        <p className={styles.muted}>{tr.case.noVerification}</p>
      )}

      <h2>{tr.case.dataGaps}</h2>
      {detail.data_gaps?.length ? (
        <ul>
          {detail.data_gaps.map((gap, index) => (
            <li key={index}>
              {gap.source} · {tr.dataGapReason[gap.reason]} ·{" "}
              {formatRange(gap.period_start, gap.period_end)}
            </li>
          ))}
        </ul>
      ) : (
        <Empty />
      )}

      <h2>{tr.case.evidence}</h2>
      <p className={styles.muted}>
        {tr.case.evidenceNote} <QradarLink url={row.qradar_offense_url} />
      </p>
      {detail.evidence?.length ? (
        <table>
          <thead>
            <tr>
              <th>{tr.case.evidenceIds}</th>
              <th>{tr.case.source}</th>
              <th>{tr.case.tool}</th>
              <th>{tr.case.query}</th>
              <th>{tr.case.window}</th>
            </tr>
          </thead>
          <tbody>
            {detail.evidence.map((item) => (
              <tr key={item.evidence_id}>
                <td>
                  {item.evidence_id}
                  <div>
                    <Badge tone={item.cited ? "good" : undefined}>
                      {item.cited ? tr.case.cited : tr.case.notCited}
                    </Badge>
                  </div>
                </td>
                <td>{tr.evidenceSource[item.source]}</td>
                <td>{item.tool_id || tr.common.none}</td>
                <td>
                  <pre>{item.query_text}</pre>
                  <span className={styles.muted}>
                    {tr.case.queryHash}: {item.query_hash}
                  </span>
                </td>
                <td>{formatRange(item.time_start, item.time_end)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty />
      )}

      <h2>{tr.case.steps}</h2>
      <Steps caseId={row.case_id} />

      <h2>{tr.case.notes}</h2>
      {detail.notes?.length ? (
        <ul>
          {detail.notes.map((note, index) => (
            <li key={index}>
              {tr.case.evaluation} {note.evaluation_no} · {tr.noteStatus[note.status]} ·{" "}
              {formatTime(note.written_at)}
            </li>
          ))}
        </ul>
      ) : (
        <Empty />
      )}

      <h2>{tr.case.notifications}</h2>
      {detail.notifications?.length ? (
        <ul>
          {detail.notifications.map((mail, index) => (
            <li key={index}>
              {tr.emailKind[mail.kind]} · {tr.notificationStatus[mail.status]} · {mail.subject} ·{" "}
              {tr.case.recipients}: {mail.recipients.join(", ") || tr.common.none} ·{" "}
              {formatTime(mail.sent_at)}
            </li>
          ))}
        </ul>
      ) : (
        <Empty />
      )}

      <h2>{tr.feedback.title}</h2>
      <div className={styles.panel}>
        <Feedback caseId={row.case_id} />
      </div>
    </>
  );
}
