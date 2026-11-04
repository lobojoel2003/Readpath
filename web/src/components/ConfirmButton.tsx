import { useId, useRef, type ReactNode } from "react";

interface ConfirmButtonProps {
  /** Visible button content. */
  children: ReactNode;
  className?: string;
  disabled?: boolean;
  /** Accessible name for the trigger, when the visible text is not enough. */
  ariaLabel?: string;
  title: string;
  body: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
}

/**
 * A button that asks "Are you sure?" in a native modal <dialog>.
 * The browser handles focus trapping and Escape; we return focus to the
 * trigger when the dialog closes.
 */
export function ConfirmButton({
  children,
  className = "btn btn--secondary",
  disabled,
  ariaLabel,
  title,
  body,
  confirmLabel,
  cancelLabel = "Cancel",
  destructive,
  onConfirm,
}: ConfirmButtonProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const bodyId = useId();

  const close = () => {
    dialogRef.current?.close();
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={className}
        aria-disabled={disabled || undefined}
        aria-label={ariaLabel}
        aria-haspopup="dialog"
        onClick={() => {
          if (!disabled) dialogRef.current?.showModal();
        }}
      >
        {children}
      </button>
      <dialog
        ref={dialogRef}
        className="dialog"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        onClose={() => triggerRef.current?.focus()}
      >
        <h2 id={titleId} className="dialog__title">
          {title}
        </h2>
        <div id={bodyId} className="dialog__body">
          {body}
        </div>
        <div className="dialog__actions">
          <button type="button" className="btn btn--secondary" onClick={close} autoFocus>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={destructive ? "btn btn--danger" : "btn btn--primary"}
            onClick={() => {
              close();
              onConfirm();
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </dialog>
    </>
  );
}
