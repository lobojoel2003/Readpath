import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api, errorMessage, urls } from "../api/client";
import type { DocumentSummary, Scan } from "../api/types";
import { useAnnounce } from "../components/Announcer";
import { Spinner, StatusBadge } from "../components/Badges";
import { DocumentsTable } from "../components/DocumentsTable";
import { Icon, type IconName } from "../components/Icon";
import { LoadError, Notice } from "../components/Notice";
import { Breadcrumbs, ExternalLink, PageTitle } from "../components/PageParts";
import {
  formatDateTime,
  isDocActive,
  isScanActive,
  plural,
  prettyUrl,
  roundScore,
  SCAN_STATUS_LABEL,
  scanTone,
} from "../lib/format";
import { useDocumentTitle } from "../lib/hooks";

const POLL_MS = 2000;
const RETRY_MS = 5000;
/** Progress text is read out at most this often while a scan runs. */
const ANNOUNCE_EVERY_MS = 20000;

function progressOf(scan: Scan): { value?: number; max?: number; text: string } | null {
  if (!isScanActive(scan.status)) return null;
  const s = scan.summary;
  if (scan.status === "crawling") {
    if (scan.max_pages) {
      return {
        value: Math.min(scan.pages_crawled, scan.max_pages),
        max: scan.max_pages,
        text: `Visited ${scan.pages_crawled.toLocaleString()} of up to ${scan.max_pages.toLocaleString()} pages`,
      };
    }
    return { text: `Visited ${plural(scan.pages_crawled, "page")}` };
  }
  if ((scan.status === "checking" || scan.status === "fixing") && s.pdfs > 0) {
    return {
      value: Math.min(s.checked, s.pdfs),
      max: s.pdfs,
      text: `Checked ${s.checked.toLocaleString()} of ${plural(s.pdfs, "PDF")}`,
    };
  }
  return { text: "Getting started" };
}

