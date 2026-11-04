import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, CONFLICT_MESSAGE, api, errorMessage } from "../api/client";
import type { ReviewOverview } from "../api/types";
import { useAnnounce } from "../components/Announcer";
import { Spinner } from "../components/Badges";
import { Icon } from "../components/Icon";
import { LoadError, Notice } from "../components/Notice";
import { Breadcrumbs, PageTitle } from "../components/PageParts";
import { useDocumentTitle, useResource } from "../lib/hooks";
import { BasicsStep } from "./review/BasicsStep";
import { FinishStep } from "./review/FinishStep";
import { ImagesStep } from "./review/ImagesStep";
import { ReadingOrderStep } from "./review/ReadingOrderStep";
import { TablesStep } from "./review/TablesStep";
import { isWizardStep, WIZARD_STEPS, type StepProps, type WizardStep } from "./review/stepTypes";

const STATUS_TEXT = { todo: "To do", done: "Done", not_needed: "Nothing to do" } as const;

export function ReviewPage() {
  const { id = "", step: stepParam } = useParams();
  const navigate = useNavigate();
  const announce = useAnnounce();
  const review = useResource((signal) => api.getReview(id, signal), [id]);
  const doc = useResource((signal) => api.getReport(id, signal).then((r) => r.document), [id]);
  const scanName = useResource(
    (signal) => (doc.data ? api.getScan(doc.data.scan_id, signal).then((s) => s.name) : Promise.resolve(null)),
    [doc.data?.scan_id],
  );
  const [stepKey, setStepKey] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [tagging, setTagging] = useState(false);
  const [tagError, setTagError] = useState<string | null>(null);
  const leaveGuard = useRef<(() => Promise<boolean>) | null>(null);
  const noticeRef = useRef<HTMLDivElement>(null);

  const step: WizardStep | null = isWizardStep(stepParam) ? stepParam : null;
  const index = step ? WIZARD_STEPS.findIndex((s) => s.id === step) : -1;
  const overview = review.data;

  const labelOf = useCallback(
    (s: WizardStep) =>
      overview?.steps.find((x) => x.id === s)?.label ?? WIZARD_STEPS.find((x) => x.id === s)?.label ?? s,
    [overview],
  );

  const stepLabel = step ? labelOf(step) : "Guided review";
  useDocumentTitle(`${stepLabel} – guided review`);

  // Steps register their own leave guard and clear it when they unmount.
  useEffect(() => setNotice(null), [step]);

  const go = useCallback(
    async (target: WizardStep) => {
      if (leaveGuard.current && !(await leaveGuard.current())) return;
      navigate(`/documents/${encodeURIComponent(id)}/review/${target}`);
    },
    [id, navigate],
  );

  const { setData: setReviewData, reload: reloadReview } = review;
  const setOverview = useCallback((ov: ReviewOverview) => setReviewData(ov), [setReviewData]);
  const setRevision = useCallback(
    (revision: number) =>
      setReviewData((prev) => (prev && prev.revision !== revision ? { ...prev, revision } : prev!)),
    [setReviewData],
  );

  const onConflict = useCallback(async () => {
    setNotice(CONFLICT_MESSAGE);
    announce(CONFLICT_MESSAGE);
    try {
      setReviewData(await api.getReview(id));
    } catch {
      reloadReview();
    }
    setStepKey((k) => k + 1); // remount the step so it refetches its data
    // The notice sits above the step; bring it into view and keep focus off <body>.
    requestAnimationFrame(() => noticeRef.current?.focus());
  }, [announce, id, setReviewData, reloadReview]);

  const nextId = index >= 0 && index < WIZARD_STEPS.length - 1 ? WIZARD_STEPS[index + 1].id : null;
  const goNext = useCallback(() => {
    if (nextId) void go(nextId);
  }, [go, nextId]);
  const conflictHandler = useCallback(() => void onConflict(), [onConflict]);
  const setLeaveGuard = useCallback((g: (() => Promise<boolean>) | null) => {
    leaveGuard.current = g;
  }, []);

  async function addStructure() {
    if (tagging) return;
    setTagging(true);
    setTagError(null);
    announce("Adding structure. This can take a few seconds.");
    try {
      await api.autofixDocument(id);
      setReviewData(await api.getReview(id));
      announce("Structure added. You can start the review now.");
      setStepKey((k) => k + 1);
      // The button is gone now; put focus back at the top of the step.
      requestAnimationFrame(() => document.querySelector<HTMLElement>("main h1")?.focus());
    } catch (err) {
      setTagError(errorMessage(err));
    } finally {
      setTagging(false);
    }
  }

  const docPath = `/documents/${encodeURIComponent(id)}`;
  const crumbs = [
    { label: "Home", to: "/" },
    ...(doc.data ? [{ label: scanName.data ?? "Scan results", to: `/scans/${encodeURIComponent(doc.data.scan_id)}` }] : []),
    { label: doc.data?.filename ?? "Document", to: docPath },
    { label: "Guided review" },
  ];

  if (!step) {
    return (
      <div className="stack">
        <Breadcrumbs items={crumbs} />
        <PageTitle>We can't find that review step</PageTitle>
        <p>
          <Link to={`${docPath}/review/basics`} className="btn btn--primary">
            Start the guided review
          </Link>
        </p>
      </div>
    );
  }

  const prev = index > 0 ? WIZARD_STEPS[index - 1] : null;
  const next = index < WIZARD_STEPS.length - 1 ? WIZARD_STEPS[index + 1] : null;

  const stepProps: StepProps | null = overview
    ? {
        docId: id,
        overview,
        revision: overview.revision,
        setOverview,
        setRevision,
        onConflict: conflictHandler,
        goNext,
        setLeaveGuard,
      }
    : null;

  return (
    <div className="stack-lg review">
      <div className="stack">
        <Breadcrumbs items={crumbs} />
        <PageTitle eyebrow={`Guided review · Step ${index + 1} of ${WIZARD_STEPS.length}`}>{stepLabel}</PageTitle>
        {doc.data && (
          <p className="page-meta">
            For <span className="break">{doc.data.title || doc.data.filename}</span>
          </p>
        )}
      </div>

      <Stepper current={step} overview={overview} labelOf={labelOf} docPath={docPath} onGo={go} />

      {notice && (
        <div ref={noticeRef} tabIndex={-1} className="focus-target" role="group" aria-labelledby="conflict-title">
          <Notice tone="info" role="none" title={<span id="conflict-title">The document was updated</span>}>
            <p>{notice}</p>
          </Notice>
        </div>
      )}

      {review.error && !overview ? (
        review.error instanceof ApiError && review.error.isNotFound ? (
          <Notice tone="error" title="We can't find this document.">
            <p>It may have been deleted along with its scan.</p>
          </Notice>
        ) : (
          <LoadError error={review.error} onRetry={review.reload} />
        )
      ) : !overview || !stepProps ? (
        <Spinner label="Loading the review…" />
      ) : !overview.tagged ? (
        <section className="card card--accent stack" aria-labelledby="tag-heading">
          <h2 id="tag-heading">First, Readpath needs to add structure to this PDF</h2>
          <p>
            Screen readers use hidden structure (called "tags") to know what is a heading, a paragraph, an image or a
            table. This PDF doesn't have any yet. Readpath can add it for you, and then you can check its work in
            these steps.
          </p>
          <p className="small">
            <Icon name="shield" size={16} className="icon--inline" /> Your original file is kept. You can undo this
            at any time.
          </p>
          {tagError && (
            <Notice tone="error" title="That didn't work.">
              <p>{tagError}</p>
            </Notice>
          )}
          <div>
            <button type="button" className="btn btn--primary btn--lg" onClick={() => void addStructure()} aria-disabled={tagging || undefined}>
              {tagging ? (
                <>
                  <span className="spinner spinner--on-accent" aria-hidden="true" /> Adding structure…
                </>
              ) : (
                <>
                  <Icon name="wrench" size={20} /> Add structure now
                </>
              )}
            </button>
          </div>
        </section>
      ) : (
        <div key={`${step}-${stepKey}`}>
          {step === "basics" && <BasicsStep {...stepProps} />}
          {step === "reading-order" && <ReadingOrderStep {...stepProps} />}
          {step === "images" && <ImagesStep {...stepProps} />}
          {step === "tables" && <TablesStep {...stepProps} />}
          {step === "finish" && <FinishStep {...stepProps} />}
        </div>
      )}

      <nav aria-label="Previous and next step" className="wizard-nav">
        {prev ? (
          <button type="button" className="btn btn--secondary" onClick={() => void go(prev.id)}>
            <Icon name="arrowLeft" size={18} />
            <span>
              Back<span className="wizard-nav__step">: {labelOf(prev.id)}</span>
            </span>
          </button>
        ) : (
          <Link to={docPath} className="btn btn--ghost">
            <Icon name="arrowLeft" size={18} /> Back to the report
          </Link>
        )}
        {next && (
          <button type="button" className="btn btn--secondary" onClick={() => void go(next.id)}>
            <span>
              Next<span className="wizard-nav__step">: {labelOf(next.id)}</span>
            </span>
            <Icon name="arrowRight" size={18} />
          </button>
        )}
      </nav>
    </div>
  );
}

