import { useState } from "react";
import { useSla } from "../api/hooks";
import { Empty, ErrorNotice, Field, Loading } from "../components/common";
import styles from "../components/ui.module.css";
import { formatRange, istanbulDayEnd, istanbulDayStart } from "../format";
import { tr } from "../i18n/tr";

export function Sla() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const query = useSla({
    from: from ? istanbulDayStart(from) : undefined,
    to: to ? istanbulDayEnd(to) : undefined,
  });
  return (
    <>
      <h1>{tr.slaPage.title}</h1>
      <div className={styles.filters}>
        <Field label={tr.common.from}>
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </Field>
        <Field label={tr.common.to}>
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </Field>
      </div>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : !query.data ? null : (
        <>
          <p className={styles.muted}>{formatRange(query.data.from, query.data.to)}</p>
          {query.data.buckets.length === 0 ? (
            <Empty />
          ) : (
            <table>
              <thead>
                <tr>
                  <th>{tr.slaPage.cols.floor}</th>
                  <th>{tr.slaPage.cols.total}</th>
                  <th>{tr.slaPage.cols.onTime}</th>
                  <th>{tr.slaPage.cols.late}</th>
                  <th>{tr.slaPage.cols.undecided}</th>
                  <th>{tr.slaPage.cols.running}</th>
                  <th>{tr.slaPage.cols.closed}</th>
                </tr>
              </thead>
              <tbody>
                {query.data.buckets.map((row) => (
                  <tr key={row.floor_level}>
                    <td>
                      {row.floor_level === "none" ? tr.slaPage.none : tr.level[row.floor_level]}
                    </td>
                    <td>{row.total}</td>
                    <td>{row.on_time}</td>
                    <td>{row.late}</td>
                    <td>{row.undecided}</td>
                    <td>{row.running}</td>
                    <td>{row.closed}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </>
  );
}
