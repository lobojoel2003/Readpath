import { useId, useRef, useState, type DragEvent, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { formatBytes, plural } from "../lib/format";
import { Icon } from "./Icon";
import { Notice } from "./Notice";

function isPdf(f: File): boolean {
  return f.type === "application/pdf" || /\.pdf$/i.test(f.name);
}

export function UploadForm() {
  const navigate = useNavigate();
  const inputId = useId();
  const hintId = useId();
  const errId = useId();
  const nameId = useId();
  const nameHintId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  function addFiles(list: FileList | File[]) {
    const incoming = Array.from(list);
    const pdfs = incoming.filter(isPdf);
    const skipped = incoming.filter((f) => !isPdf(f));
    setFiles((prev) => {
      const key = (f: File) => `${f.name}:${f.size}:${f.lastModified}`;
      const seen = new Set(prev.map(key));
      return [...prev, ...pdfs.filter((f) => !seen.has(key(f)))];
    });
    setError(
      skipped.length
        ? `Readpath only checks PDF files. Skipped: ${skipped.map((f) => f.name).join(", ")}.`
        : null,
    );
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (submitting) return;
    setSubmitError(null);
    if (!files.length) {
      setError("Choose at least one PDF file to check.");
      inputRef.current?.focus();
      return;
    }
    setSubmitting(true);
    try {
      const scan = await api.upload(files, name.trim() || undefined);
      navigate(`/scans/${encodeURIComponent(scan.id)}`);
    } catch (err) {
      setSubmitError(errorMessage(err));
      setSubmitting(false);
    }
  }

  const total = files.reduce((n, f) => n + f.size, 0);

  return (
    <form className="stack" onSubmit={onSubmit} noValidate aria-labelledby="upload-heading">
      <div
        className={`dropzone${dragging ? " is-dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <input
          ref={inputRef}
          id={inputId}
          className="visually-hidden file-input"
          type="file"
          multiple
          accept=".pdf,application/pdf"
          aria-invalid={error ? true : undefined}
          aria-describedby={[hintId, error ? errId : ""].filter(Boolean).join(" ")}
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = ""; // allow choosing the same file again after removing it
          }}
        />
        <label htmlFor={inputId} className="btn btn--secondary file-label">
          <Icon name="upload" size={20} /> Choose PDF files
        </label>
        <p id={hintId} className="field__hint">
          You can pick several at once, or drag them here.
        </p>
      </div>
      {error && (
        <p id={errId} className="field__error">
          <Icon name="alertCircle" size={16} /> {error}
        </p>
      )}

      {files.length > 0 && (
        <div>
          <p className="small muted">
            {plural(files.length, "file")} selected ({formatBytes(total)})
          </p>
          <ul className="file-list">
            {files.map((f, i) => (
              <li key={`${f.name}-${f.size}-${f.lastModified}`}>
                <Icon name="file" size={18} />
                <span className="file-list__name">{f.name}</span>
                <span className="file-list__size">{formatBytes(f.size)}</span>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  aria-label={`Remove ${f.name}`}
                  onClick={() => {
                    setFiles((prev) => prev.filter((_, j) => j !== i));
                    inputRef.current?.focus();
                  }}
                >
                  <Icon name="x" size={16} />
                  <span aria-hidden="true">Remove</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="field">
        <label htmlFor={nameId} className="field__label">
          Name for this batch <span className="field__optional">(optional)</span>
        </label>
        <p id={nameHintId} className="field__hint">
          For example, "Council packets – October".
        </p>
        <input
          id={nameId}
          className="input"
          type="text"
          value={name}
          aria-describedby={nameHintId}
          onChange={(e) => setName(e.target.value)}
        />
      </div>

      {submitError && (
        <Notice tone="error" title="The upload didn't finish.">
          <p>{submitError}</p>
        </Notice>
      )}

      <div>
        <button type="submit" className="btn btn--primary" aria-disabled={submitting || undefined}>
          {submitting ? (
            <>
              <span className="spinner spinner--on-accent" aria-hidden="true" /> Uploading…
            </>
          ) : (
            <>
              <Icon name="upload" size={20} /> Upload and check
            </>
          )}
        </button>
      </div>
    </form>
  );
}
