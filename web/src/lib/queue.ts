import { writable } from 'svelte/store';
import { ApiError, api } from './api';
import { describeError, isRateLimited, isSessionError } from './errors';
import type { Alias } from './types';

// Changes made while offline: only ones that are safe to repeat (setting a value, never toggling).
// Delete and "new alias" are never queued: delete is permanent and a new alias needs Apple.
interface Fields {
  label: string | null;
  note: string | null;
}
export type QueuedOp =
  | { kind: 'active'; id: string; from: boolean; to: boolean }
  | { kind: 'patch'; id: string; from: Fields; to: Fields };

let ops: QueuedOp[] = [];
export const pending = writable(0);
export const online = writable(typeof navigator === 'undefined' || navigator.onLine !== false);
if (typeof window !== 'undefined') {
  window.addEventListener('online', () => online.set(true));
  window.addEventListener('offline', () => online.set(false));
}

const sync = (): void => pending.set(ops.length);
export const isOnline = (): boolean => typeof navigator === 'undefined' || navigator.onLine !== false;
export const isNetworkError = (e: unknown): boolean => e instanceof ApiError && e.detail === 'network_error';
export const size = (): number => ops.length;
export const snapshot = (): QueuedOp[] => ops.slice();

const same = (a: Fields, b: Fields): boolean => (a.label ?? '') === (b.label ?? '') && (a.note ?? '') === (b.note ?? '');
const atTarget = (a: Alias, op: QueuedOp): boolean =>
  op.kind === 'active' ? a.is_active === op.to : same(a, op.to);
const atBase = (a: Alias, op: QueuedOp): boolean =>
  op.kind === 'active' ? a.is_active === op.from : same(a, op.from);

/** Latest change per alias and kind wins; one that lands back on its starting value cancels out. */
export function add(op: QueuedOp): void {
  const i = ops.findIndex((o) => o.kind === op.kind && o.id === op.id);
  const merged = i < 0 ? op : ({ ...op, from: ops[i]!.from } as QueuedOp);
  ops = ops.filter((_, j) => j !== i);
  const back = merged.kind === 'active' ? merged.from === merged.to : same(merged.from, merged.to);
  if (!back) ops.push(merged);
  sync();
}

/** A change just went through online, so an older waiting one for the same alias and kind is
 * obsolete: replaying it later would overwrite what the user did last. */
export function supersede(op: QueuedOp): void {
  const n = ops.length;
  ops = ops.filter((o) => !(o.kind === op.kind && o.id === op.id));
  if (ops.length !== n) sync();
}

export function restore(saved: unknown): void {
  ops = Array.isArray(saved)
    ? saved.filter((o): o is QueuedOp => o !== null && typeof o === 'object' && typeof (o as QueuedOp).id === 'string' && ((o as QueuedOp).kind === 'active' || (o as QueuedOp).kind === 'patch'))
    : [];
  sync();
}

/** Forgets everything (logout); returns how many changes were dropped so the caller can say so. */
export function reset(): number {
  const n = ops.length;
  ops = [];
  sync();
  return n;
}

/** The list as the user sees it: server state with the still-waiting changes laid on top. */
export function overlay(list: Alias[]): Alias[] {
  if (ops.length === 0) return list;
  return list.map((a) =>
    ops.reduce<Alias>(
      (acc, o) =>
        o.id !== a.anonymous_id ? acc : o.kind === 'active' ? { ...acc, is_active: o.to } : { ...acc, ...o.to },
      a
    )
  );
}

export interface FlushResult {
  list: Alias[];
  notes: string[];
  applied: number;
  error: unknown;
}

/** Replays the queue against the fresh server list. A change whose alias moved on elsewhere is
 * reported, never applied over it. Stops (keeping the rest) on network, rate-limit or session errors. */
export async function flush(server: Alias[]): Promise<FlushResult> {
  let list = server;
  const notes: string[] = [];
  let applied = 0;
  let error: unknown = null;
  for (const op of ops.slice()) {
    const drop = (): void => {
      ops = ops.filter((o) => o !== op);
    };
    const cur = list.find((a) => a.anonymous_id === op.id);
    if (cur === undefined) {
      notes.push('An alias you changed offline no longer exists.');
      drop();
    } else if (atTarget(cur, op)) {
      drop();
    } else if (!atBase(cur, op)) {
      notes.push(`${cur.hme} changed elsewhere, so your offline change to it was not applied.`);
      drop();
    } else {
      try {
        if (op.kind === 'active') await api.setActive(op.id, op.to);
        else await api.patch(op.id, { label: op.to.label ?? '', note: op.to.note ?? '' });
        list = list.map((a) => (a === cur ? overlay1(a, op) : a));
        applied++;
        drop();
      } catch (e) {
        if (isNetworkError(e) || isRateLimited(e) || isSessionError(e)) {
          error = e;
          break;
        }
        notes.push(`Couldn't sync ${cur.hme}. ${describeError(e)}`);
        drop();
      }
    }
  }
  sync();
  return { list, notes, applied, error };
}

const overlay1 = (a: Alias, op: QueuedOp): Alias =>
  op.kind === 'active' ? { ...a, is_active: op.to } : { ...a, ...op.to };
