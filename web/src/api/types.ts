// Types for the Readpath HTTP API.
// Everything above the "Request bodies" line is copied verbatim from
// docs/api.md — keep the two in sync.

export type ScanStatus =
  | "queued" | "crawling" | "checking" | "fixing" | "done" | "failed" | "cancelled";

export interface ScanSummary {
  documents: number;          // every document found (PDF + Office files)
  pdfs: number;
  checked: number;            // PDFs checked so far
  average_score: number | null; // 0-100, over checked PDFs (current versions)
  ready: number;              // grade "ready"
  auto_fixable: number;       // grade "auto_fixable"
  needs_help: number;         // grade "needs_help"
  errors: number;             // could not download or open
  other_documents: number;    // Word/Excel/PowerPoint: listed but not checked yet
}

export interface Scan {
  id: string;
  kind: "crawl" | "upload";
  name: string;               // host name for crawls, or the upload batch name
  start_url: string | null;
  status: ScanStatus;
  message: string | null;     // progress or error text, e.g. "Visited 34 of up to 200 pages"
  pages_crawled: number;
  max_pages: number | null;
  created_at: string;
  finished_at: string | null;
  summary: ScanSummary;
}

// ready        = passes every automatic check
// auto_fixable = at least one problem Readpath can fix by itself
// needs_help   = remaining problems need a person (guided review or source file)
export type Grade = "ready" | "auto_fixable" | "needs_help";

export type DocKind = "pdf" | "word" | "excel" | "powerpoint" | "other";
export type DocStatus =
  | "queued" | "downloading" | "checking" | "fixing" | "checked" | "error" | "not_checked";

export interface DocumentSummary {
  id: string;
  scan_id: string;
  filename: string;
  kind: DocKind;
  url: string | null;         // where the file lives on the website
  found_on: string | null;    // web page that links to it
  status: DocStatus;
  error: string | null;
  size_bytes: number | null;
  page_count: number | null;
  title: string | null;       // title metadata of the current version
  score: number | null;       // 0-100, current version
  original_score: number | null;
  grade: Grade | null;
  counts: { failed: number; auto_fixable: number; needs_help: number; passed: number };
  fixed: boolean;             // current version differs from the original
  revision: number;           // increments on every change to the current version
  updated_at: string;
}

export type CheckStatus = "pass" | "fail" | "warning" | "manual" | "not_applicable";
export type Severity = "critical" | "serious" | "moderate" | "minor";
// auto   = Readpath can fix it with "Fix automatically"
// guided = fixed in the guided review (see `step`)
// manual = needs the source file or another tool (e.g. re-export, contrast)
export type FixKind = "auto" | "guided" | "manual";
export type ReviewStep = "basics" | "reading-order" | "images" | "tables";

export interface Check {
  id: string;                 // stable id, e.g. "title", "alt-text"
  title: string;              // "Document has a title"
  status: CheckStatus;
  severity: Severity;
  wcag: string[];             // WCAG 2.1 success criteria, e.g. ["2.4.2"]
  pdfua: string[];            // ISO 14289-1 (PDF/UA-1) clauses, e.g. ["7.1"]
  message: string;            // plain-language result for this document
  help: string;               // why it matters and how it gets fixed
  fix: FixKind | null;        // null when passing / not applicable
  step: ReviewStep | null;    // guided-review step that handles it
  count: number | null;       // affected items (e.g. 3 images without alt text)
  total: number | null;       // items of that kind (e.g. 7 images)
  details: string[];          // optional extra lines (page numbers, examples)
}

export interface VeraPdfResult {
  available: boolean;         // veraPDF installed on the server
  ran: boolean;
  compliant: boolean | null;
  profile: string | null;     // e.g. "PDF/UA-1"
  failed_rules: { clause: string; test: number; description: string; count: number }[];
  error: string | null;
}

export interface Report {
  document: DocumentSummary;
  checks: Check[];            // ordered most important first
  verapdf: VeraPdfResult | null;
  fixes_applied: string[];    // plain-language list of what the last auto-fix did
  checked_at: string;
}

export interface ReviewStepState {
  id: ReviewStep;
  label: string;              // "Title and language", "Reading order", "Images", "Tables"
  status: "todo" | "done" | "not_needed";
  count: number;              // items in this step (pages, images, tables)
}

export interface ReviewOverview {
  document_id: string;
  revision: number;
  tagged: boolean;            // false: run autofix first (it adds tags)
  page_count: number;
  title: string | null;
  lang: string | null;        // BCP 47, e.g. "en-US"
  suggested_title: string | null;
  suggested_lang: string | null;
  vision_available: boolean;  // a local vision model can draft alt text
  steps: ReviewStepState[];
  pages: { number: number; width: number; height: number; blocks: number }[]; // points, as displayed
}

export interface Block {
  id: string;
  type: string;               // standard structure type: "P", "H1".."H6", "Figure", "Table", "L", ...
  label: string;              // "Paragraph", "Heading 1", "Image", "Table", "List", ...
  text: string;               // preview (max ~160 chars); alt text for images
  bbox: [number, number, number, number] | null;
  editable_type: boolean;     // type can be changed with the block endpoint
  pages: number[];            // pages the block touches
}

export interface PageBlocks { revision: number; page: number; blocks: Block[] }

export interface Figure {
  id: string;
  page: number;
  bbox: [number, number, number, number] | null;
  alt: string | null;
  image_url: string;          // PNG crop of the figure (relative URL)
  context: string;            // nearby text, to help write alt text
}

export interface TableCell { text: string; header: boolean; row_span: number; col_span: number }

export interface Table {
  id: string;
  page: number;
  bbox: [number, number, number, number] | null;
  image_url: string;          // PNG crop of the table
  rows: TableCell[][];
  header_row: boolean;        // every cell in the first row is a header
  header_column: boolean;     // every cell in the first column is a header
}

export interface Health {
  status: "ok";
  version: string;
  verapdf: boolean;           // veraPDF available
  vision: boolean;            // alt-text drafting available
  ocr: boolean;               // OCR available for scanned PDFs
}

// ---------------------------------------------------------------------------
// Request bodies (from the endpoint tables in docs/api.md)

export interface CreateScanRequest {
  url: string;
  max_pages?: number;         // default 200, max 5000
  max_depth?: number;         // default 5
  include_subdomains?: boolean; // default false
}

export interface MetadataRequest { revision: number; title: string; lang: string }
export interface RevisionRequest { revision: number }
export interface OrderRequest { revision: number; block_ids: string[] }
export interface BlockTypeRequest { revision: number; type: BlockType }
export interface FigureRequest { revision: number; alt?: string; decorative?: boolean }
export interface TableRequest { revision: number; header_row: boolean; header_column: boolean }

// Block types accepted by PUT /pages/{n}/blocks/{block_id}.
export type BlockType = "P" | "H1" | "H2" | "H3" | "H4" | "H5" | "H6" | "Caption" | "Artifact";

export interface FiguresResponse { revision: number; figures: Figure[] }
export interface TablesResponse { revision: number; tables: Table[] }
export interface SuggestResponse { alt: string }

export type DownloadVersion = "current" | "original";
