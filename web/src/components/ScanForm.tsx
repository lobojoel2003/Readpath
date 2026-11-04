import { useId, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { Icon } from "./Icon";
import { Notice } from "./Notice";

const MAX_PAGES_LIMIT = 5000;
const MAX_DEPTH_LIMIT = 20;

/** Accepts "example.gov" or "https://example.gov/path"; returns null if invalid. */
export function normalizeWebsite(input: string): string | null {
  let value = input.trim();
  if (!value || /\s/.test(value)) return null;
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(value)) value = `https://${value}`;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  const host = url.hostname;
  const looksLikeHost = host === "localhost" || /^[^.]+(\.[^.]+)+$/.test(host);
  if (!looksLikeHost || host.endsWith(".")) return null;
  return url.href;
}

function parseWhole(value: string, min: number, max: number): number | null {
  if (!/^\d+$/.test(value.trim())) return null;
  const n = Number(value);
  return n >= min && n <= max ? n : null;
}

interface Errors {
  url?: string;
  maxPages?: string;
  maxDepth?: string;
}

export function ScanForm() {
  const navigate = useNavigate();
  const ids = { url: useId(), urlHint: useId(), urlErr: useId(), pages: useId(), pagesHint: useId(), pagesErr: useId(), depth: useId(), depthHint: useId(), depthErr: useId(), subs: useId(), subsHint: useId() };
  const [url, setUrl] = useState("");
  const [maxPages, setMaxPages] = useState("200");
  const [maxDepth, setMaxDepth] = useState("5");
  const [includeSubdomains, setIncludeSubdomains] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  const [attempted, setAttempted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const urlRef = useRef<HTMLInputElement>(null);
  const pagesRef = useRef<HTMLInputElement>(null);
  const depthRef = useRef<HTMLInputElement>(null);

  const normalized = normalizeWebsite(url);

  function validate(next = { url, maxPages, maxDepth }): Errors {
    const e: Errors = {};
    if (!next.url.trim()) e.url = "Enter your website's address, like cityofmaple.gov.";
    else if (!normalizeWebsite(next.url)) e.url = "That doesn't look like a website address. Try something like cityofmaple.gov.";
    if (parseWhole(next.maxPages, 1, MAX_PAGES_LIMIT) == null)
      e.maxPages = `Enter a whole number from 1 to ${MAX_PAGES_LIMIT.toLocaleString()}.`;
    if (parseWhole(next.maxDepth, 1, MAX_DEPTH_LIMIT) == null)
      e.maxDepth = `Enter a whole number from 1 to ${MAX_DEPTH_LIMIT}.`;
    return e;
  }

  // After the first submit, keep messages in sync as the person types.
  function revalidate(next: Partial<{ url: string; maxPages: string; maxDepth: string }>) {
    if (attempted) setErrors(validate({ url, maxPages, maxDepth, ...next }));
  }

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    if (submitting) return;
    setAttempted(true);
    setSubmitError(null);
    const e = validate();
    setErrors(e);
    if (e.url) return urlRef.current?.focus();
    if (e.maxPages || e.maxDepth) {
      const target = e.maxPages ? pagesRef : depthRef;
      if (moreOpen) return target.current?.focus();
      // Wait for the <details> to open before focusing inside it.
      setMoreOpen(true);
      requestAnimationFrame(() => target.current?.focus());
      return;
    }
    setSubmitting(true);
    try {
      const scan = await api.createScan({
        url: normalized!,
        max_pages: Number(maxPages),
        max_depth: Number(maxDepth),
        include_subdomains: includeSubdomains,
      });
      navigate(`/scans/${encodeURIComponent(scan.id)}`);
    } catch (err) {
      setSubmitError(errorMessage(err));
      setSubmitting(false);
    }
  }

  const describe = (...parts: (string | false | undefined)[]) => parts.filter(Boolean).join(" ") || undefined;

  return (
    <form className="stack" onSubmit={onSubmit} noValidate aria-labelledby="scan-heading">
      <div className="field">
        <label htmlFor={ids.url} className="field__label">
          Website address
        </label>
        <p id={ids.urlHint} className="field__hint">
          Readpath visits your pages and finds every PDF they link to.
        </p>
        <div className="input-row">
          <span className="input-row__icon" aria-hidden="true">
            <Icon name="globe" size={20} />
          </span>
          <input
            ref={urlRef}
            id={ids.url}
            className="input input--with-icon"
            type="text"
            inputMode="url"
            autoComplete="url"
            spellCheck={false}
            autoCapitalize="none"
            placeholder="cityofmaple.gov"
            value={url}
            aria-invalid={errors.url ? true : undefined}
            aria-describedby={describe(ids.urlHint, errors.url && ids.urlErr)}
            onChange={(e) => {
              setUrl(e.target.value);
              revalidate({ url: e.target.value });
            }}
          />
        </div>
        {errors.url && (
          <p id={ids.urlErr} className="field__error">
            <Icon name="alertCircle" size={16} /> {errors.url}
          </p>
        )}
        {!errors.url && normalized && normalized !== url.trim() && (
          <p className="field__hint field__hint--soft">Readpath will start at {normalized}</p>
        )}
      </div>

      <details className="disclosure" open={moreOpen} onToggle={(e) => setMoreOpen(e.currentTarget.open)}>
        <summary>More options</summary>
        <div className="disclosure__body stack">
          <div className="field-grid">
            <div className="field">
              <label htmlFor={ids.pages} className="field__label">
                Most pages to visit
              </label>
              <p id={ids.pagesHint} className="field__hint">
                Up to {MAX_PAGES_LIMIT.toLocaleString()}.
              </p>
              <input
                ref={pagesRef}
                id={ids.pages}
                className="input input--short"
                type="text"
                inputMode="numeric"
                value={maxPages}
                aria-invalid={errors.maxPages ? true : undefined}
                aria-describedby={describe(ids.pagesHint, errors.maxPages && ids.pagesErr)}
                onChange={(e) => {
                  setMaxPages(e.target.value);
                  revalidate({ maxPages: e.target.value });
                }}
              />
              {errors.maxPages && (
                <p id={ids.pagesErr} className="field__error">
                  <Icon name="alertCircle" size={16} /> {errors.maxPages}
                </p>
              )}
            </div>
            <div className="field">
              <label htmlFor={ids.depth} className="field__label">
                How many clicks deep
              </label>
              <p id={ids.depthHint} className="field__hint">
                How far from the first page to follow links.
              </p>
              <input
                ref={depthRef}
                id={ids.depth}
                className="input input--short"
                type="text"
                inputMode="numeric"
                value={maxDepth}
                aria-invalid={errors.maxDepth ? true : undefined}
                aria-describedby={describe(ids.depthHint, errors.maxDepth && ids.depthErr)}
                onChange={(e) => {
                  setMaxDepth(e.target.value);
                  revalidate({ maxDepth: e.target.value });
                }}
              />
              {errors.maxDepth && (
                <p id={ids.depthErr} className="field__error">
                  <Icon name="alertCircle" size={16} /> {errors.maxDepth}
                </p>
              )}
            </div>
          </div>
          <div className="checkbox">
            <input
              id={ids.subs}
              type="checkbox"
              checked={includeSubdomains}
              aria-describedby={ids.subsHint}
              onChange={(e) => setIncludeSubdomains(e.target.checked)}
            />
            <div>
              <label htmlFor={ids.subs}>Include subdomains</label>
              <p id={ids.subsHint} className="field__hint">
                Also check sites like library.cityofmaple.gov.
              </p>
            </div>
          </div>
        </div>
      </details>

      {submitError && (
        <Notice tone="error" title="The scan didn't start.">
          <p>{submitError}</p>
        </Notice>
      )}

      <div>
        <button type="submit" className="btn btn--primary btn--lg" aria-disabled={submitting || undefined}>
          {submitting ? (
            <>
              <span className="spinner spinner--on-accent" aria-hidden="true" /> Starting scan…
            </>
          ) : (
            <>
              <Icon name="arrowRight" size={20} /> Start scan
            </>
          )}
        </button>
      </div>
    </form>
  );
}
