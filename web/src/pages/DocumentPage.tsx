import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api, errorMessage, urls } from "../api/client";
import type { Report } from "../api/types";
import { useAnnounce } from "../components/Announcer";
import { GradeBadge, ScoreBadge, Spinner, StatusBadge } from "../components/Badges";
import { CheckList, groupChecks } from "../components/CheckList";
import { ConfirmButton } from "../components/ConfirmButton";
import { Icon } from "../components/Icon";
import { LoadError, Notice } from "../components/Notice";
import { Breadcrumbs, ExternalLink, PageTitle } from "../components/PageParts";
import { VeraPdfPanel } from "../components/VeraPdfPanel";
import { docStatusDisplay, formatBytes, formatDateTime, isDocActive, OFFICE_KIND_LABEL, plural, prettyUrl, roundScore } from "../lib/format";
import { useDocumentTitle, useResource } from "../lib/hooks";

type Action = "autofix" | "recheck" | "revert";

const BUSY_TEXT: Record<Action, string> = {
  autofix: "Fixing this PDF. This can take a few seconds…",
  recheck: "Checking this PDF again…",
  revert: "Undoing Readpath's changes…",
};

const GRADE_TEXT = {
  ready: "This PDF passes every automatic check.",
  auto_fixable: "Readpath can fix some of the problems by itself.",
  needs_help: "Some problems need a person. The guided review walks you through them.",
} as const;

