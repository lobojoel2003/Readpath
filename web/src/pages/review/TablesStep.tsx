import { useEffect, useId, useRef, useState } from "react";
import { api, errorMessage, isAbort, isConflict, urls } from "../../api/client";
import type { Table } from "../../api/types";
import { useAnnounce } from "../../components/Announcer";
import { Spinner, StatusBadge } from "../../components/Badges";
import { Icon } from "../../components/Icon";
import { LoadError, Notice } from "../../components/Notice";
import type { StepProps } from "./stepTypes";

/** Table ids change after every save; key cards by position in the document. */
function tableKey(t: Table, i: number): string {
  return t.bbox ? `${t.page}:${t.bbox.map((n) => n.toFixed(4)).join(",")}` : `${t.page}:#${i}`;
}

interface Draft {
  headerRow?: boolean;
  headerColumn?: boolean;
  saving?: boolean;
  error?: string;
}

export function TablesStep({ docId, overview, setRevision, setOverview, onConflict, goNext }: StepProps) {
  const announce = useAnnounce();
  const [tables, setTables] = useState<Table[] | null>(null);
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [loadNonce, setLoadNonce] = useState(0);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [completing, setCompleting] = useState(false);
  const [completeError, setCompleteError] = useState<string | null>(null);
  const revisionRef = useRef(overview.revision);
  revisionRef.current = overview.revision;

  useEffect(() => {
    const ctrl = new AbortController();
    setLoadError(null);
    api.getTables(docId, ctrl.signal).then(
      (res) => {
        setTables(res.tables);
        setRevision(res.revision);
      },
      (err: unknown) => {
        if (!isAbort(err)) setLoadError(err instanceof Error ? err : new Error(String(err)));
      },
    );
    return () => ctrl.abort();
  }, [docId, loadNonce, setRevision]);

  const patch = (key: string, d: Partial<Draft>) => setDrafts((all) => ({ ...all, [key]: { ...all[key], ...d } }));

  async function save(t: Table, key: string) {
    const d = drafts[key] ?? {};
    if (d.saving) return;
    patch(key, { saving: true, error: undefined });
    try {
      const res = await api.putTable(docId, t.id, revisionRef.current, d.headerRow ?? t.header_row, d.headerColumn ?? t.header_column);
      revisionRef.current = res.revision;
      setRevision(res.revision);
      setTables(res.tables);
      setDrafts((all) => ({ ...all, [key]: {} }));
      announce("Table headings saved.");
    } catch (err) {
      if (isConflict(err)) onConflict();
      else patch(key, { saving: false, error: errorMessage(err) });
    }
  }

  async function complete() {
    if (completing) return;
    setCompleting(true);
    setCompleteError(null);
    try {
      const ov = await api.completeStep(docId, "tables", revisionRef.current);
      setOverview(ov);
      announce("Tables step finished.");
      goNext();
    } catch (err) {
      if (isConflict(err)) onConflict();
      else setCompleteError(errorMessage(err));
    } finally {
      setCompleting(false);
    }
  }

  if (loadError) return <LoadError error={loadError} onRetry={() => setLoadNonce((n) => n + 1)} />;
  if (!tables) return <Spinner label="Loading tables…" />;

  const keys = tables.map(tableKey);
  const unsaved = tables
    .map((t, i) => ({ t, i, d: drafts[keys[i]] }))
    .filter(({ t, d }) => d && ((d.headerRow != null && d.headerRow !== t.header_row) || (d.headerColumn != null && d.headerColumn !== t.header_column)));

  return (
    <div className="step stack-lg">
      <div className="step__intro stack-sm">
        <h2>Mark the table headings</h2>
        <p>
          Headings tell screen reader users what each number in a table means. For example, they hear "Tuesday,
          Opening time: 9 AM" instead of just "9 AM".
        </p>
        <p className="refs">WCAG 1.3.1 · PDF/UA 7.5</p>
      </div>

      {tables.length === 0 ? (
        <div className="card empty">
          <Icon name="checkCircle" size={28} />
          <p>No tables found — nothing to do here.</p>
        </div>
      ) : (
        <ol className="figure-list" aria-label="Tables in this PDF">
          {tables.map((t, i) => (
            <TableCard
              key={keys[i]}
              table={t}
              number={i + 1}
              draft={drafts[keys[i]] ?? {}}
              onChange={(d) => patch(keys[i], { ...d, error: undefined })}
              onSave={() => void save(t, keys[i])}
            />
          ))}
        </ol>
      )}

      <div className="card stack-sm step__finish">
        {unsaved.length > 0 && (
          <p className="field__warning">
            <Icon name="alert" size={16} /> You haven't saved your changes to{" "}
            {unsaved.map((u) => `table ${u.i + 1}`).join(", ")}.
          </p>
        )}
        <p>{tables.length === 0 ? "This PDF has no tables to check." : "When the headings look right, finish this step."}</p>
        {completeError && (
          <Notice tone="error" title="That didn't work.">
            <p>{completeError}</p>
          </Notice>
        )}
        <div>
          {tables.length === 0 ? (
            <button type="button" className="btn btn--primary btn--lg" onClick={goNext}>
              Continue <Icon name="arrowRight" size={20} />
            </button>
          ) : (
            <button type="button" className="btn btn--primary btn--lg" onClick={() => void complete()} aria-disabled={completing || undefined}>
              <Icon name="check" size={20} /> {completing ? "Saving…" : "These tables look right"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

interface TableCardProps {
  table: Table;
  number: number;
  draft: Draft;
  onChange: (d: Partial<Draft>) => void;
  onSave: () => void;
}

function TableCard({ table: t, number, draft, onChange, onSave }: TableCardProps) {
  const rowId = useId();
  const colId = useId();
  const errId = useId();
  const headerRow = draft.headerRow ?? t.header_row;
  const headerColumn = draft.headerColumn ?? t.header_column;
  const changed = headerRow !== t.header_row || headerColumn !== t.header_column;
  const headingId = `table-${number}-heading`;
  const hasHeaders = t.header_row || t.header_column || t.rows.some((r) => r.some((c) => c.header));

  return (
    <li className="card figure-card figure-card--table" aria-labelledby={headingId}>
      <div className="figure-card__head">
        <h3 id={headingId}>
          Table {number} <span className="muted">· page {t.page}</span>
        </h3>
        <StatusBadge
          tone={changed ? "help" : hasHeaders ? "ready" : "help"}
          label={changed ? "Not saved yet" : hasHeaders ? "Has headings" : "No headings yet"}
        />
      </div>

      <div className="table-compare">
        <figure className="table-compare__item">
          <figcaption className="small muted">How it looks in the PDF</figcaption>
          <img src={urls.asset(t.image_url)} alt={`Picture of table ${number} from page ${t.page}`} loading="lazy" />
        </figure>
        <div className="table-compare__item">
          <p className="small muted" id={`${headingId}-preview`}>
            How screen readers will understand it (headings are shaded)
          </p>
          <TablePreview table={t} headerRow={headerRow} headerColumn={headerColumn} number={number} />
        </div>
      </div>

      <form
        className="stack"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          onSave();
        }}
      >
        <fieldset className="fieldset">
          <legend className="field__label">Which cells are headings?</legend>
          <div className="checkbox">
            <input id={rowId} type="checkbox" checked={headerRow} onChange={(e) => onChange({ headerRow: e.target.checked })} />
            <label htmlFor={rowId}>The first row has column headings</label>
          </div>
          <div className="checkbox">
            <input id={colId} type="checkbox" checked={headerColumn} onChange={(e) => onChange({ headerColumn: e.target.checked })} />
            <label htmlFor={colId}>The first column has row headings</label>
          </div>
        </fieldset>
        {draft.error && (
          <p id={errId} className="field__error" role="alert">
            <Icon name="alertCircle" size={16} /> {draft.error}
          </p>
        )}
        <div className="inline-row">
          <button type="submit" className="btn btn--primary" aria-disabled={draft.saving || undefined}>
            {draft.saving ? "Saving…" : "Save"}
            <span className="visually-hidden"> table {number}</span>
          </button>
        </div>
      </form>
    </li>
  );
}

/** An HTML rendering of the table as it will be tagged, with header cells as <th>. */
function TablePreview({ table, headerRow, headerColumn, number }: { table: Table; headerRow: boolean; headerColumn: boolean; number: number }) {
  const rows = table.rows;
  if (!rows.length) return <p className="muted">Readpath couldn't read the cells of this table.</p>;
  return (
    <div className="table-scroll" role="region" aria-label={`Preview of table ${number}`} tabIndex={0}>
      <table className="preview-table">
        <caption className="visually-hidden">Preview of table {number}</caption>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {row.map((cell, c) => {
                // Checkbox choices override what the server sent for the first row / column.
                const header = r === 0 ? headerRow || (headerColumn && c === 0) : c === 0 ? headerColumn : cell.header;
                const Tag = header ? "th" : "td";
                const scope = header ? (r === 0 && headerRow ? "col" : "row") : undefined;
                return (
                  <Tag key={c} scope={scope} rowSpan={cell.row_span > 1 ? cell.row_span : undefined} colSpan={cell.col_span > 1 ? cell.col_span : undefined}>
                    {cell.text}
                  </Tag>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
