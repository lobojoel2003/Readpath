import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { DocumentSummary } from "../api/types";
import { docCategory, docStatusDisplay, plural, prettyUrl, type DocCategory } from "../lib/format";
import { useAnnounce } from "./Announcer";
import { ScoreBadge, StatusBadge } from "./Badges";
import { Icon } from "./Icon";
import { ExternalLink, Pagination } from "./PageParts";

const PAGE_SIZE = 50;

type Filter = "all" | "needs_help" | "auto_fixable" | "ready" | "error" | "office";
type Sort = "score-asc" | "score-desc" | "name";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "needs_help", label: "Needs your help" },
  { id: "auto_fixable", label: "Can fix automatically" },
  { id: "ready", label: "Ready" },
  { id: "error", label: "Errors" },
  { id: "office", label: "Office files" },
];

function matches(filter: Filter, cat: DocCategory): boolean {
  return filter === "all" || filter === cat;
}

function compare(sort: Sort) {
  const byName = (a: DocumentSummary, b: DocumentSummary) =>
    a.filename.localeCompare(b.filename, undefined, { numeric: true, sensitivity: "base" });
  return (a: DocumentSummary, b: DocumentSummary) => {
    if (sort === "name") return byName(a, b);
    // Unscored files (errors, Office files, still checking) always go last.
    if (a.score == null && b.score == null) return byName(a, b);
    if (a.score == null) return 1;
    if (b.score == null) return -1;
    const d = sort === "score-asc" ? a.score - b.score : b.score - a.score;
    return d || byName(a, b);
  };
}

interface Props {
  documents: DocumentSummary[];
  /** Called after a per-row fix so the page can refresh the scan summary. */
  onChanged: (doc: DocumentSummary) => void;
}

