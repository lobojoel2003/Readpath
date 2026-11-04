import { useEffect, useId, useRef, useState } from "react";
import { ApiError, api, errorMessage, isAbort, isConflict, urls } from "../../api/client";
import type { Figure } from "../../api/types";
import { useAnnounce } from "../../components/Announcer";
import { Spinner, StatusBadge } from "../../components/Badges";
import { Icon } from "../../components/Icon";
import { LoadError, Notice } from "../../components/Notice";
import type { StepProps } from "./stepTypes";

const SOFT_LIMIT = 250;

/**
 * Figure ids change after every save, so cards are keyed by where the image
 * sits in the document (page + box), which stays the same.
 */
function figureKey(f: Figure, i: number): string {
  return f.bbox ? `${f.page}:${f.bbox.map((n) => n.toFixed(4)).join(",")}` : `${f.page}:#${i}`;
}

interface Draft {
  text?: string;
  decorative?: boolean;
  suggested?: boolean;
  saving?: boolean;
  suggesting?: boolean;
  error?: string;
  justSaved?: boolean;
}

export function ImagesStep({ docId, overview, setRevision, setOverview, onConflict, goNext }: StepProps) {
  const announce = useAnnounce();
  const [figures, setFigures] = useState<Figure[] | null>(null);
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [loadNonce, setLoadNonce] = useState(0);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  // The API's Figure has no "decorative" field, so remember the choices made here.
  const [decorative, setDecorative] = useState<Set<string>>(() => new Set());
  const [completing, setCompleting] = useState(false);
  const [completeError, setCompleteError] = useState<string | null>(null);
  // Decorative images leave the tags, so the server stops listing them.
  const [skipped, setSkipped] = useState(0);
  const skippedRef = useRef<HTMLDivElement>(null);
  const revisionRef = useRef(overview.revision);
  revisionRef.current = overview.revision;

  useEffect(() => {
    const ctrl = new AbortController();
    setLoadError(null);
    api.getFigures(docId, ctrl.signal).then(
      (res) => {
        setFigures(res.figures);
        setRevision(res.revision);
      },
      (err: unknown) => {
        if (!isAbort(err)) setLoadError(err instanceof Error ? err : new Error(String(err)));
      },
    );
    return () => ctrl.abort();
  }, [docId, loadNonce, setRevision]);

  const patch = (key: string, d: Partial<Draft>) => setDrafts((all) => ({ ...all, [key]: { ...all[key], ...d } }));

  const isDone = (f: Figure, key: string) => Boolean(f.alt && f.alt.trim()) || decorative.has(key);

  async function save(f: Figure, key: string) {
    const d = drafts[key] ?? {};
    if (d.saving) return;
    const isDecorative = d.decorative ?? decorative.has(key);
    const text = (d.text ?? f.alt ?? "").trim();
    if (!isDecorative && !text) {
      patch(key, { error: "Write a short description, or mark the image as decorative." });
      return;
    }
    patch(key, { saving: true, error: undefined });
    try {
      const res = await api.putFigure(
        docId,
        f.id,
        isDecorative ? { revision: revisionRef.current, decorative: true } : { revision: revisionRef.current, alt: text, decorative: false },
      );
      revisionRef.current = res.revision;
      setRevision(res.revision);
      setFigures(res.figures);
      setDecorative((s) => {
        const next = new Set(s);
        if (isDecorative) next.add(key);
        else next.delete(key);
        return next;
      });
      setDrafts((all) => ({ ...all, [key]: { justSaved: true } }));
      const removed = isDecorative && !res.figures.some((x, i) => figureKey(x, i) === key);
      if (removed) {
        setSkipped((n) => n + 1);
        // The card is gone; keep keyboard focus on the explanation instead of losing it
        window.setTimeout(() => skippedRef.current?.focus(), 0);
      }
      announce(isDecorative ? "Saved. Screen readers will skip this image." : "Description saved.");
    } catch (err) {
      if (isConflict(err)) onConflict();
      else patch(key, { saving: false, error: errorMessage(err) });
    }
  }

  async function suggest(f: Figure, key: string, focusTarget: () => void) {
    if (drafts[key]?.suggesting) return;
    patch(key, { suggesting: true, error: undefined });
    announce("Asking for a draft description…");
    try {
      const res = await api.suggestAlt(docId, f.id);
      patch(key, { suggesting: false, text: res.alt, suggested: true, justSaved: false });
      announce("Draft description added. Check it, change anything that's wrong, then save.");
      focusTarget();
    } catch (err) {
      const msg =
        err instanceof ApiError && err.status === 503
          ? "Description drafts aren't available on this server right now."
          : errorMessage(err);
      patch(key, { suggesting: false, error: msg });
    }
  }

  async function complete() {
    if (completing) return;
    setCompleting(true);
    setCompleteError(null);
    try {
      const ov = await api.completeStep(docId, "images", revisionRef.current);
      setOverview(ov);
      announce("Images step finished.");
      goNext();
    } catch (err) {
      if (isConflict(err)) onConflict();
      else setCompleteError(errorMessage(err));
    } finally {
      setCompleting(false);
    }
  }

  const stepState = overview.steps.find((s) => s.id === "images");

  if (loadError) return <LoadError error={loadError} onRetry={() => setLoadNonce((n) => n + 1)} />;
  if (!figures) return <Spinner label="Loading images…" />;

  const keys = figures.map(figureKey);
  const done = figures.filter((f, i) => isDone(f, keys[i])).length;
  const total = figures.length;
  const unsaved = figures.some((f, i) => {
    const d = drafts[keys[i]];
    return d && !d.justSaved && ((d.text != null && d.text.trim() !== (f.alt ?? "").trim()) || d.decorative != null);
  });

  return (
    <div className="step stack-lg">
      <div className="step__intro stack-sm">
        <h2>Describe the images</h2>
        <p>
          People who can't see an image hear its description instead. Describe what matters about it in a sentence or
          two. If an image is only decoration, mark it so screen readers skip it.
        </p>
        <p className="refs">WCAG 1.1.1 · PDF/UA 7.3</p>
      </div>

      {skipped > 0 && (
        <div ref={skippedRef} tabIndex={-1}>
          <Notice tone="success" title={`Marked ${skipped} ${skipped === 1 ? "image" : "images"} as decorative.`}>
            <p>Screen readers will skip {skipped === 1 ? "it" : "them"}, so {skipped === 1 ? "it no longer appears" : "they no longer appear"} in this list.</p>
          </Notice>
        </div>
      )}

      {total === 0 ? (
        <div className="card empty">
          <Icon name="checkCircle" size={28} />
          <p>{skipped > 0 ? "No images left to describe." : "No images found — nothing to do here."}</p>
        </div>
      ) : (
        <>
          <div className="progress-block">
            <p className="progress-block__label" id="images-progress-label">
              <strong>
                {done} of {total}
              </strong>{" "}
              {total === 1 ? "image" : "images"} described
            </p>
            <progress className="progress" value={done} max={total} aria-labelledby="images-progress-label" />
          </div>

          <ol className="figure-list" aria-label="Images in this PDF">
            {figures.map((f, i) => (
              <FigureCard
                key={keys[i]}
                figure={f}
                number={i + 1}
                total={total}
                draft={drafts[keys[i]] ?? {}}
                savedDecorative={decorative.has(keys[i])}
                visionAvailable={overview.vision_available}
                onChange={(d) => patch(keys[i], { ...d, justSaved: false, error: undefined })}
                onSave={() => void save(f, keys[i])}
                onSuggest={(focus) => void suggest(f, keys[i], focus)}
              />
            ))}
          </ol>
        </>
      )}

      <div className="card stack-sm step__finish">
        {total === 0 || done === total ? (
          <>
            <p>
              {total === 0 ? "This PDF has no images to describe." : "All images are described or marked as decoration."}
              {unsaved && " Some changes aren't saved yet."}
            </p>
            {completeError && (
              <Notice tone="error" title="That didn't work.">
                <p>{completeError}</p>
              </Notice>
            )}
            <div>
              {total === 0 ? (
                <button type="button" className="btn btn--primary btn--lg" onClick={goNext}>
                  Continue <Icon name="arrowRight" size={20} />
                </button>
              ) : (
                <button type="button" className="btn btn--primary btn--lg" onClick={() => void complete()} aria-disabled={completing || undefined}>
                  <Icon name="check" size={20} /> {completing ? "Saving…" : stepState?.status === "done" ? "Done with images" : "Finish this step"}
                </button>
              )}
            </div>
          </>
        ) : (
          <p>
            <Icon name="info" size={18} className="icon--inline" /> Save a description for each image, or mark it as
            decorative, to finish this step. {total - done} left.
          </p>
        )}
      </div>
    </div>
  );
}

