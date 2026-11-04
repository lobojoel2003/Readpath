import { useId, useRef, useState, type FormEvent } from "react";
import { api, errorMessage, isConflict } from "../../api/client";
import { useAnnounce } from "../../components/Announcer";
import { Icon } from "../../components/Icon";
import { Notice } from "../../components/Notice";
import { isValidLangTag, LANGUAGES } from "../../lib/format";
import type { StepProps } from "./stepTypes";

const OTHER = "other";

function languageName(code: string | null): string | null {
  if (!code) return null;
  const known = LANGUAGES.find((l) => l.code.toLowerCase() === code.toLowerCase());
  if (known) return known.name;
  try {
    return new Intl.DisplayNames(undefined, { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

function choiceFor(code: string): { choice: string; other: string } {
  const known = LANGUAGES.find((l) => l.code.toLowerCase() === code.toLowerCase());
  return known ? { choice: known.code, other: "" } : { choice: OTHER, other: code };
}

export function BasicsStep({ docId, overview, setOverview, onConflict, goNext }: StepProps) {
  const announce = useAnnounce();
  const ids = {
    title: useId(),
    titleHint: useId(),
    titleErr: useId(),
    titleWarn: useId(),
    lang: useId(),
    langHint: useId(),
    other: useId(),
    otherHint: useId(),
    otherErr: useId(),
  };
  const initialLang = choiceFor(overview.lang ?? overview.suggested_lang ?? "en-US");
  const [title, setTitle] = useState(overview.title ?? "");
  const [langChoice, setLangChoice] = useState(initialLang.choice);
  const [otherLang, setOtherLang] = useState(initialLang.other);
  const [errors, setErrors] = useState<{ title?: string; lang?: string }>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const titleRef = useRef<HTMLInputElement>(null);
  const otherRef = useRef<HTMLInputElement>(null);

  const lang = langChoice === OTHER ? otherLang.trim() : langChoice;
  const suggestion = overview.suggested_title?.trim();
  const showSuggestion = !!suggestion && suggestion !== title.trim();
  const langSuggestion = overview.suggested_lang;
  const showLangSuggestion = !!langSuggestion && langSuggestion.toLowerCase() !== lang.toLowerCase();
  const looksLikeFileName = /\.(pdf|docx?|xlsx?|pptx?)$/i.test(title.trim()) || /^[\w-]+_[\w-]+$/.test(title.trim());
  const stepState = overview.steps.find((s) => s.id === "basics");

  function validate() {
    const e: { title?: string; lang?: string } = {};
    if (!title.trim()) e.title = "Enter a title for this document.";
    if (langChoice === OTHER && !isValidLangTag(otherLang))
      e.lang = 'Enter a language code like "pl" (Polish) or "es-MX" (Spanish, Mexico).';
    return e;
  }

  async function onSubmit(ev: FormEvent) {
    ev.preventDefault();
    if (saving) return;
    setSaveError(null);
    const e = validate();
    setErrors(e);
    if (e.title) return titleRef.current?.focus();
    if (e.lang) return otherRef.current?.focus();

    setSaving(true);
    try {
      const afterMeta = await api.putMetadata(docId, overview.revision, title.trim(), lang);
      const afterComplete = await api.completeStep(docId, "basics", afterMeta.revision);
      setOverview(afterComplete);
      announce("Title and language saved.");
      goNext();
    } catch (err) {
      if (isConflict(err)) onConflict();
      else setSaveError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  const describe = (...parts: (string | false | undefined)[]) => parts.filter(Boolean).join(" ") || undefined;

  return (
    <form className="step stack-lg" onSubmit={onSubmit} noValidate aria-labelledby="basics-heading">
      <div className="step__intro stack-sm">
        <h2 id="basics-heading">Give the PDF a clear title and language</h2>
        <p>
          When someone opens this PDF, their screen reader says the title first. The language tells it how to
          pronounce the words.
        </p>
        <p className="refs">WCAG 2.4.2 · WCAG 3.1.1 · PDF/UA 7.1</p>
        {stepState?.status === "done" && (
          <p className="inline-note">
            <Icon name="checkCircle" size={16} /> You've finished this step. You can still change it.
          </p>
        )}
      </div>

      <div className="card stack">
        <div className="field">
          <label htmlFor={ids.title} className="field__label">
            Title
          </label>
          <p id={ids.titleHint} className="field__hint">
            Use a name people would recognize, like "City Council Minutes – March 4, 2026". Not the file name.
          </p>
          <input
            ref={titleRef}
            id={ids.title}
            className="input"
            type="text"
            value={title}
            aria-invalid={errors.title ? true : undefined}
            aria-describedby={describe(ids.titleHint, errors.title && ids.titleErr, looksLikeFileName && ids.titleWarn)}
            onChange={(e) => {
              setTitle(e.target.value);
              if (errors.title && e.target.value.trim()) setErrors((x) => ({ ...x, title: undefined }));
            }}
          />
          {errors.title && (
            <p id={ids.titleErr} className="field__error">
              <Icon name="alertCircle" size={16} /> {errors.title}
            </p>
          )}
          {!errors.title && looksLikeFileName && (
            <p id={ids.titleWarn} className="field__warning">
              <Icon name="alert" size={16} /> This looks like a file name. A plain-language title is easier to
              understand.
            </p>
          )}
          {showSuggestion && (
            <div className="suggestion">
              <span className="suggestion__label">Readpath suggests:</span>
              <span className="suggestion__text">"{suggestion}"</span>
              <button
                type="button"
                className="btn btn--secondary btn--sm"
                onClick={() => {
                  setTitle(suggestion!);
                  setErrors((x) => ({ ...x, title: undefined }));
                  announce("Suggested title added.");
                  titleRef.current?.focus();
                }}
              >
                Use suggestion
              </button>
            </div>
          )}
        </div>

        <div className="field">
          <label htmlFor={ids.lang} className="field__label">
            Language
          </label>
          <p id={ids.langHint} className="field__hint">
            The main language of the text in this document.
          </p>
          <select
            id={ids.lang}
            className="select"
            value={langChoice}
            aria-describedby={ids.langHint}
            onChange={(e) => {
              setLangChoice(e.target.value);
              setErrors((x) => ({ ...x, lang: undefined }));
            }}
          >
            {LANGUAGES.map((l) => (
              <option key={l.code} value={l.code}>
                {l.name}
              </option>
            ))}
            <option value={OTHER}>Other language…</option>
          </select>
          {showLangSuggestion && (
            <div className="suggestion">
              <span className="suggestion__label">Readpath thinks this document is in:</span>
              <span className="suggestion__text">{languageName(langSuggestion)}</span>
              <button
                type="button"
                className="btn btn--secondary btn--sm"
                onClick={() => {
                  const c = choiceFor(langSuggestion!);
                  setLangChoice(c.choice);
                  setOtherLang(c.other);
                  announce(`Language set to ${languageName(langSuggestion)}.`);
                }}
              >
                Use this language
              </button>
            </div>
          )}
        </div>

        {langChoice === OTHER && (
          <div className="field">
            <label htmlFor={ids.other} className="field__label">
              Language code
            </label>
            <p id={ids.otherHint} className="field__hint">
              A short code such as "pl" for Polish or "es-MX" for Spanish (Mexico). Technical name: BCP 47.
            </p>
            <input
              ref={otherRef}
              id={ids.other}
              className="input input--short"
              type="text"
              value={otherLang}
              spellCheck={false}
              autoCapitalize="none"
              aria-invalid={errors.lang ? true : undefined}
              aria-describedby={describe(ids.otherHint, errors.lang && ids.otherErr)}
              onChange={(e) => setOtherLang(e.target.value)}
            />
            {errors.lang && (
              <p id={ids.otherErr} className="field__error">
                <Icon name="alertCircle" size={16} /> {errors.lang}
              </p>
            )}
          </div>
        )}
      </div>

      {saveError && (
        <Notice tone="error" title="Your changes weren't saved.">
          <p>{saveError}</p>
        </Notice>
      )}

      <div className="step__actions">
        <button type="submit" className="btn btn--primary btn--lg" aria-disabled={saving || undefined}>
          {saving ? (
            <>
              <span className="spinner spinner--on-accent" aria-hidden="true" /> Saving…
            </>
          ) : (
            <>
              <Icon name="check" size={20} /> Save and continue
            </>
          )}
        </button>
      </div>
    </form>
  );
}
