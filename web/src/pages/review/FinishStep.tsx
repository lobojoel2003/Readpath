import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage, isConflict, urls } from "../../api/client";
import type { Report } from "../../api/types";
import { useAnnounce } from "../../components/Announcer";
import { GradeBadge, ScoreBadge, Spinner } from "../../components/Badges";
import { CheckList, isProblem } from "../../components/CheckList";
import { Icon } from "../../components/Icon";
import { Notice } from "../../components/Notice";
import { roundScore } from "../../lib/format";
import type { StepProps } from "./stepTypes";

const HUMAN_CHECKS = [
  { id: "contrast", label: "Colors have enough contrast", hint: "Text is easy to read against its background, including in charts." },
  { id: "links", label: "Link text makes sense on its own", hint: 'For example "Apply for a permit" instead of "click here".' },
  { id: "descriptions", label: "Descriptions are accurate", hint: "Image descriptions match what the images show." },
  { id: "forms", label: "Forms have clear labels", hint: "If people fill in this PDF, each field says what to enter." },
];

export function FinishStep({ docId, revision, setRevision, onConflict }: StepProps) {
  const announce = useAnnounce();
  const listId = useId();
  const [report, setReport] = useState<Report | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ticked, setTicked] = useState<Record<string, boolean>>({});
  const started = useRef(false);
  const revisionRef = useRef(revision);
  revisionRef.current = revision;

  async function run() {
    if (running) return;
    setRunning(true);
    setError(null);
    announce("Checking your PDF one last time…");
    try {
      const r = await api.finish(docId, revisionRef.current);
      setReport(r);
      setRevision(r.document.revision);
      const s = roundScore(r.document.score);
      announce(s != null ? `Check finished. The score is ${s} out of 100.` : "Check finished.");
    } catch (err) {
      if (isConflict(err)) onConflict();
      else setError(errorMessage(err));
    } finally {
      setRunning(false);
    }
  }

  // Run the final check once when the step opens (the ref survives StrictMode's re-mount).
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!report) {
    return (
      <div className="step stack">
        {error ? (
          <Notice
            tone="error"
            title="The final check didn't finish."
            action={
              <button type="button" className="btn btn--secondary" onClick={() => void run()}>
                <Icon name="refresh" size={18} /> Try again
              </button>
            }
          >
            <p>{error}</p>
          </Notice>
        ) : (
          <div className="card busy-panel">
            <Spinner label="Checking your PDF one last time…" />
          </div>
        )}
      </div>
    );
  }

  const doc = report.document;
  const remaining = report.checks.filter(isProblem);
  const was = roundScore(doc.original_score);

  return (
    <div className="step stack-lg">
      <section className="card score-panel score-panel--finish" aria-labelledby="final-heading">
        <div className="score-panel__number">
          <ScoreBadge score={doc.score} grade={doc.grade} size="lg" />
          <span className="score-panel__outof" aria-hidden="true">
            out of 100
          </span>
        </div>
        <div className="score-panel__text stack-sm">
          <h2 id="final-heading">{remaining.length === 0 ? "Nice work. This PDF passes every automatic check." : "Almost there"}</h2>
          {doc.grade && <GradeBadge grade={doc.grade} />}
          {doc.fixed && was != null && was !== roundScore(doc.score) && <p className="muted">It started at {was}.</p>}
          <div className="action-bar">
            <a href={urls.download(doc.id, "current")} className="btn btn--primary btn--xl" download>
              <Icon name="download" size={22} /> Download accessible PDF
            </a>
          </div>
          <p className="small muted">Replace the old file on your website with this one.</p>
        </div>
      </section>

      {remaining.length > 0 && (
        <section aria-labelledby="remaining-heading" className="stack">
          <h2 id="remaining-heading">Problems that are left</h2>
          <p className="muted">
            Some of these may need the original file (for example, the Word document) to fix. You can still post this
            improved version now.
          </p>
          <CheckList checks={remaining} docId={docId} compact />
        </section>
      )}

      <section aria-labelledby="human-heading" className="card stack">
        <h2 id="human-heading">Things only a person can check</h2>
        <p className="muted">Software can't fully check these. Take a quick look at the PDF and tick each one.</p>
        <ul className="checklist" id={listId}>
          {HUMAN_CHECKS.map((c) => (
            <li key={c.id} className="checkbox">
              <input
                id={`${listId}-${c.id}`}
                type="checkbox"
                checked={!!ticked[c.id]}
                aria-describedby={`${listId}-${c.id}-hint`}
                onChange={(e) => setTicked((t) => ({ ...t, [c.id]: e.target.checked }))}
              />
              <div>
                <label htmlFor={`${listId}-${c.id}`}>{c.label}</label>
                <p id={`${listId}-${c.id}-hint`} className="field__hint">
                  {c.hint}
                </p>
              </div>
            </li>
          ))}
        </ul>
        <p className="small muted">
          {Object.values(ticked).filter(Boolean).length} of {HUMAN_CHECKS.length} checked. These ticks are just for you;
          they aren't saved.
        </p>
      </section>

      <div className="inline-row">
        <Link to={`/documents/${encodeURIComponent(docId)}`} className="btn btn--secondary">
          See the full report
        </Link>
        <Link to={`/scans/${encodeURIComponent(doc.scan_id)}`} className="btn btn--ghost">
          Back to all files in this scan
        </Link>
        <button type="button" className="btn btn--ghost" onClick={() => void run()} aria-disabled={running || undefined}>
          <Icon name="refresh" size={18} /> {running ? "Checking…" : "Check again"}
        </button>
      </div>
    </div>
  );
}
