import { Link } from "react-router-dom";
import type { Check, CheckStatus, ReviewStep, Severity } from "../api/types";
import { StatusBadge } from "./Badges";
import type { Tone } from "../lib/format";

export const STEP_LABEL: Record<ReviewStep, string> = {
  basics: "Title and language",
  "reading-order": "Reading order",
  images: "Images",
  tables: "Tables",
};

const STATUS_DISPLAY: Record<CheckStatus, { label: string; tone: Tone }> = {
  fail: { label: "Problem", tone: "help" },
  warning: { label: "Check this", tone: "help" },
  manual: { label: "Check by hand", tone: "neutral" },
  pass: { label: "Passed", tone: "ready" },
  not_applicable: { label: "Doesn't apply", tone: "neutral" },
};

const SEVERITY_LABEL: Record<Severity, string> = {
  critical: "Critical",
  serious: "Serious",
  moderate: "Moderate",
  minor: "Minor",
};

export function isProblem(c: Check): boolean {
  return c.status === "fail" || c.status === "warning";
}

/** Splits checks into the sections shown on the report page. */
export function groupChecks(checks: Check[]) {
  return {
    needsHelp: checks.filter((c) => isProblem(c) && c.fix !== "auto"),
    autoFix: checks.filter((c) => isProblem(c) && c.fix === "auto"),
    byHand: checks.filter((c) => c.status === "manual"),
    passed: checks.filter((c) => c.status === "pass"),
    notApplicable: checks.filter((c) => c.status === "not_applicable"),
  };
}

function References({ check }: { check: Check }) {
  const refs = [...check.wcag.map((w) => `WCAG ${w}`), ...check.pdfua.map((p) => `PDF/UA ${p}`)];
  if (!refs.length) return null;
  return (
    <p className="refs">
      <span className="visually-hidden">Standards: </span>
      {refs.join(" · ")}
    </p>
  );
}

interface CheckItemProps {
  check: Check;
  docId: string;
  compact?: boolean;
  /** Heading level for the check title (default 3). */
  level?: 3 | 4;
}

export function CheckItem({ check, docId, compact, level = 3 }: CheckItemProps) {
  const display = STATUS_DISPLAY[check.status];
  const problem = isProblem(check);
  const Heading = level === 3 ? "h3" : "h4";

  return (
    <li className={`check check--${check.status}${compact ? " check--compact" : ""}`}>
      <div className="check__head">
        <Heading className="check__title">{check.title}</Heading>
        <div className="check__badges">
          <StatusBadge tone={display.tone} label={display.label} />
          {problem && (check.severity === "critical" || check.severity === "serious") && (
            <span className="check__severity">{SEVERITY_LABEL[check.severity]}</span>
          )}
        </div>
      </div>
      <p className="check__message">{check.message}</p>
      {problem && check.count != null && check.count > 0 && (
        <p className="check__count">
          {check.total != null ? `${check.count.toLocaleString()} of ${check.total.toLocaleString()} affected` : `${check.count.toLocaleString()} affected`}
        </p>
      )}
      {check.details.length > 0 && !compact && (
        <ul className="check__details">
          {check.details.map((d, i) => (
            <li key={i}>{d}</li>
          ))}
        </ul>
      )}
      {problem && check.fix === "guided" && check.step && (
        <p>
          <Link
            to={`/documents/${encodeURIComponent(docId)}/review/${check.step}`}
            className="btn btn--secondary btn--sm"
          >
            Fix in guided review: {STEP_LABEL[check.step]}
          </Link>
        </p>
      )}
      {problem && check.fix === "manual" && (
        <p className="check__manual small">
          This needs a change in the original file (for example, the Word document it was made from) or another tool.
        </p>
      )}
      {check.help && (
        <details className="disclosure disclosure--plain">
          <summary>{problem ? "Why this matters and how to fix it" : "Why this matters"}</summary>
          <div className="disclosure__body">
            <p>{check.help}</p>
          </div>
        </details>
      )}
      <References check={check} />
    </li>
  );
}

export function CheckList({ checks, docId, compact }: { checks: Check[]; docId: string; compact?: boolean }) {
  return (
    <ul className="check-list" role="list">
      {checks.map((c) => (
        <CheckItem key={c.id} check={c} docId={docId} compact={compact} />
      ))}
    </ul>
  );
}
