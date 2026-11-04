import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { api, errorMessage, isAbort, isConflict, urls } from "../../api/client";
import type { Block, BlockType, PageBlocks } from "../../api/types";
import { useAnnounce } from "../../components/Announcer";
import { Spinner } from "../../components/Badges";
import { Icon, type IconName } from "../../components/Icon";
import { LoadError, Notice } from "../../components/Notice";
import type { StepProps } from "./stepTypes";

const TYPE_OPTIONS: { value: BlockType; label: string }[] = [
  { value: "P", label: "Paragraph" },
  { value: "H1", label: "Heading 1 (main heading)" },
  { value: "H2", label: "Heading 2" },
  { value: "H3", label: "Heading 3" },
  { value: "H4", label: "Heading 4" },
  { value: "H5", label: "Heading 5" },
  { value: "H6", label: "Heading 6" },
  { value: "Caption", label: "Caption" },
  { value: "Artifact", label: "Not content (page header, footer, decoration)" },
];

type Control = "up" | "down" | "type" | "item";

function iconFor(type: string): IconName {
  if (/^H[1-6]$/.test(type)) return "text";
  if (type === "Figure") return "image";
  if (type === "Table") return "table";
  if (type === "L") return "list";
  return "text";
}

function shortText(b: Block, i: number): string {
  const t = b.text.trim().replace(/\s+/g, " ");
  if (!t) return `${b.label} ${i + 1}`;
  return t.length > 48 ? `“${t.slice(0, 45)}…”` : `“${t}”`;
}

const sameOrder = (a: Block[], b: Block[]) => a.length === b.length && a.every((x, i) => x.id === b[i].id);

