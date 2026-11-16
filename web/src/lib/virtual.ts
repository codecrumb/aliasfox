// Windowed list geometry. ROW_HEIGHT is the single source for the row height: Palette writes it
// into the --row-h CSS variable (CSSOM), and AliasRow's CSS reads that variable.

export const ROW_HEIGHT = 56;
export const OVERSCAN = 8;
/**
 * Hard cap on rendered rows. With OVERSCAN on both sides this covers a viewport of up to
 * (MAX_ROWS - 2 * OVERSCAN) * ROW_HEIGHT = 44 * 56 = 2464 px of list height; a taller list would
 * show blank space below the last rendered row.
 */
export const MAX_ROWS = 60;
/** Padding inside the scrolling list (top and bottom); written to --list-pad from here. */
export const LIST_PADDING = 4;
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
  const first = Math.floor(Math.max(0, scrollTop - LIST_PADDING) / ROW_HEIGHT);
  const start = Math.max(0, Math.min(first - OVERSCAN, total));
  let end = Math.min(total, Math.ceil((Math.max(0, scrollTop) + vh) / ROW_HEIGHT) + OVERSCAN);
  end = Math.min(end, start + MAX_ROWS);
  if (end < start) end = start;
  return { start, end, top: start * ROW_HEIGHT, bottom: (total - end) * ROW_HEIGHT };
}

/** The scrollTop that brings row `i` fully into view, or null if it already is. */
export function scrollToShow(i: number, scrollTop: number, viewport: number): number | null {
  const vh = viewport > 0 ? viewport : FALLBACK_VIEWPORT;
  // Rows start after the list's top padding; keep that padding visible around the first and last.
  const top = LIST_PADDING + i * ROW_HEIGHT;
  const bottom = top + ROW_HEIGHT;
  if (top < scrollTop) return Math.max(0, top - LIST_PADDING);
  if (bottom > scrollTop + vh) return Math.max(0, bottom + LIST_PADDING - vh);
  return null;
}

export function pageSize(viewport: number): number {
  const vh = viewport > 0 ? viewport : FALLBACK_VIEWPORT;
  return Math.max(1, Math.floor(vh / ROW_HEIGHT) - 1);
}
