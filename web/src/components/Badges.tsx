import type { Grade } from "../api/types";
import { GRADE_LABEL, GRADE_TONE, roundScore, type Tone } from "../lib/format";
import { Icon, type IconName } from "./Icon";

export const TONE_ICON: Record<Tone, IconName> = {
  ready: "checkCircle",
  auto: "wrench",
  help: "hand",
  error: "alertCircle",
  neutral: "minus",
  info: "clock",
};

/** A pill with an icon and text. Color is never the only signal. */
export function StatusBadge({ tone, label, icon }: { tone: Tone; label: string; icon?: IconName }) {
  return (
    <span className={`badge badge--${tone}`}>
      <Icon name={icon ?? TONE_ICON[tone]} size={16} />
      <span>{label}</span>
    </span>
  );
}

export function GradeBadge({ grade }: { grade: Grade }) {
  return <StatusBadge tone={GRADE_TONE[grade]} label={GRADE_LABEL[grade]} />;
}

interface ScoreProps {
  score: number | null;
  originalScore?: number | null;
  fixed?: boolean;
  grade?: Grade | null;
  size?: "sm" | "lg";
}

/** Score out of 100. Shows "before → after" when the file was fixed. */
export function ScoreBadge({ score, originalScore, fixed, grade, size = "sm" }: ScoreProps) {
  const now = roundScore(score);
  const was = roundScore(originalScore);
  const tone: Tone = grade ? GRADE_TONE[grade] : "neutral";
  const showWas = Boolean(fixed) && was != null && now != null && was !== now;

  if (now == null) {
    return (
      <span className={`score score--neutral score--${size}`}>
        <span aria-hidden="true">—</span>
        <span className="visually-hidden">No score yet</span>
      </span>
    );
  }

  const spoken = showWas ? `Score ${now} out of 100, was ${was}` : `Score ${now} out of 100`;
  return (
    <span className={`score score--${tone} score--${size}`}>
      <span className="visually-hidden">{spoken}</span>
      <span aria-hidden="true" className="score__visual">
        {showWas && (
          <>
            <span className="score__was">{was}</span>
            <Icon name="arrowRight" size={size === "lg" ? 22 : 14} className="score__arrow" />
          </>
        )}
        <span className="score__value">{now}</span>
      </span>
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner-wrap">
      <span className="spinner" aria-hidden="true" />
      {label && <span>{label}</span>}
    </span>
  );
}
