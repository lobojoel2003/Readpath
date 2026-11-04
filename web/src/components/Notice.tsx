import type { ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

type NoticeTone = "error" | "success" | "info" | "warning";

const ICON: Record<NoticeTone, IconName> = {
  error: "alertCircle",
  success: "checkCircle",
  info: "info",
  warning: "alert",
};

interface NoticeProps {
  tone: NoticeTone;
  title?: ReactNode;
  children?: ReactNode;
  /** Errors default to role="alert" so they are announced right away. */
  role?: "alert" | "status" | "none";
  className?: string;
  id?: string;
  action?: ReactNode;
}

export function Notice({ tone, title, children, role, className, id, action }: NoticeProps) {
  const ariaRole = role ?? (tone === "error" ? "alert" : undefined);
  return (
    <div
      id={id}
      className={`notice notice--${tone}${className ? ` ${className}` : ""}`}
      role={ariaRole === "none" ? undefined : ariaRole}
    >
      <Icon name={ICON[tone]} size={22} className="notice__icon" />
      <div className="notice__body">
        {title && <p className="notice__title">{title}</p>}
        {children}
        {action && <div className="notice__action">{action}</div>}
      </div>
    </div>
  );
}

/** Standard error block for a failed load, with an optional retry button. */
export function LoadError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <Notice
      tone="error"
      title="Readpath couldn't load this."
      action={
        onRetry && (
          <button type="button" className="btn btn--secondary" onClick={onRetry}>
            <Icon name="refresh" size={18} /> Try again
          </button>
        )
      }
    >
      <p>{message}</p>
    </Notice>
  );
}
