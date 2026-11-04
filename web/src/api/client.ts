// A small typed client for the Readpath JSON API (see docs/api.md).
import type {
  BlockType,
  CreateScanRequest,
  DocumentSummary,
  DownloadVersion,
  FigureRequest,
  FiguresResponse,
  Health,
  PageBlocks,
  Report,
  ReviewOverview,
  ReviewStep,
  Scan,
  SuggestResponse,
  TablesResponse,
} from "./types";

const BASE = "/api";

/** Shown whenever the server answers 409 (the revision we sent is stale). */
export const CONFLICT_MESSAGE =
  "This document changed since you opened it, so Readpath is reloading the latest version. Please check your last change and try again.";

const NETWORK_MESSAGE =
  "Readpath can't reach its server right now. Check your connection and try again.";

export class ApiError extends Error {
  /** HTTP status, or 0 when the request never reached the server. */
  readonly status: number;
  /** The server's own `detail` text, when it sent one. */
  readonly serverDetail: string | null;

  constructor(status: number, message: string, serverDetail: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.serverDetail = serverDetail;
  }

  get isConflict(): boolean {
    return this.status === 409;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

export function isConflict(err: unknown): boolean {
  return err instanceof ApiError && err.isConflict;
}

export function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

/** Turn anything thrown into a plain-language message. */
export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return "Something went wrong. Please try again.";
}

function fallbackMessage(status: number): string {
  if (status === 404) return "We couldn't find that. It may have been deleted.";
  if (status === 413) return "That upload is too large for this server.";
  if (status === 503) return "That feature isn't available on this server right now.";
  if (status >= 500) return "Something went wrong on the Readpath server. Please try again in a moment.";
  return "Something went wrong. Please try again.";
}

/** FastAPI sends `{detail: string}` or, for validation errors, `{detail: [{msg}]}`. */
async function readDetail(res: Response): Promise<string | null> {
  try {
    const data: unknown = await res.json();
    const detail = (data as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      const parts = detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : ""))
        .filter(Boolean);
      if (parts.length) return parts.join(" ");
    }
  } catch {
    // Not JSON; fall through.
  }
  return null;
}

interface RequestOptions {
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
}

async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  } else if (opts.form) {
    body = opts.form; // the browser sets the multipart boundary
  }

  let res: Response;
  try {
    res = await fetch(BASE + path, { method, headers, body, signal: opts.signal });
  } catch (err) {
    if (isAbort(err)) throw err;
    throw new ApiError(0, NETWORK_MESSAGE);
  }

  if (!res.ok) {
    const detail = await readDetail(res);
    const message = res.status === 409 ? CONFLICT_MESSAGE : detail ?? fallbackMessage(res.status);
    throw new ApiError(res.status, message, detail);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const enc = encodeURIComponent;

export const api = {
  // Health
  health: (signal?: AbortSignal) => request<Health>("GET", "/health", { signal }),

  // Scans
  listScans: (signal?: AbortSignal) => request<Scan[]>("GET", "/scans", { signal }),
  createScan: (body: CreateScanRequest) => request<Scan>("POST", "/scans", { json: body }),
  upload: (files: File[], name?: string) => {
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    if (name) form.append("name", name);
    return request<Scan>("POST", "/uploads", { form });
  },
  getScan: (id: string, signal?: AbortSignal) => request<Scan>("GET", `/scans/${enc(id)}`, { signal }),
  getScanDocuments: (id: string, signal?: AbortSignal) =>
    request<DocumentSummary[]>("GET", `/scans/${enc(id)}/documents`, { signal }),
  cancelScan: (id: string) => request<Scan>("POST", `/scans/${enc(id)}/cancel`),
  autofixScan: (id: string) => request<Scan>("POST", `/scans/${enc(id)}/autofix`),
  deleteScan: (id: string) => request<void>("DELETE", `/scans/${enc(id)}`),

  // Documents
  getReport: (id: string, signal?: AbortSignal) => request<Report>("GET", `/documents/${enc(id)}`, { signal }),
  autofixDocument: (id: string) => request<Report>("POST", `/documents/${enc(id)}/autofix`),
  recheckDocument: (id: string) => request<Report>("POST", `/documents/${enc(id)}/recheck`),
  revertDocument: (id: string) => request<Report>("POST", `/documents/${enc(id)}/revert`),

  // Guided review
  getReview: (id: string, signal?: AbortSignal) =>
    request<ReviewOverview>("GET", `/documents/${enc(id)}/review`, { signal }),
  putMetadata: (id: string, revision: number, title: string, lang: string) =>
    request<ReviewOverview>("PUT", `/documents/${enc(id)}/metadata`, { json: { revision, title, lang } }),
  completeStep: (id: string, step: ReviewStep, revision: number) =>
    request<ReviewOverview>("POST", `/documents/${enc(id)}/review/${enc(step)}/complete`, { json: { revision } }),
  getPageBlocks: (id: string, page: number, signal?: AbortSignal) =>
    request<PageBlocks>("GET", `/documents/${enc(id)}/pages/${page}/blocks`, { signal }),
  putPageOrder: (id: string, page: number, revision: number, blockIds: string[]) =>
    request<PageBlocks>("PUT", `/documents/${enc(id)}/pages/${page}/order`, {
      json: { revision, block_ids: blockIds },
    }),
  putBlockType: (id: string, page: number, blockId: string, revision: number, type: BlockType) =>
    request<PageBlocks>("PUT", `/documents/${enc(id)}/pages/${page}/blocks/${enc(blockId)}`, {
      json: { revision, type },
    }),
  getFigures: (id: string, signal?: AbortSignal) =>
    request<FiguresResponse>("GET", `/documents/${enc(id)}/figures`, { signal }),
  putFigure: (id: string, figureId: string, body: FigureRequest) =>
    request<FiguresResponse>("PUT", `/documents/${enc(id)}/figures/${enc(figureId)}`, { json: body }),
  suggestAlt: (id: string, figureId: string) =>
    request<SuggestResponse>("POST", `/documents/${enc(id)}/figures/${enc(figureId)}/suggest`),
  getTables: (id: string, signal?: AbortSignal) =>
    request<TablesResponse>("GET", `/documents/${enc(id)}/tables`, { signal }),
  putTable: (id: string, tableId: string, revision: number, headerRow: boolean, headerColumn: boolean) =>
    request<TablesResponse>("PUT", `/documents/${enc(id)}/tables/${enc(tableId)}`, {
      json: { revision, header_row: headerRow, header_column: headerColumn },
    }),
  finish: (id: string, revision: number) =>
    request<Report>("POST", `/documents/${enc(id)}/finish`, { json: { revision } }),
};

// URLs used directly in <a href> / <img src>.
export const urls = {
  reportCsv: (scanId: string) => `${BASE}/scans/${enc(scanId)}/report.csv`,
  download: (docId: string, version: DownloadVersion) =>
    `${BASE}/documents/${enc(docId)}/download?version=${version}`,
  pageImage: (docId: string, page: number, width = 900) =>
    `${BASE}/documents/${enc(docId)}/pages/${page}/image?width=${width}`,
  /**
   * Figure and table `image_url`s are "relative URLs". A root-relative path
   * ("/api/...") or full URL is used as-is; a bare path ("documents/...") is
   * treated as relative to /api/.
   */
  asset: (url: string) => (/^(\/|[a-z][a-z0-9+.-]*:)/i.test(url) ? url : `${BASE}/${url}`),
};