export function ReadingOrderStep({ docId, overview, revision, setRevision, setOverview, onConflict, goNext, setLeaveGuard }: StepProps) {
  const announce = useAnnounce();
  const pageSelectId = useId();
  const pageCount = Math.max(1, overview.page_count);
  const [page, setPage] = useState(1);
  const [server, setServer] = useState<PageBlocks | null>(null);
  const [order, setOrder] = useState<Block[]>([]);
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [loadNonce, setLoadNonce] = useState(0);
  const [busy, setBusy] = useState<"order" | "type" | "complete" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewed, setViewed] = useState<Set<number>>(() => new Set());
  const [active, setActive] = useState<number | null>(null);
  const [imgState, setImgState] = useState<"loading" | "ok" | "error">("loading");
  const pendingFocus = useRef<{ index: number; control: Control } | null>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const saveBarRef = useRef<HTMLDivElement>(null);
  const revisionRef = useRef(revision);
  revisionRef.current = revision;

  const dirty = server != null && !sameOrder(order, server.blocks);
  const pageInfo = overview.pages.find((p) => p.number === page);
  const stepState = overview.steps.find((s) => s.id === "reading-order");
  const allViewed = viewed.size >= pageCount;

  // Load the blocks for the current page.
  useEffect(() => {
    const ctrl = new AbortController();
    setServer(null);
    setLoadError(null);
    setImgState("loading");
    api.getPageBlocks(docId, page, ctrl.signal).then(
      (pb) => {
        setServer(pb);
        setOrder(pb.blocks);
        setRevision(pb.revision);
        setViewed((v) => (v.has(page) ? v : new Set(v).add(page)));
      },
      (err: unknown) => {
        if (!isAbort(err)) setLoadError(err instanceof Error ? err : new Error(String(err)));
      },
    );
    return () => ctrl.abort();
  }, [docId, page, loadNonce, setRevision]);

  // Put focus back on the right control after the list re-renders.
  useLayoutEffect(() => {
    const want = pendingFocus.current;
    if (!want || !listRef.current) return;
    pendingFocus.current = null;
    const item = listRef.current.querySelector<HTMLElement>(`[data-index="${want.index}"]`);
    if (!item) return;
    const control = item.querySelector<HTMLElement>(`[data-control="${want.control}"]`);
    const fallback = want.control === "up" ? "down" : want.control === "down" ? "up" : null;
    const other = fallback ? item.querySelector<HTMLElement>(`[data-control="${fallback}"]`) : null;
    if (control && !(control as HTMLButtonElement).disabled) control.focus();
    else if (other && !(other as HTMLButtonElement).disabled) other.focus();
    else item.focus();
  }, [order]);

  const applyServer = (pb: PageBlocks) => {
    setServer(pb);
    setOrder(pb.blocks);
    setRevision(pb.revision);
    revisionRef.current = pb.revision;
  };

  const handleError = (err: unknown) => {
    if (isConflict(err)) onConflict();
    else setError(errorMessage(err));
  };

  const saveOrder = useCallback(async (): Promise<PageBlocks | null> => {
    if (!server) return null;
    setBusy("order");
    setError(null);
    try {
      const pb = await api.putPageOrder(docId, page, revisionRef.current, order.map((b) => b.id));
      applyServer(pb);
      announce(`Reading order for page ${page} saved.`);
      return pb;
    } catch (err) {
      handleError(err);
      return null;
    } finally {
      setBusy(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [server, docId, page, order, announce]);

  // Save unsaved order changes automatically before leaving the step.
  useEffect(() => {
    setLeaveGuard(dirty ? async () => (await saveOrder()) != null : null);
    return () => setLeaveGuard(null);
  }, [dirty, saveOrder, setLeaveGuard]);

  function move(index: number, delta: -1 | 1) {
    const target = index + delta;
    if (target < 0 || target >= order.length) return;
    const next = [...order];
    [next[index], next[target]] = [next[target], next[index]];
    pendingFocus.current = { index: target, control: delta < 0 ? "up" : "down" };
    setOrder(next);
    setActive(target);
    announce(`Moved ${shortText(order[index], index)} to number ${target + 1} of ${next.length}. Not saved yet.`);
  }

  async function changeType(index: number, type: BlockType) {
    setError(null);
    // Ids change after every save, so save a pending order first and use the
    // block at the same position in the fresh list.
    let blocks = order;
    if (dirty) {
      const pb = await saveOrder();
      if (!pb) return;
      blocks = pb.blocks;
    }
    const block = blocks[index];
    if (!block) return;
    setBusy("type");
    try {
      const pb = await api.putBlockType(docId, page, block.id, revisionRef.current, type);
      pendingFocus.current = {
        index: Math.max(0, Math.min(index, pb.blocks.length - 1)),
        control: type === "Artifact" ? "item" : "type",
      };
      applyServer(pb);
      const label = TYPE_OPTIONS.find((o) => o.value === type)?.label ?? type;
      announce(
        type === "Artifact"
          ? `${shortText(block, index)} marked as not content. It was taken out of the reading order.`
          : `Changed to ${label}. Saved.`,
      );
    } catch (err) {
      handleError(err);
    } finally {
      setBusy(null);
    }
  }

  async function goToPage(p: number) {
    if (busy || p < 1 || p > pageCount || p === page) return;
    if (dirty && !(await saveOrder())) return;
    setActive(null);
    setPage(p);
    announce(`Page ${p} of ${pageCount}.`);
  }

  async function complete() {
    if (busy) return;
    setError(null);
    if (dirty && !(await saveOrder())) return;
    setBusy("complete");
    try {
      const ov = await api.completeStep(docId, "reading-order", revisionRef.current);
      setOverview(ov);
      announce("Reading order step finished.");
      goNext();
    } catch (err) {
      handleError(err);
    } finally {
      setBusy(null);
    }
  }

  function focusItem(index: number) {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${index}"]`)?.focus();
  }

  const aspect = pageInfo && pageInfo.width > 0 && pageInfo.height > 0 ? `${pageInfo.width} / ${pageInfo.height}` : "8.5 / 11";

  return (
    <div className="step stack-lg">
      <div className="step__intro stack-sm">
        <h2>Check the reading order</h2>
        <p>
          Screen readers read this page in the numbered order. Make the numbers follow the way a person would read
          it. Mark headings so people can jump between sections.
        </p>
        <p className="refs">WCAG 1.3.1 · WCAG 1.3.2 · WCAG 2.4.6 · PDF/UA 7.2</p>
        {stepState?.status === "done" && (
          <p className="inline-note">
            <Icon name="checkCircle" size={16} /> You've finished this step. You can still make changes.
          </p>
        )}
      </div>

      <div className="page-picker" role="group" aria-label="Choose a page">
        <button type="button" className="btn btn--secondary btn--sm" onClick={() => void goToPage(page - 1)} aria-disabled={page <= 1 || busy !== null || undefined}>
          <Icon name="chevronLeft" size={16} /> Previous page
        </button>
        <div className="page-picker__current">
          <label htmlFor={pageSelectId}>Page</label>
          <select
            id={pageSelectId}
            className="select select--sm"
            value={page}
            onChange={(e) => void goToPage(Number(e.target.value))}
          >
            {Array.from({ length: pageCount }, (_, i) => (
              <option key={i + 1} value={i + 1}>
                {i + 1}
              </option>
            ))}
          </select>
          <span>of {pageCount}</span>
        </div>
        <button type="button" className="btn btn--secondary btn--sm" onClick={() => void goToPage(page + 1)} aria-disabled={page >= pageCount || busy !== null || undefined}>
          Next page <Icon name="chevronRight" size={16} />
        </button>
      </div>

      {error && (
        <Notice tone="error" title="That change wasn't saved.">
          <p>{error}</p>
        </Notice>
      )}

      {loadError ? (
        <LoadError error={loadError} onRetry={() => setLoadNonce((n) => n + 1)} />
      ) : !server ? (
        <Spinner label={`Loading page ${page}…`} />
      ) : (
        <div className="order-layout">
          <div className="order-layout__page">
            <h3 className="visually-hidden">Picture of page {page}</h3>
            <div className="page-view" style={{ aspectRatio: aspect }}>
              {imgState !== "ok" && (
                <div className="page-view__placeholder">
                  {imgState === "error" ? "The picture of this page didn't load. You can still use the list." : <Spinner label="Loading page picture…" />}
                </div>
              )}
              <img
                src={urls.pageImage(docId, page, 900)}
                alt={`Page ${page} of the PDF, with numbered boxes showing the reading order`}
                className="page-view__img"
                onLoad={() => setImgState("ok")}
                onError={() => setImgState("error")}
              />
              <div className="page-view__overlay" aria-hidden="true">
                {order.map((b, i) =>
                  b.bbox ? (
                    <div
                      key={b.id}
                      className={`block-box${active === i ? " is-active" : ""}${/^H[1-6]$/.test(b.type) ? " is-heading" : ""}`}
                      style={{
                        left: `${b.bbox[0] * 100}%`,
                        top: `${b.bbox[1] * 100}%`,
                        width: `${Math.max(0, b.bbox[2] - b.bbox[0]) * 100}%`,
                        height: `${Math.max(0, b.bbox[3] - b.bbox[1]) * 100}%`,
                      }}
                      onMouseEnter={() => setActive(i)}
                      onMouseLeave={() => setActive((a) => (a === i ? null : a))}
                      onClick={() => focusItem(i)}
                    >
                      <span className="block-box__num">{i + 1}</span>
                    </div>
                  ) : null,
                )}
              </div>
            </div>
          </div>

          <div className="order-layout__list stack">
            <h3 id="order-list-heading">Reading order for page {page}</h3>
            {order.length === 0 ? (
              <div className="empty">
                <p>Nothing to read on this page. It may be blank, or only have decoration.</p>
              </div>
            ) : (
              <ol ref={listRef} className="block-list" aria-labelledby="order-list-heading">
                {order.map((b, i) => (
                  <BlockItem
                    key={b.id}
                    block={b}
                    index={i}
                    total={order.length}
                    active={active === i}
                    disabled={busy !== null}
                    onActive={setActive}
                    onMove={move}
                    onType={(t) => void changeType(i, t)}
                  />
                ))}
              </ol>
            )}

            <div ref={saveBarRef} tabIndex={-1} className={`save-bar${dirty ? " is-dirty" : ""}`}>
              {dirty ? (
                <>
                  <p className="save-bar__text">
                    <Icon name="alert" size={18} /> You changed the order. It isn't saved yet.
                  </p>
                  <div className="inline-row">
                    <button
                      type="button"
                      className="btn btn--primary"
                      aria-disabled={busy !== null || undefined}
                      onClick={async () => {
                        if (busy) return;
                        // The button goes away once saved; keep focus nearby.
                        if (await saveOrder()) saveBarRef.current?.focus();
                      }}
                    >
                      {busy === "order" ? "Saving…" : "Save order"}
                    </button>
                    <button
                      type="button"
                      className="btn btn--ghost"
                      disabled={busy !== null}
                      onClick={() => {
                        setOrder(server.blocks);
                        announce("Order changes undone.");
                        saveBarRef.current?.focus();
                      }}
                    >
                      Undo changes
                    </button>
                  </div>
                </>
              ) : (
                <p className="save-bar__text muted">
                  <Icon name="check" size={18} /> Order saved. Type changes save right away.
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="card stack-sm step__finish">
        <p>
          {allViewed
            ? "When every page reads in the right order, you're done with this step."
            : `You've looked at ${viewed.size} of ${pageCount} pages. Pages you don't open keep the order Readpath worked out from the layout.`}
        </p>
        <div>
          <button type="button" className="btn btn--primary btn--lg" onClick={() => void complete()} aria-disabled={busy !== null || undefined}>
            <Icon name="check" size={20} /> {busy === "complete" ? "Saving…" : "This reading order looks right"}
          </button>
        </div>
      </div>
    </div>
  );
}

interface BlockItemProps {
  block: Block;
  index: number;
  total: number;
  active: boolean;
  disabled: boolean;
  onActive: (i: number | null) => void;
  onMove: (i: number, delta: -1 | 1) => void;
  onType: (t: BlockType) => void;
}

function BlockItem({ block: b, index: i, total, active, disabled, onActive, onMove, onType }: BlockItemProps) {
  const selectId = useId();
  const known = TYPE_OPTIONS.some((o) => o.value === b.type);
  const name = shortText(b, i);

  return (
    <li
      data-index={i}
      tabIndex={-1}
      className={`block-item${active ? " is-active" : ""}`}
      onMouseEnter={() => onActive(i)}
      onMouseLeave={() => onActive(null)}
      onFocus={() => onActive(i)}
    >
      <span className="block-item__num" aria-hidden="true">
        {i + 1}
      </span>
      <div className="block-item__body">
        <p className="block-item__type">
          <Icon name={iconFor(b.type)} size={16} /> {b.label}
          {!b.bbox && <span className="muted"> · position on page unknown</span>}
        </p>
        <p className="block-item__text">
          {b.text.trim() ? (
            b.type === "Figure" ? `Description: ${b.text}` : b.text
          ) : (
            <em className="muted">{b.type === "Figure" ? "No description yet (add one in the Images step)" : "No text"}</em>
          )}
        </p>
        {b.pages.length > 1 && <p className="small muted">Continues on pages {b.pages.join(", ")}</p>}
      </div>
      <div className="block-item__controls">
        <div className="inline-row inline-row--tight">
          <button
            type="button"
            data-control="up"
            className="btn btn--secondary btn--sm"
            onClick={() => onMove(i, -1)}
            disabled={disabled || i === 0}
            aria-label={`Move up: ${name}`}
          >
            <Icon name="arrowUp" size={16} /> Move up
          </button>
          <button
            type="button"
            data-control="down"
            className="btn btn--secondary btn--sm"
            onClick={() => onMove(i, 1)}
            disabled={disabled || i === total - 1}
            aria-label={`Move down: ${name}`}
          >
            <Icon name="arrowDown" size={16} /> Move down
          </button>
        </div>
        {b.editable_type && (
          <div className="block-item__type-field">
            <label htmlFor={selectId} className="small field__label">
              Type<span className="visually-hidden"> for number {i + 1}, {name}</span>
            </label>
            <select
              id={selectId}
              data-control="type"
              className="select select--sm"
              value={b.type}
              disabled={disabled}
              onChange={(e) => onType(e.target.value as BlockType)}
            >
              {!known && <option value={b.type}>{b.label}</option>}
              {TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
    </li>
  );
}