export function DocumentPage() {
  const { id = "" } = useParams();
  const announce = useAnnounce();
  const report = useResource((signal) => api.getReport(id, signal), [id]);
  const [action, setAction] = useState<Action | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [result, setResult] = useState<{ kind: Action; fixes: string[] } | null>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const r = report.data;
  const doc = r?.document;

  const scanName = useResource(
    (signal) => (doc ? api.getScan(doc.scan_id, signal).then((s) => s.name) : Promise.resolve(null)),
    [doc?.scan_id],
  );

  useDocumentTitle(doc ? `${doc.title || doc.filename} – report` : "Document report");

  // While the server is still working on this file, check back every 2 s.
  const working = doc ? isDocActive(doc.status) : false;
  useEffect(() => {
    if (!working) return;
    const t = window.setTimeout(report.reload, 2000);
    return () => window.clearTimeout(t);
  }, [working, report.data, report.reload]);

  // The clicked button may disappear (e.g. "Fix automatically"), so move focus to the outcome.
  useEffect(() => {
    if (result) resultRef.current?.focus();
  }, [result]);
  useEffect(() => {
    if (actionError) errorRef.current?.focus();
  }, [actionError]);

  async function run(kind: Action) {
    if (action || working) return;
    setAction(kind);
    setActionError(null);
    setResult(null);
    announce(BUSY_TEXT[kind]);
    try {
      const fn = kind === "autofix" ? api.autofixDocument : kind === "recheck" ? api.recheckDocument : api.revertDocument;
      const next: Report = await fn(id);
      report.setData(next);
      setResult({ kind, fixes: kind === "autofix" ? next.fixes_applied : [] });
      const score = roundScore(next.document.score);
      announce(score != null ? `Done. The score is now ${score} out of 100.` : "Done.");
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setAction(null);
    }
  }

  if (!r || !doc) {
    const notFound = report.error instanceof ApiError && report.error.isNotFound;
    return (
      <div className="stack">
        <Breadcrumbs items={[{ label: "Home", to: "/" }, { label: "Document report" }]} />
        <PageTitle>{notFound ? "We can't find this document" : "Document report"}</PageTitle>
        {notFound ? (
          <p className="lead">It may have been deleted along with its scan.</p>
        ) : report.error ? (
          <LoadError error={report.error} onRetry={report.reload} />
        ) : (
          <Spinner label="Loading report…" />
        )}
      </div>
    );
  }

  const groups = groupChecks(r.checks);
  const isPdf = doc.kind === "pdf";
  const reviewPath = `/documents/${encodeURIComponent(doc.id)}/review/basics`;
  const canAutofix = isPdf && doc.counts.auto_fixable > 0;
  const status = docStatusDisplay(doc);
  const was = roundScore(doc.original_score);
  const busy = action !== null || working;

  return (
    <div className="stack-lg">
      <div className="stack">
        <Breadcrumbs
          items={[
            { label: "Home", to: "/" },
            { label: scanName.data ?? "Scan results", to: `/scans/${encodeURIComponent(doc.scan_id)}` },
            { label: doc.filename },
          ]}
        />
        <PageTitle eyebrow="Document report">{doc.title || doc.filename}</PageTitle>
        <ul className="meta-list">
          {doc.title && (
            <li>
              <span className="meta-list__label">File:</span> <span className="break">{doc.filename}</span>
            </li>
          )}
          {doc.page_count != null && <li>{plural(doc.page_count, "page")}</li>}
          {doc.size_bytes != null && <li>{formatBytes(doc.size_bytes)}</li>}
          {doc.url && (
            <li>
              <ExternalLink href={doc.url}>Open on the website</ExternalLink>
            </li>
          )}
          {doc.found_on && (
            <li>
              <span className="meta-list__label">Found on:</span>{" "}
              <ExternalLink href={doc.found_on} className="break">
                {prettyUrl(doc.found_on)}
              </ExternalLink>
            </li>
          )}
        </ul>
      </div>

      {!isPdf && (
        <Notice tone="info" title={`${OFFICE_KIND_LABEL[doc.kind] ?? "This file"} — not checked yet`}>
          <p>
            Readpath doesn't check Word, Excel or PowerPoint files yet. A good way to fix them: make the original file
            accessible, then save it as a tagged PDF and check that PDF here.
          </p>
        </Notice>
      )}

      {doc.status === "error" && (
        <Notice tone="error" title="Readpath couldn't open this file.">
          <p>{doc.error ?? "The file may be damaged, password-protected, or no longer on the website."}</p>
        </Notice>
      )}

      {isPdf && doc.status !== "error" && (
        <section className="card score-panel" aria-labelledby="score-heading">
          <h2 id="score-heading" className="visually-hidden">
            Score
          </h2>
          <div className="score-panel__number">
            <ScoreBadge score={doc.score} grade={doc.grade} size="lg" />
            <span className="score-panel__outof" aria-hidden="true">
              out of 100
            </span>
          </div>
          <div className="score-panel__text stack-sm">
            {doc.grade ? <GradeBadge grade={doc.grade} /> : <StatusBadge tone={status.tone} label={status.label} />}
            {doc.grade && <p className="score-panel__grade-text">{GRADE_TEXT[doc.grade]}</p>}
            {doc.fixed && was != null && (
              <p className="muted">Was {was} before Readpath fixed it.</p>
            )}
            <p className="small muted">
              {plural(doc.counts.needs_help, "problem")} need your help · {doc.counts.auto_fixable.toLocaleString()} can
              be fixed automatically · {plural(doc.counts.passed, "check")} passed
            </p>
            {r.checked_at && <p className="small muted">Checked {formatDateTime(r.checked_at)}</p>}
          </div>
        </section>
      )}

      {isPdf && doc.status !== "error" && (
        <section aria-labelledby="actions-heading" className="stack">
          <h2 id="actions-heading" className="visually-hidden">
            Actions
          </h2>
          <div className="action-bar">
            {canAutofix && (
              <button type="button" className="btn btn--primary" onClick={() => void run("autofix")} aria-disabled={busy || undefined}>
                <Icon name="wrench" size={18} /> Fix automatically
              </button>
            )}
            <Link to={reviewPath} className={canAutofix ? "btn btn--secondary" : "btn btn--primary"}>
              <Icon name="hand" size={18} /> Start guided review
            </Link>
            <a href={urls.download(doc.id, "current")} className="btn btn--secondary" download>
              <Icon name="download" size={18} /> {doc.fixed ? "Download accessible PDF" : "Download PDF"}
            </a>
            {doc.fixed && (
              <a href={urls.download(doc.id, "original")} className="btn btn--ghost" download>
                <Icon name="file" size={18} /> Download original
              </a>
            )}
            <button type="button" className="btn btn--ghost" onClick={() => void run("recheck")} aria-disabled={busy || undefined}>
              <Icon name="refresh" size={18} /> Run again
            </button>
            {doc.fixed && (
              <ConfirmButton
                className="btn btn--ghost"
                disabled={busy}
                title="Undo all of Readpath's changes?"
                body={
                  <p>
                    This puts the original file back, exactly as it was on your website. Fixes and anything you entered
                    in the guided review will be removed.
                  </p>
                }
                confirmLabel="Undo all changes"
                destructive
                onConfirm={() => void run("revert")}
              >
                <Icon name="undo" size={18} /> Undo all changes
              </ConfirmButton>
            )}
          </div>
          {canAutofix && !doc.fixed && (
            <p className="small muted">
              <Icon name="shield" size={14} className="icon--inline" /> Readpath keeps the original. You can download it
              or undo the changes at any time.
            </p>
          )}

          {(action || working) && (
            <div className="busy-panel">
              <Spinner label={action ? BUSY_TEXT[action] : "Readpath is still working on this file…"} />
            </div>
          )}
          {actionError && (
            <div ref={errorRef} tabIndex={-1} className="focus-target">
              <Notice tone="error" title="That didn't work.">
                <p>{actionError}</p>
              </Notice>
            </div>
          )}
          {result && (
            <div ref={resultRef} tabIndex={-1} className="focus-target" role="group" aria-labelledby="result-title">
              <Notice
                tone="success"
                role="none"
                title={<span id="result-title">{resultTitle(result.kind, result.fixes.length)}</span>}
              >
                {result.fixes.length > 0 && (
                  <ul className="fix-list">
                    {result.fixes.map((f, i) => (
                      <li key={i}>
                        <Icon name="check" size={16} /> {f}
                      </li>
                    ))}
                  </ul>
                )}
              </Notice>
            </div>
          )}
        </section>
      )}

      {isPdf && r.checks.length > 0 && (
        <>
          {groups.needsHelp.length > 0 && (
            <section aria-labelledby="needs-help-heading" className="stack">
              <h2 id="needs-help-heading" className="section-title section-title--help">
                <Icon name="hand" size={22} /> Needs your help <span className="count-pill">{groups.needsHelp.length}</span>
              </h2>
              <p className="muted">Readpath can't decide these by itself. Most take a minute or two in the guided review.</p>
              <CheckList checks={groups.needsHelp} docId={doc.id} />
            </section>
          )}

          {groups.autoFix.length > 0 && (
            <section aria-labelledby="auto-heading" className="stack">
              <h2 id="auto-heading" className="section-title section-title--auto">
                <Icon name="wrench" size={22} /> Readpath can fix these <span className="count-pill">{groups.autoFix.length}</span>
              </h2>
              <p className="muted">Choose "Fix automatically" above and Readpath takes care of them.</p>
              <CheckList checks={groups.autoFix} docId={doc.id} />
            </section>
          )}

          {groups.byHand.length > 0 && (
            <section aria-labelledby="hand-heading" className="stack">
              <h2 id="hand-heading" className="section-title">
                <Icon name="info" size={22} /> Check these yourself <span className="count-pill">{groups.byHand.length}</span>
              </h2>
              <p className="muted">Software can't fully check these. Take a quick look at the PDF.</p>
              <CheckList checks={groups.byHand} docId={doc.id} />
            </section>
          )}

          {groups.passed.length > 0 && (
            <section aria-labelledby="passed-heading" className="stack">
              <h2 id="passed-heading" className="section-title section-title--ready">
                <Icon name="checkCircle" size={22} /> Passed <span className="count-pill">{groups.passed.length}</span>
              </h2>
              <CheckList checks={groups.passed} docId={doc.id} compact />
            </section>
          )}

          {groups.notApplicable.length > 0 && (
            <section aria-labelledby="na-heading" className="stack">
              <h2 id="na-heading" className="section-title">
                Not applicable <span className="count-pill">{groups.notApplicable.length}</span>
              </h2>
              <details className="disclosure">
                <summary>Show {plural(groups.notApplicable.length, "check")} that don't apply to this PDF</summary>
                <div className="disclosure__body">
                  <CheckList checks={groups.notApplicable} docId={doc.id} compact />
                </div>
              </details>
            </section>
          )}
        </>
      )}

      {isPdf && r.verapdf && <VeraPdfPanel result={r.verapdf} />}
    </div>
  );
}

function resultTitle(kind: Action, fixes: number): string {
  if (kind === "revert") return "The original file is back. Readpath's changes were removed.";
  if (kind === "recheck") return "Checked again. The report below is up to date.";
  return fixes > 0 ? `Readpath fixed ${plural(fixes, "thing")}:` : "Readpath checked the file. There was nothing more it could fix by itself.";
}
