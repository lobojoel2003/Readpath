import { useEffect, useRef, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Icon } from "./Icon";

/**
 * Set by the layout right after a client-side navigation. Pages often swap a
 * "Loading…" h1 for the real one; if that drops focus to <body>, the new h1
 * takes it back (only within this window, and never from another control).
 */
export const routeFocus = { until: 0 };

/**
 * The page's single h1. tabIndex=-1 lets the layout move focus here after
 * each route change, so screen reader users hear where they landed.
 */
export function PageTitle({ children, eyebrow }: { children: ReactNode; eyebrow?: ReactNode }) {
  const ref = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const active = document.activeElement;
    if (Date.now() < routeFocus.until && (!active || active === document.body)) {
      ref.current?.focus({ preventScroll: true });
    }
  }, []);

  return (
    <div className="page-title-wrap">
      {eyebrow && <p className="eyebrow">{eyebrow}</p>}
      <h1 ref={ref} className="page-title" tabIndex={-1}>
        {children}
      </h1>
    </div>
  );
}

export interface Crumb {
  label: string;
  to?: string;
}

export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="Breadcrumb" className="breadcrumbs">
      <ol>
        {items.map((c, i) => {
          const last = i === items.length - 1;
          return (
            <li key={`${i}-${c.label}`}>
              {c.to && !last ? (
                <Link to={c.to}>{c.label}</Link>
              ) : (
                <span aria-current={last ? "page" : undefined}>{c.label}</span>
              )}
              {!last && <Icon name="chevronRight" size={14} className="breadcrumbs__sep" />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** A link that opens outside the app, labelled as such for screen readers. */
export function ExternalLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={className}>
      {children}
      <Icon name="external" size={14} className="icon--inline" />
      <span className="visually-hidden"> (opens in a new tab)</span>
    </a>
  );
}

interface PaginationProps {
  page: number; // 1-based
  pageCount: number;
  onChange: (page: number) => void;
  label: string;
}

export function Pagination({ page, pageCount, onChange, label }: PaginationProps) {
  if (pageCount <= 1) return null;
  return (
    <nav aria-label={label} className="pagination">
      <button
        type="button"
        className="btn btn--secondary btn--sm"
        onClick={() => onChange(page - 1)}
        disabled={page <= 1}
      >
        <Icon name="chevronLeft" size={16} /> Previous<span className="visually-hidden"> page</span>
      </button>
      <span className="pagination__status">
        Page {page} of {pageCount}
      </span>
      <button
        type="button"
        className="btn btn--secondary btn--sm"
        onClick={() => onChange(page + 1)}
        disabled={page >= pageCount}
      >
        Next<span className="visually-hidden"> page</span> <Icon name="chevronRight" size={16} />
      </button>
    </nav>
  );
}