interface FigureCardProps {
  figure: Figure;
  number: number;
  total: number;
  draft: Draft;
  savedDecorative: boolean;
  visionAvailable: boolean;
  onChange: (d: Partial<Draft>) => void;
  onSave: () => void;
  onSuggest: (focus: () => void) => void;
}

function FigureCard({ figure: f, number, total, draft, savedDecorative, visionAvailable, onChange, onSave, onSuggest }: FigureCardProps) {
  const ids = { text: useId(), hint: useId(), count: useId(), warn: useId(), err: useId(), deco: useId(), decoHint: useId(), draft: useId() };
  const textRef = useRef<HTMLTextAreaElement>(null);
  const text = draft.text ?? f.alt ?? "";
  const isDecorative = draft.decorative ?? savedDecorative;
  const changed = (draft.text != null && draft.text.trim() !== (f.alt ?? "").trim()) || (draft.decorative != null && draft.decorative !== savedDecorative);
  const saved = !changed && (savedDecorative || Boolean(f.alt && f.alt.trim()));
  const len = text.trim().length;
  const tooLong = len > SOFT_LIMIT;
  const startsWithImageOf = /^\s*(an?\s+)?(image|picture|photo|photograph|graphic)\s+of\b/i.test(text);
  const headingId = `figure-${number}-heading`;

  let status: { tone: "ready" | "help" | "neutral"; label: string };
  if (saved && savedDecorative) status = { tone: "neutral", label: "Saved as decorative" };
  else if (saved) status = { tone: "ready", label: "Saved" };
  else if (changed) status = { tone: "help", label: "Not saved yet" };
  else status = { tone: "help", label: "Needs a description" };

  const describedBy = [ids.hint, ids.count, startsWithImageOf && ids.warn, draft.error && ids.err, draft.suggested && ids.draft]
    .filter(Boolean)
    .join(" ");

  return (
    <li className="card figure-card" aria-labelledby={headingId}>
      <div className="figure-card__media">
        <img src={urls.asset(f.image_url)} alt={`Image ${number} of ${total}, from page ${f.page}`} loading="lazy" />
      </div>
      <form
        className="figure-card__form stack"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          onSave();
        }}
      >
        <div className="figure-card__head">
          <h3 id={headingId}>
            Image {number} <span className="muted">· page {f.page}</span>
          </h3>
          <StatusBadge tone={status.tone} label={status.label} icon={status.tone === "ready" ? "checkCircle" : undefined} />
        </div>

        {f.context && (
          <div className="context">
            <p className="context__label">Text near this image</p>
            <p className="context__text">{f.context}</p>
          </div>
        )}

        <div className="field">
          <label htmlFor={ids.text} className="field__label">
            Description
          </label>
          <p id={ids.hint} className="field__hint">
            Say what the image shows and why it's there, in 1–2 sentences. Don't start with "Image of" — screen
            readers already say it's an image.
          </p>
          <textarea
            ref={textRef}
            id={ids.text}
            className="textarea"
            rows={3}
            value={isDecorative ? "" : text}
            disabled={isDecorative || draft.saving}
            aria-invalid={draft.error ? true : undefined}
            aria-describedby={describedBy}
            onChange={(e) => onChange({ text: e.target.value, suggested: draft.suggested })}
          />
          <p id={ids.count} className={`char-count${tooLong ? " is-over" : ""}`}>
            {isDecorative
              ? "No description needed for a decorative image."
              : tooLong
                ? `${len} characters. That's long — try to keep it to about ${SOFT_LIMIT}.`
                : `${len} of about ${SOFT_LIMIT} characters`}
          </p>
          {!isDecorative && startsWithImageOf && (
            <p id={ids.warn} className="field__warning">
              <Icon name="alert" size={16} /> Try starting with what the image shows instead of "Image of".
            </p>
          )}
          {draft.suggested && !isDecorative && (
            <p id={ids.draft} className="field__warning">
              <Icon name="sparkle" size={16} /> This is a draft from Readpath. Check that it's accurate before you save.
            </p>
          )}
        </div>

        <div className="checkbox">
          <input
            id={ids.deco}
            type="checkbox"
            checked={isDecorative}
            disabled={draft.saving}
            aria-describedby={ids.decoHint}
            onChange={(e) => onChange({ decorative: e.target.checked })}
          />
          <div>
            <label htmlFor={ids.deco}>This image is decorative — screen readers should skip it</label>
            <p id={ids.decoHint} className="field__hint">
              For borders, background patterns and pictures that add no information.
            </p>
          </div>
        </div>

        {draft.error && (
          <p id={ids.err} className="field__error" role="alert">
            <Icon name="alertCircle" size={16} /> {draft.error}
          </p>
        )}

        <div className="inline-row">
          <button type="submit" className="btn btn--primary" aria-disabled={draft.saving || undefined}>
            {draft.saving ? "Saving…" : "Save"}
            <span className="visually-hidden"> image {number}</span>
          </button>
          {visionAvailable && !isDecorative && (
            <button
              type="button"
              className="btn btn--secondary"
              aria-disabled={draft.suggesting || draft.saving || undefined}
              onClick={() => onSuggest(() => textRef.current?.focus())}
            >
              <Icon name="sparkle" size={18} /> {draft.suggesting ? "Writing a draft…" : "Suggest a description"}
              <span className="visually-hidden"> for image {number}</span>
            </button>
          )}
        </div>
      </form>
    </li>
  );
}
