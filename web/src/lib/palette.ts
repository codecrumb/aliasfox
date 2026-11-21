import type { SortMode } from './search';
import type { ImportPlan } from './transfer';
import type { Alias } from './types';

// Pure pieces of the palette screen, kept out of Palette.svelte.
export type Filter = 'all' | 'active' | 'inactive';
export type Dialog =
  | { kind: 'new' }
  | { kind: 'edit'; alias: Alias }
  | { kind: 'delete'; alias: Alias }
  | { kind: 'bulkDelete' }
  | { kind: 'menu' }
  | { kind: 'help' }
  | { kind: 'stats' }
  | { kind: 'import'; plan: ImportPlan }
  | { kind: 'detail' }
  | { kind: 'forward'; current: string }
  | { kind: 'disconnect' }
  | { kind: 'account' }
  | { kind: 'logout' }
  | { kind: 'copy'; text: string };

export const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'active', label: 'Active' },
  { id: 'inactive', label: 'Inactive' }
];
export const SORTS: { id: SortMode; label: string }[] = [
  { id: 'newest', label: 'Newest' },
  { id: 'oldest', label: 'Oldest' },
  { id: 'label', label: 'Label A–Z' },
  { id: 'address', label: 'Address A–Z' }
];

export const plural = (n: number): string => `${n.toLocaleString()} ${n === 1 ? 'address' : 'addresses'}`;

export function importSummary(p: ImportPlan): string {
  const parts = [`${p.unchanged} unchanged`];
  if (p.unknown) parts.push(`${p.unknown} not in your list`);
  if (p.inactive) parts.push(`${p.inactive} inactive (reactivate them first)`);
  return parts.join(', ') + '.';
}

/** Average position of the fingers on the screen. */
export function centre(touches: ArrayLike<{ clientX: number; clientY: number }>): { x: number; y: number } {
  let x = 0;
  let y = 0;
  for (const t of Array.from(touches)) {
    x += t.clientX;
    y += t.clientY;
  }
  return { x: x / touches.length, y: y / touches.length };
}

/** Index of the list row under a screen point, or -1. */
export function rowAt(x: number, y: number): number {
  const row = document.elementFromPoint(x, y)?.closest<HTMLElement>('[role="option"]');
  const i = row ? Number(row.dataset.index) : NaN;
  return Number.isInteger(i) ? i : -1;
}

/** Ids of the rows between two indexes, inclusive, in either order. */
export function idsBetween(results: Alias[], a: number, b: number): Set<string> {
  const ids = new Set<string>();
  for (let k = Math.min(a, b); k <= Math.max(a, b); k++) {
    const r = results[k];
    if (r) ids.add(r.anonymous_id);
  }
  return ids;
}