export function DocumentsTable({ documents, onChanged }: Props) {
  const announce = useAnnounce();
  const filterName = useId();
  const sortId = useId();
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("score-asc");
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [rowError, setRowError] = useState<string | null>(null);
  const statusRef = useRef<HTMLParagraphElement>(null);

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: documents.length, needs_help: 0, auto_fixable: 0, ready: 0, error: 0, office: 0 };
    for (const d of documents) {
      const cat = docCategory(d);
      if (cat in c) c[cat as Filter] += 1;
    }
    return c;
  }, [documents]);

  const rows = useMemo(
    () => documents.filter((d) => matches(filter, docCategory(d))).sort(compare(sort)),
    [documents, filter, sort],
  );

  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const current = Math.min(page, pageCount);
  const start = (current - 1) * PAGE_SIZE;
  const visible = rows.slice(start, start + PAGE_SIZE);

  useEffect(() => setPage(1), [filter, sort]);

  // Tell screen reader users what a filter or sort change did.
  const lastView = useRef(`${filter}|${sort}`);
  useEffect(() => {
    if (lastView.current === `${filter}|${sort}`) return;
    lastView.current = `${filter}|${sort}`;
    announce(rows.length === 0 ? "No files match this filter." : `${plural(rows.length, "file")} shown.`);
    // Only on filter/sort changes, not on every poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter, sort]);

  function goToPage(p: number) {
    setPage(p);
    // Bring keyboard and screen reader users back to the top of the results.
    requestAnimationFrame(() => statusRef.current?.focus());
  }

  async function fix(doc: DocumentSummary) {
    if (busy[doc.id]) return;
    setBusy((b) => ({ ...b, [doc.id]: true }));
    setRowError(null);
    announce(`Fixing ${doc.filename}. This can take a few seconds.`);
    try {
      const report = await api.autofixDocument(doc.id);
      const s = report.document.score;
      announce(`Fixed ${doc.filename}.${s != null ? ` New score: ${Math.round(s)} out of 100.` : ""}`);
      onChanged(report.document);
      // The "Fix" button is replaced by "Review" or "View": keep focus in the row,
      // or on the results line if the row no longer matches the filter.
      requestAnimationFrame(() => {
        const row = document.querySelector(`[data-doc-row="${CSS.escape(doc.id)}"]`);
        const target = row?.querySelector<HTMLElement>(".actions-cell a, .actions-cell button, a");
        (target ?? statusRef.current)?.focus();
      });
    } catch (err) {
      setRowError(`Couldn't fix ${doc.filename}. ${errorMessage(err)}`);
    } finally {
      setBusy((b) => ({ ...b, [doc.id]: false }));
    }
  }

  const statusText =
    rows.length === 0
      ? "No files match this filter."
      : rows.length <= PAGE_SIZE
        ? `Showing ${plural(rows.length, "file")}.`
        : `Showing ${start + 1}–${start + visible.length} of ${rows.length.toLocaleString()} files.`;

  return (
    <div className="stack">
      <div className="toolbar">
        <fieldset className="segmented">
          <legend className="segmented__legend">Show</legend>
          <div className="segmented__options">
            {FILTERS.map((f) => (
              <label key={f.id} className={`segmented__option${filter === f.id ? " is-checked" : ""}`}>
                <input
                  type="radio"
                  name={filterName}
                  value={f.id}
                  checked={filter === f.id}
                  onChange={() => setFilter(f.id)}
                />
                <span>
                  {f.label} <span className="segmented__count">({counts[f.id].toLocaleString()})</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="field field--inline">
          <label htmlFor={sortId} className="field__label">
            Sort by
          </label>
          <select id={sortId} className="select" value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
            <option value="score-asc">Lowest score first</option>
            <option value="score-desc">Highest score first</option>
            <option value="name">File name (A–Z)</option>
          </select>
        </div>
      </div>

      {rowError && (
        <p className="field__error" role="alert">
          <Icon name="alertCircle" size={16} /> {rowError}
        </p>
      )}

      <p ref={statusRef} tabIndex={-1} className="results-status">
        {statusText}
      </p>

      {visible.length > 0 && (
        <div className="table-scroll" role="region" aria-label="Table of files found" tabIndex={0}>
          <table className="data-table docs-table">
            <caption className="visually-hidden">
              Files found, {FILTERS.find((f) => f.id === filter)?.label.toLowerCase()}, sorted by{" "}
              {sort === "name" ? "file name" : sort === "score-asc" ? "lowest score first" : "highest score first"}
            </caption>
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col" className="num">
                  Pages
                </th>
                <th scope="col">Score</th>
                <th scope="col">Status</th>
                <th scope="col">
                  <span className="visually-hidden">Action</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map((doc) => (
                <DocumentRow key={doc.id} doc={doc} busy={!!busy[doc.id]} onFix={() => void fix(doc)} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Pagination page={current} pageCount={pageCount} onChange={goToPage} label="Pages of files" />
    </div>
  );
}

function DocumentRow({ doc, busy, onFix }: { doc: DocumentSummary; busy: boolean; onFix: () => void }) {
  const cat = docCategory(doc);
  const status = docStatusDisplay(doc);
  const docPath = `/documents/${encodeURIComponent(doc.id)}`;
  const checkable = cat !== "office" && cat !== "error";

  return (
    <tr data-doc-row={doc.id}>
      <th scope="row" className="cell-main">
        {checkable ? (
          <Link to={docPath} className="strong-link break">
            {doc.filename}
          </Link>
        ) : doc.url ? (
          <ExternalLink href={doc.url} className="strong-link break">
            {doc.filename}
          </ExternalLink>
        ) : (
          <span className="break">{doc.filename}</span>
        )}
        {doc.title && doc.title !== doc.filename && <span className="cell-sub">{doc.title}</span>}
        {doc.found_on && (
          <span className="cell-sub">
            Found on{" "}
            <ExternalLink href={doc.found_on} className="break">
              {prettyUrl(doc.found_on)}
            </ExternalLink>
          </span>
        )}
        {cat === "error" && doc.error && <span className="cell-sub cell-sub--error">{doc.error}</span>}
      </th>
      <td className="num">
        {doc.page_count ?? (
          <>
            <span aria-hidden="true">—</span>
            <span className="visually-hidden">Unknown</span>
          </>
        )}
      </td>
      <td>
        <ScoreBadge score={doc.score} originalScore={doc.original_score} fixed={doc.fixed} grade={doc.grade} />
      </td>
      <td>
        <StatusBadge tone={status.tone} label={status.label} />
        {doc.fixed && <span className="cell-sub">Fixed by Readpath</span>}
      </td>
      <td className="actions-cell">
        {cat === "needs_help" && (
          <Link
            to={`${docPath}/review/basics`}
            className="btn btn--secondary btn--sm"
            aria-label={`Review ${doc.filename}`}
          >
            Review
          </Link>
        )}
        {cat === "auto_fixable" && (
          <button
            type="button"
            className="btn btn--primary btn--sm"
            onClick={onFix}
            aria-disabled={busy || undefined}
            aria-label={busy ? `Fixing ${doc.filename}` : `Fix ${doc.filename} automatically`}
          >
            {busy ? (
              <>
                <span className="spinner spinner--on-accent" aria-hidden="true" /> Fixing…
              </>
            ) : (
              <>
                <Icon name="wrench" size={16} /> Fix
              </>
            )}
          </button>
        )}
        {cat === "ready" && (
          <Link to={docPath} className="btn btn--ghost btn--sm" aria-label={`View ${doc.filename}`}>
            View
          </Link>
        )}
      </td>
    </tr>
  );
}
