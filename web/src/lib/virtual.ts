// Windowed list geometry. ROW_HEIGHT is the single source for the row height: Palette writes it
// into the --row-h CSS variable (CSSOM), and AliasRow's CSS reads that variable.

export const ROW_HEIGHT = 56;
export const OVERSCAN = 8;
export const MAX_ROWS = 60;
/** Used until the list has been measured (and in environments without layout). */
export const FALLBACK_VIEWPORT = 560;

export interface Window {
  start: number;
  end: number;
  top: number;
  bottom: number;
}

export function windowFor(scrollTop: number, viewport: number, total: number): Window {
  const vh = viewport > 0 ? viewport : FALLBACK_VIEWPORT;
  const first = Math.floor(Math.max(0, scrollTop) / ROW_HEIGHT);
  const start = Math.max(0, Math.min(first - OVERSCAN, total));
  let end = Math.min(total, Math.ceil((Math.max(0, scrollTop) + vh) / ROW_HEIGHT) + OVERSCAN);
  end = Math.min(end, start + MAX_ROWS);
  if (end < start) end = start;
  return { start, end, top: start * ROW_HEIGHT, bottom: (total - end) * ROW_HEIGHT };
}

/** The scrollTop that brings row `i` fully into view, or null if it already is. */
export function scrollToShow(i: number, scrollTop: number, viewport: number): number | null {
  const vh = viewport > 0 ? viewport : FALLBACK_VIEWPORT;
  const top = i * ROW_HEIGHT;
  const bottom = top + ROW_HEIGHT;
  if (top < scrollTop) return top;
  if (bottom > scrollTop + vh) return Math.max(0, bottom - vh);
  return null;
}

export function pageSize(viewport: number): number {
  const vh = viewport > 0 ? viewport : FALLBACK_VIEWPORT;
  return Math.max(1, Math.floor(vh / ROW_HEIGHT) - 1);
}
