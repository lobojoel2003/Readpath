import type { DocStatus, DocumentSummary, Grade, ScanStatus } from "../api/types";

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : dateFormat.format(d);
}

export function formatBytes(n: number | null | undefined): string {
  if (n == null) return "";
  if (n < 1024) return `${n} bytes`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

export function roundScore(score: number | null | undefined): number | null {
  return score == null ? null : Math.round(score);
}

/** Show a URL without the scheme, for compact display. */
export function prettyUrl(url: string | null | undefined): string {
  if (!url) return "";
  return url.replace(/^https?:\/\//i, "").replace(/\/$/, "");
}

// ---------------------------------------------------------------------------
// Status vocabulary. Every visual status has an icon + text, never color alone.

export type Tone = "ready" | "auto" | "help" | "error" | "neutral" | "info";

export const GRADE_LABEL: Record<Grade, string> = {
  ready: "Ready",
  auto_fixable: "Can fix automatically",
  needs_help: "Needs your help",
};

export const GRADE_TONE: Record<Grade, Tone> = {
  ready: "ready",
  auto_fixable: "auto",
  needs_help: "help",
};

export const SCAN_STATUS_LABEL: Record<ScanStatus, string> = {
  queued: "Waiting to start",
  crawling: "Looking for PDFs",
  checking: "Checking PDFs",
  fixing: "Fixing PDFs",
  done: "Finished",
  failed: "Stopped with a problem",
  cancelled: "Cancelled",
};

export const ACTIVE_SCAN_STATUSES: readonly ScanStatus[] = ["queued", "crawling", "checking", "fixing"];
export const ACTIVE_DOC_STATUSES: readonly DocStatus[] = ["queued", "downloading", "checking", "fixing"];

export function isScanActive(status: ScanStatus): boolean {
  return ACTIVE_SCAN_STATUSES.includes(status);
}

export function isDocActive(status: DocStatus): boolean {
  return ACTIVE_DOC_STATUSES.includes(status);
}

export function scanTone(status: ScanStatus): Tone {
  if (status === "done") return "ready";
  if (status === "failed") return "error";
  if (status === "cancelled") return "neutral";
  return "info";
}

const DOC_ACTIVE_LABEL: Partial<Record<DocStatus, string>> = {
  queued: "Waiting",
  downloading: "Downloading",
  checking: "Checking",
  fixing: "Fixing",
};

export const OFFICE_KIND_LABEL: Record<string, string> = {
  word: "Word file",
  excel: "Excel file",
  powerpoint: "PowerPoint file",
  other: "Other file",
};

export type DocCategory = "ready" | "auto_fixable" | "needs_help" | "error" | "office" | "in_progress";

/** One category per document, used for filtering and for the status badge. */
export function docCategory(doc: DocumentSummary): DocCategory {
  if (doc.status === "error") return "error";
  if (doc.kind !== "pdf" || doc.status === "not_checked") return "office";
  if (isDocActive(doc.status)) return "in_progress";
  if (doc.grade) return doc.grade;
  return "in_progress";
}

export function docStatusDisplay(doc: DocumentSummary): { label: string; tone: Tone } {
  const cat = docCategory(doc);
  switch (cat) {
    case "error":
      return { label: "Couldn't open", tone: "error" };
    case "office":
      return { label: doc.kind === "pdf" ? "Not checked yet" : `${OFFICE_KIND_LABEL[doc.kind] ?? "File"}, not checked`, tone: "neutral" };
    case "in_progress":
      return { label: `${DOC_ACTIVE_LABEL[doc.status] ?? "Checking"}…`, tone: "info" };
    default:
      return { label: GRADE_LABEL[cat], tone: GRADE_TONE[cat] };
  }
}

/** Common document languages for US local government, as BCP 47 tags. */
export const LANGUAGES: { code: string; name: string }[] = [
  { code: "en-US", name: "English (US)" },
  { code: "es", name: "Spanish (Español)" },
  { code: "zh", name: "Chinese (中文)" },
  { code: "vi", name: "Vietnamese (Tiếng Việt)" },
  { code: "tl", name: "Tagalog" },
  { code: "ko", name: "Korean (한국어)" },
  { code: "fr", name: "French (Français)" },
  { code: "ar", name: "Arabic (العربية)" },
  { code: "ru", name: "Russian (Русский)" },
  { code: "ht", name: "Haitian Creole (Kreyòl ayisyen)" },
  { code: "pt", name: "Portuguese (Português)" },
  { code: "de", name: "German (Deutsch)" },
];

/** Loose BCP 47 check: "en", "en-US", "zh-Hant-TW", "es-419". */
export function isValidLangTag(tag: string): boolean {
  return /^[a-z]{2,3}(-[a-z0-9]{2,8})*$/i.test(tag.trim());
}