export function ScanPage() {
  const { id = "" } = useParams();
  const announce = useAnnounce();
  const [scan, setScan] = useState<Scan>();
  const [docs, setDocs] = useState<DocumentSummary[]>();
  const [error, setError] = useState<ApiError | Error>();
  const [pollKey, setPollKey] = useState(0);
  const [acting, setActing] = useState<"cancel" | "autofix" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const lastStatus = useRef<string | null>(null);
  const statusRef = useRef<HTMLElement>(null);
  const lastAnnounce = useRef(0);

  useDocumentTitle(scan ? `${scan.name} – scan results` : "Scan results");

  // Poll the scan and its documents every 2 s until everything has settled.
  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    const ctrl = new AbortController();

    async function load() {
      try {
        const [s, d] = await Promise.all([api.getScan(id, ctrl.signal), api.getScanDocuments(id, ctrl.signal)]);
        if (stopped) return;
        setScan(s);
        setDocs(d);
        setError(undefined);
        if (isScanActive(s.status) || d.some((doc) => isDocActive(doc.status))) {
          timer = window.setTimeout(load, POLL_MS);
        }
      } catch (err) {
        if (stopped) return;
        setError(err instanceof Error ? err : new Error(String(err)));
        // A dropped connection is retried; a missing scan is not.
        if (!(err instanceof ApiError && err.isNotFound)) timer = window.setTimeout(load, RETRY_MS);
      }
    }

    void load();
    return () => {
      stopped = true;
      ctrl.abort();
      window.clearTimeout(timer);
    };
  }, [id, pollKey]);

  // Read out status changes right away, and progress now and then.
  useEffect(() => {
    if (!scan) return;
    const progress = progressOf(scan);
    const now = Date.now();
    if (lastStatus.current !== scan.status) {
      const first = lastStatus.current === null;
      lastStatus.current = scan.status;
      lastAnnounce.current = now;
      if (first && !isScanActive(scan.status)) return; // nothing new to say on page load
      if (scan.status === "done") {
        announce(`Scan finished. ${plural(scan.summary.pdfs, "PDF")} found and ${scan.summary.checked.toLocaleString()} checked.`);
      } else {
        announce(`${SCAN_STATUS_LABEL[scan.status]}. ${scan.message ?? progress?.text ?? ""}`);
      }
    } else if (progress && now - lastAnnounce.current > ANNOUNCE_EVERY_MS) {
      lastAnnounce.current = now;
      announce(`${progress.text}.`);
    }
  }, [scan, announce]);

  const restartPolling = useCallback(() => setPollKey((k) => k + 1), []);

  async function cancel() {
    if (acting) return;
    setActing("cancel");
    setActionError(null);
    try {
      setScan(await api.cancelScan(id));
      restartPolling();
      statusRef.current?.focus(); // the Cancel button goes away
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setActing(null);
    }
  }

  async function autofixAll() {
    if (acting) return;
    setActing("autofix");
    setActionError(null);
    try {
      const updated = await api.autofixScan(id);
      setScan(updated);
      announce("Fixing started. This page updates as each PDF is finished.");
      restartPolling();
      statusRef.current?.focus(); // the Fix all panel goes away while fixing
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setActing(null);
    }
  }

  const onDocChanged = useCallback(
    (doc: DocumentSummary) => {
      setDocs((list) => list?.map((d) => (d.id === doc.id ? doc : d)));
      restartPolling(); // refresh the summary numbers
    },
    [restartPolling],
  );

  if (error instanceof ApiError && error.isNotFound && !scan) {
    return (
      <div className="narrow stack">
        <PageTitle>We can't find this scan</PageTitle>
        <p className="lead">It may have been deleted, or the link may be wrong.</p>
        <p>
          <Link to="/" className="btn btn--primary">
            Go to the home page
          </Link>
        </p>
      </div>
    );
  }

  if (!scan || !docs) {
    return (
      <div className="stack">
        <Breadcrumbs items={[{ label: "Home", to: "/" }, { label: "Scan results" }]} />
        <PageTitle>Scan results</PageTitle>
        {error ? <LoadError error={error} onRetry={restartPolling} /> : <Spinner label="Loading scan…" />}
      </div>
    );
  }

  const active = isScanActive(scan.status);
  const progress = progressOf(scan);
  const s = scan.summary;
  const avg = roundScore(s.average_score);

  return (
    <div className="stack-lg">
      <div className="stack">
        <Breadcrumbs items={[{ label: "Home", to: "/" }, { label: scan.name }]} />
        <PageTitle eyebrow={scan.kind === "upload" ? "Uploaded files" : "Website scan"}>{scan.name}</PageTitle>
        <p className="page-meta">
          {scan.start_url && (
            <>
              <ExternalLink href={scan.start_url}>{prettyUrl(scan.start_url)}</ExternalLink>
              <span aria-hidden="true"> · </span>
            </>
          )}
          Started {formatDateTime(scan.created_at)}
          {scan.finished_at && <> · Finished {formatDateTime(scan.finished_at)}</>}
        </p>
      </div>

      {error && (
        <Notice tone="warning" role="none" title="Lost touch with the server.">
          <p>{error.message} Readpath will keep trying.</p>
        </Notice>
      )}

      <section
        ref={statusRef}
        tabIndex={-1}
        className={`card status-panel status-panel--${scanTone(scan.status)}`}
        aria-labelledby="status-heading"
      >
        <div className="status-panel__main">
          <h2 id="status-heading" className="visually-hidden">
            Scan status
          </h2>
          <div className="status-panel__line">
            {active ? <span className="spinner" aria-hidden="true" /> : null}
            <StatusBadge tone={scanTone(scan.status)} label={SCAN_STATUS_LABEL[scan.status]} />
            {scan.message && scan.status !== "failed" && <span className="status-panel__message">{scan.message}</span>}
          </div>
          {progress && (
            <div className="progress-block">
              <progress
                className="progress"
                value={progress.value}
                max={progress.max}
                aria-label={progress.text}
              />
              {progress.text !== scan.message && <span className="small muted">{progress.text}</span>}
            </div>
          )}
          {scan.status === "failed" && (
            <Notice tone="error" title="The scan stopped before it finished.">
              <p>{scan.message ?? "Something went wrong while scanning."}</p>
            </Notice>
          )}
          {scan.status === "cancelled" && <p className="muted">You stopped this scan. Files found so far are listed below.</p>}
        </div>
        {active && (
          <button type="button" className="btn btn--secondary" onClick={() => void cancel()} aria-disabled={acting === "cancel" || undefined}>
            <Icon name="stop" size={18} /> {acting === "cancel" ? "Cancelling…" : "Cancel scan"}
          </button>
        )}
      </section>

      <section aria-labelledby="summary-heading" className="stack">
        <h2 id="summary-heading">Summary</h2>
        <dl className="stat-grid">
          <Stat label="Average score" icon="shield" tone="accent" value={avg == null ? "—" : `${avg}`} suffix={avg == null ? undefined : "/ 100"} note={`${plural(s.checked, "PDF")} checked`} />
          <Stat label="Ready" icon="checkCircle" tone="ready" value={s.ready} note="Pass every automatic check" />
          <Stat label="Can fix automatically" icon="wrench" tone="auto" value={s.auto_fixable} note="Readpath can fix these" />
          <Stat label="Needs your help" icon="hand" tone="help" value={s.needs_help} note="A person needs to look" />
          <Stat label="Errors" icon="alertCircle" tone="error" value={s.errors} note="Couldn't download or open" />
          <Stat label="Office files found" icon="file" tone="neutral" value={s.other_documents} note="Word, Excel, PowerPoint (not checked yet)" />
        </dl>
      </section>

      {actionError && (
        <Notice tone="error" title="That didn't work.">
          <p>{actionError}</p>
        </Notice>
      )}

      {s.auto_fixable > 0 && scan.status !== "fixing" && (
        <section className="card card--accent fix-all" aria-labelledby="fixall-heading">
          <div className="fix-all__text">
            <h2 id="fixall-heading">
              Readpath can fix {plural(s.auto_fixable, "PDF")} for you
            </h2>
            <p>
              It adds a title and language, adds hidden structure that screen readers use (called "tags"), and
              marks page decorations so they are skipped.
            </p>
            <p className="small">
              <Icon name="shield" size={16} className="icon--inline" /> Your original files are kept. You can
              download them or undo the changes at any time.
            </p>
          </div>
          <div className="fix-all__actions">
            <button
              type="button"
              className="btn btn--primary btn--lg"
              onClick={() => {
                if (!active) void autofixAll();
              }}
              aria-disabled={acting === "autofix" || active || undefined}
            >
              <Icon name="wrench" size={20} /> {acting === "autofix" ? "Starting…" : "Fix all automatically"}
            </button>
            {active && <p className="small muted">Available when the scan finishes.</p>}
          </div>
        </section>
      )}

      <section aria-labelledby="docs-heading" className="stack">
        <div className="section-head">
          <h2 id="docs-heading">Files found</h2>
          {docs.length > 0 && (
            <a href={urls.reportCsv(scan.id)} className="btn btn--secondary btn--sm" download>
              <Icon name="download" size={16} /> Download report (CSV)
            </a>
          )}
        </div>
        {docs.length === 0 ? (
          <div className="empty">
            <Icon name="file" size={28} />
            {active ? (
              <p>No files found yet. They will appear here as Readpath finds them.</p>
            ) : scan.kind === "upload" ? (
              <p>This upload has no files.</p>
            ) : (
              <p>
                Readpath didn't find any PDFs on this site. Check that the address is right, or try again with more
                pages under "More options".
              </p>
            )}
          </div>
        ) : (
          <DocumentsTable documents={docs} onChanged={onDocChanged} />
        )}
      </section>
    </div>
  );
}

interface StatProps {
  label: string;
  value: number | string;
  suffix?: string;
  note: string;
  icon: IconName;
  tone: "accent" | "ready" | "auto" | "help" | "error" | "neutral";
}

function Stat({ label, value, suffix, note, icon, tone }: StatProps) {
  return (
    <div className={`stat stat--${tone}`}>
      <dt className="stat__label">
        <Icon name={icon} size={18} /> {label}
      </dt>
      <dd className="stat__value">
        {typeof value === "number" ? value.toLocaleString() : value}
        {suffix && <span className="stat__suffix"> {suffix}</span>}
      </dd>
      <dd className="stat__note">{note}</dd>
    </div>
  );
}