interface StepperProps {
  current: WizardStep;
  overview: ReviewOverview | undefined;
  labelOf: (s: WizardStep) => string;
  docPath: string;
  onGo: (s: WizardStep) => Promise<void>;
}

function Stepper({ current, overview, labelOf, docPath, onGo }: StepperProps) {
  return (
    <nav aria-label="Review steps" className="stepper">
      <ol>
        {WIZARD_STEPS.map((s, i) => {
          const state = overview?.steps.find((x) => x.id === s.id);
          const status = state?.status;
          const isCurrent = s.id === current;
          const onClick = (e: MouseEvent) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return; // let "open in new tab" work
            e.preventDefault();
            void onGo(s.id);
          };
          return (
            <li key={s.id} className={`stepper__item${isCurrent ? " is-current" : ""}${status ? ` is-${status}` : ""}`}>
              <Link
                to={`${docPath}/review/${s.id}`}
                aria-current={isCurrent ? "step" : undefined}
                onClick={onClick}
                className="stepper__link"
              >
                <span className="stepper__num" aria-hidden="true">
                  {status === "done" ? <Icon name="check" size={16} /> : i + 1}
                </span>
                <span className="stepper__text">
                  <span className="stepper__label">{labelOf(s.id)}</span>
                  {status && (
                    <span className="stepper__status">
                      <span className="visually-hidden">, </span>
                      {STATUS_TEXT[status]}
                      {status === "todo" && state && state.count > 0 && s.id !== "basics" ? ` (${state.count})` : ""}
                    </span>
                  )}
                </span>
              </Link>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
