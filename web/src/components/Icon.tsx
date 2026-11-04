// Inline SVG icons (stroke style, 24px grid). Always decorative: the text
// next to an icon carries the meaning, so icons are hidden from assistive tech.

const PATHS = {
  check: "M5 12.5l4.5 4.5L19 7.5",
  checkCircle: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM8 12.5l2.8 2.8L16.5 9.5",
  wrench:
    "M14.7 6.3a4 4 0 0 0-5.2 5.2L4 17v3h3l5.5-5.5a4 4 0 0 0 5.2-5.2l-2.5 2.5-2.4-.6-.6-2.4z",
  hand:
    "M8 13V5.5a1.5 1.5 0 0 1 3 0V11m0-6.5V4a1.5 1.5 0 0 1 3 0v7m0-5.5a1.5 1.5 0 0 1 3 0V12m0-3.5a1.5 1.5 0 0 1 3 0V15a6 6 0 0 1-6 6h-1.2a6 6 0 0 1-4.6-2.2L4.6 15.4a1.5 1.5 0 0 1 2.3-1.9L8 14.8",
  alert: "M12 3.5L2.5 20h19L12 3.5zM12 10v4.5M12 17.2v.3",
  alertCircle: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7.5v5.5M12 16.2v.3",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v5.5M12 7.8v.3",
  x: "M6 6l12 12M18 6L6 18",
  minus: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM8 12h8",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3.5 2",
  file: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5zM14 3v5h5",
  download: "M12 4v11m0 0l-4.5-4.5M12 15l4.5-4.5M5 19.5h14",
  upload: "M12 16V5m0 0L7.5 9.5M12 5l4.5 4.5M5 19.5h14",
  undo: "M9 14L4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11",
  refresh: "M20 11a8 8 0 0 0-14.8-3.5M4 4v4h4M4 13a8 8 0 0 0 14.8 3.5M20 20v-4h-4",
  arrowUp: "M12 19V5m0 0l-6 6m6-6l6 6",
  arrowDown: "M12 5v14m0 0l-6-6m6 6l6-6",
  arrowRight: "M5 12h14m0 0l-6-6m6 6l-6 6",
  arrowLeft: "M19 12H5m0 0l6-6m-6 6l6 6",
  chevronLeft: "M15 5l-7 7 7 7",
  chevronRight: "M9 5l7 7-7 7",
  external: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  globe: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z",
  sparkle: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3zM18.5 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z",
  trash: "M4 7h16M10 11v6M14 11v6M5.5 7l1 12a2 2 0 0 0 2 2h7a2 2 0 0 0 2-2l1-12M9 7V4h6v3",
  stop: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9.5h5v5h-5z",
  list: "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01",
  image: "M4 5h16v14H4zM4 16l4.5-4.5 3.5 3.5 2.5-2.5L20 18M15.5 9.5h.01",
  table: "M4 5h16v14H4zM4 10h16M4 14.5h16M10 5v14",
  text: "M5 6h14M5 10h14M5 14h10M5 18h7",
  shield: "M12 3l7.5 3v5.5c0 4.6-3.2 8.3-7.5 9.5-4.3-1.2-7.5-4.9-7.5-9.5V6L12 3zM8.5 12l2.5 2.5 4.5-5",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      className={className ? `icon ${className}` : "icon"}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
