import { useState, type FormEvent } from "react";
import {
  useAcceptDraft,
  useCatalogLogSources,
  useCatalogRules,
  useSaveLogSource,
  useSaveRule,
  useStartSync,
} from "../api/hooks";
import { useIsAdmin } from "../api/session";
import type { CatalogLogSource, CatalogMode, CatalogRule, Level } from "../api/types";
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

type Tri = "" | "true" | "false";

const triValue = (value: Tri): boolean | undefined => (value === "" ? undefined : value === "true");

function TriSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: Tri;
  onChange: (v: Tri) => void;
}) {
  return (
    <Field label={label}>
      <select value={value} onChange={(e) => onChange(e.target.value as Tri)}>
        <option value="">{tr.common.all}</option>
        <option value="true">{tr.common.yes}</option>
        <option value="false">{tr.common.no}</option>
      </select>
    </Field>
  );
}

function yesNo(value: boolean): string {
  return value ? tr.common.yes : tr.common.no;
}

// --- rules ------------------------------------------------------------------------------------

function RuleDialog({ rule, onClose }: { rule: CatalogRule; onClose: () => void }) {
  const save = useSaveRule(rule.rule_id);
  const [mode, setMode] = useState<CatalogMode>(rule.mode);
  const [minLevel, setMinLevel] = useState<Level | "">(rule.min_level ?? "");
  const [automated, setAutomated] = useState(rule.has_automated_action);
  const [note, setNote] = useState(rule.context_note ?? "");
  const [techniques, setTechniques] = useState((rule.attack_techniques ?? []).join(", "));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(
      {
        mode,
        min_level: minLevel === "" ? null : minLevel,
        has_automated_action: automated,
        context_note: note.trim() ? note : null,
        attack_techniques: techniques
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      },
      { onSuccess: onClose },
    );
  };

  return (
    <Dialog label={tr.catalog.edit}>
      <form onSubmit={submit}>
        <h2>
          {rule.rule_id} · {rule.rule_name}
        </h2>
        <EnumSelect
          label={tr.catalog.cols.mode}
          value={mode}
          options={enumKeys(tr.catalogMode)}
          labels={tr.catalogMode}
          emptyLabel={tr.catalog.noLevel}
          onChange={(v) => v && setMode(v)}
        />
        <EnumSelect
          label={tr.catalog.cols.minLevel}
          value={minLevel}
          options={enumKeys(tr.level)}
          labels={tr.level}
          emptyLabel={tr.catalog.noLevel}
          onChange={setMinLevel}
        />
        <Field label={tr.catalog.automatedAction}>
          <input
            type="checkbox"
            checked={automated}
            onChange={(e) => setAutomated(e.target.checked)}
          />
        </Field>
        <Field label={tr.catalog.techniques}>
          <input value={techniques} onChange={(e) => setTechniques(e.target.value)} />
        </Field>
        <Field label={tr.catalog.contextNote}>
          <textarea
            rows={3}
            maxLength={600}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </Field>
        <ErrorNotice error={save.error} />
        <div className={styles.row}>
          <button type="submit" disabled={save.isPending}>
            {tr.common.save}
          </button>
          <button type="button" className="secondary" onClick={onClose}>
            {tr.common.cancel}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function AcceptDraftButton({ rule }: { rule: CatalogRule }) {
  const accept = useAcceptDraft(rule.rule_id);
  return (
    <>
      <button
        type="button"
        className="secondary"
        disabled={accept.isPending}
        onClick={() => accept.mutate(undefined)}
      >
        {tr.catalog.acceptDraft}
      </button>
      <ErrorNotice error={accept.error} />
      {accept.isSuccess && <Notice>{tr.catalog.draftAccepted}</Notice>}
    </>
  );
}

function Rules() {
  const admin = useIsAdmin();
  const [defined, setDefined] = useState<Tri>("");
  const [mode, setMode] = useState<CatalogMode | "">("");
  const [enabled, setEnabled] = useState<Tri>("");
  const [missing, setMissing] = useState<Tri>("");
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<CatalogRule | null>(null);
  const list = useCatalogRules({
    defined: triValue(defined),
    mode,
    qradar_enabled: triValue(enabled),
    missing: triValue(missing),
    q,
  });
  const rows = list.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <>
      <div className={styles.filters}>
        <TriSelect label={tr.catalog.filters.defined} value={defined} onChange={setDefined} />
        <EnumSelect
          label={tr.catalog.filters.mode}
          value={mode}
          options={enumKeys(tr.catalogMode)}
          labels={tr.catalogMode}
          onChange={setMode}
        />
        <TriSelect label={tr.catalog.filters.qradarEnabled} value={enabled} onChange={setEnabled} />
        <TriSelect label={tr.catalog.filters.missing} value={missing} onChange={setMissing} />
        <Field label={tr.catalog.filters.search}>
          <input value={q} onChange={(e) => setQ(e.target.value)} />
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
              <th>{tr.catalog.cols.id}</th>
              <th>{tr.catalog.cols.name}</th>
              <th>{tr.catalog.cols.defined}</th>
              <th>{tr.catalog.cols.mode}</th>
              <th>{tr.catalog.cols.minLevel}</th>
              <th>{tr.catalog.cols.qradar}</th>
              <th>{tr.catalog.cols.note}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((rule) => (
              <tr key={rule.rule_id}>
                <td>{rule.rule_id}</td>
                <td>
                  {rule.rule_name}
                  {rule.missing_since && (
                    <div>
                      <Badge tone="high">{tr.catalog.cols.missing}</Badge>
                    </div>
                  )}
                </td>
                <td>{yesNo(rule.defined)}</td>
                <td>{tr.catalogMode[rule.mode]}</td>
                <td>{rule.min_level ? tr.level[rule.min_level] : tr.common.none}</td>
                <td>{yesNo(rule.qradar_enabled)}</td>
                <td>
                  {rule.context_note}
                  {rule.ai_draft_note && (
                    <div>
                      <span className={styles.muted}>{tr.catalog.cols.draft}: </span>
                      {rule.ai_draft_note}
                      {admin && <AcceptDraftButton rule={rule} />}
                    </div>
                  )}
                </td>
                <td>
                  {admin && (
                    <button type="button" className="secondary" onClick={() => setEditing(rule)}>
                      {tr.catalog.edit}
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
      {editing && <RuleDialog rule={editing} onClose={() => setEditing(null)} />}
    </>
  );
}

// --- log sources ------------------------------------------------------------------------------

function LogSourceDialog({ source, onClose }: { source: CatalogLogSource; onClose: () => void }) {
  const save = useSaveLogSource(source.log_source_id);
  const [description, setDescription] = useState(source.description ?? "");
  const [owner, setOwner] = useState(source.owner ?? "");
  const [criticality, setCriticality] = useState<Level | "">(source.criticality ?? "");
  const [inScope, setInScope] = useState(source.in_scope);
  const [note, setNote] = useState(source.context_note ?? "");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(
      {
        description: description.trim() ? description : null,
        owner: owner.trim() ? owner : null,
        criticality: criticality === "" ? null : criticality,
        in_scope: inScope,
        context_note: note.trim() ? note : null,
      },
      { onSuccess: onClose },
    );
  };

  return (
    <Dialog label={tr.catalog.edit}>
      <form onSubmit={submit}>
        <h2>
          {source.log_source_id} · {source.name}
        </h2>
        <Field label={tr.catalog.description}>
          <input
            maxLength={300}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
        <Field label={tr.catalog.owner}>
          <input maxLength={120} value={owner} onChange={(e) => setOwner(e.target.value)} />
        </Field>
        <EnumSelect
          label={tr.catalog.cols.criticality}
          value={criticality}
          options={enumKeys(tr.level)}
          labels={tr.level}
          emptyLabel={tr.catalog.noLevel}
          onChange={setCriticality}
        />
        <Field label={tr.catalog.inScope}>
          <input type="checkbox" checked={inScope} onChange={(e) => setInScope(e.target.checked)} />
        </Field>
        <Field label={tr.catalog.contextNote}>
          <textarea
            rows={3}
            maxLength={600}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </Field>
        <ErrorNotice error={save.error} />
        <div className={styles.row}>
          <button type="submit" disabled={save.isPending}>
            {tr.common.save}
          </button>
          <button type="button" className="secondary" onClick={onClose}>
            {tr.common.cancel}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function LogSources() {
  const admin = useIsAdmin();
  const [defined, setDefined] = useState<Tri>("");
  const [inScope, setInScope] = useState<Tri>("");
  const [missing, setMissing] = useState<Tri>("");
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<CatalogLogSource | null>(null);
  const list = useCatalogLogSources({
    defined: triValue(defined),
    in_scope: triValue(inScope),
    missing: triValue(missing),
    q,
  });
  const rows = list.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <>
      <div className={styles.filters}>
        <TriSelect label={tr.catalog.filters.defined} value={defined} onChange={setDefined} />
        <TriSelect label={tr.catalog.filters.inScope} value={inScope} onChange={setInScope} />
        <TriSelect label={tr.catalog.filters.missing} value={missing} onChange={setMissing} />
        <Field label={tr.catalog.filters.search}>
          <input value={q} onChange={(e) => setQ(e.target.value)} />
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
              <th>{tr.catalog.cols.id}</th>
              <th>{tr.catalog.cols.name}</th>
              <th>{tr.catalog.cols.type}</th>
              <th>{tr.catalog.cols.defined}</th>
              <th>{tr.catalog.cols.scope}</th>
              <th>{tr.catalog.cols.owner}</th>
              <th>{tr.catalog.cols.criticality}</th>
              <th>{tr.catalog.cols.updated}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((source) => (
              <tr key={source.log_source_id}>
                <td>{source.log_source_id}</td>
                <td>
                  {source.name}
                  {source.missing_since && (
                    <div>
                      <Badge tone="high">{tr.catalog.cols.missing}</Badge>
                    </div>
                  )}
                </td>
                <td>{source.type_name}</td>
                <td>{yesNo(source.defined)}</td>
                <td>{yesNo(source.in_scope)}</td>
                <td>{source.owner ?? tr.common.none}</td>
                <td>{source.criticality ? tr.level[source.criticality] : tr.common.none}</td>
                <td>{formatTime(source.updated_at)}</td>
                <td>
                  {admin && (
                    <button type="button" className="secondary" onClick={() => setEditing(source)}>
                      {tr.catalog.edit}
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
      {editing && <LogSourceDialog source={editing} onClose={() => setEditing(null)} />}
    </>
  );
}

// --- page -------------------------------------------------------------------------------------

export function Catalog() {
  const admin = useIsAdmin();
  const [tab, setTab] = useState<"rules" | "logSources">("rules");
  const sync = useStartSync();
  return (
    <>
      <h1>{tr.catalog.title}</h1>
      <div className={styles.row}>
        <button
          type="button"
          className={tab === "rules" ? "" : "secondary"}
          onClick={() => setTab("rules")}
        >
          {tr.catalog.rules}
        </button>
        <button
          type="button"
          className={tab === "logSources" ? "" : "secondary"}
          onClick={() => setTab("logSources")}
        >
          {tr.catalog.logSources}
        </button>
        {admin && (
          <button type="button" disabled={sync.isPending} onClick={() => sync.mutate(undefined)}>
            {tr.catalog.sync}
          </button>
        )}
      </div>
      <ErrorNotice error={sync.error} />
      {sync.isSuccess && <Notice>{tr.catalog.syncStarted}</Notice>}
      <p />
      {tab === "rules" ? <Rules /> : <LogSources />}
    </>
  );
}
