import type { ReviewOverview, ReviewStep } from "../../api/types";

export type WizardStep = ReviewStep | "finish";

export const WIZARD_STEPS: { id: WizardStep; label: string }[] = [
  { id: "basics", label: "Title and language" },
  { id: "reading-order", label: "Reading order" },
  { id: "images", label: "Images" },
  { id: "tables", label: "Tables" },
  { id: "finish", label: "Finish" },
];

export function isWizardStep(value: string | undefined): value is WizardStep {
  return WIZARD_STEPS.some((s) => s.id === value);
}

/** What the wizard shell gives each step. */
export interface StepProps {
  docId: string;
  overview: ReviewOverview;
  /** The revision the client last saw. Send it with every change. */
  revision: number;
  /** Replace the overview (after metadata or "complete" calls). */
  setOverview: (overview: ReviewOverview) => void;
  /** Record the revision from any other mutation response. */
  setRevision: (revision: number) => void;
  /** Call on a 409: the shell reloads everything and tells the person. */
  onConflict: () => void;
  /** Go to the next step. */
  goNext: () => void;
  /**
   * Register a check that runs before leaving the step (for example to save
   * unsaved changes). Resolve false to stay on the step.
   */
  setLeaveGuard: (guard: (() => Promise<boolean>) | null) => void;
}
