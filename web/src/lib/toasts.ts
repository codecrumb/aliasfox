import { writable } from 'svelte/store';

export type ToastKind = 'ok' | 'error';
export interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
  action?: ToastAction;
}
export interface ToastAction {
  label: string;
  run: () => void;
}

const MAX_VISIBLE = 4;
const TTL: Record<ToastKind, number> = { ok: 4000, error: 7000 };
const ACTION_TTL = 8000; // long enough to reach for an Undo

const store = writable<Toast[]>([]);
const timers = new Map<number, ReturnType<typeof setTimeout>>();
let nextId = 1;

function forget(id: number): void {
  const t = timers.get(id);
  if (t !== undefined) clearTimeout(t);
  timers.delete(id);
}

function dismiss(id: number): void {
  forget(id);
  store.update((list) => list.filter((t) => t.id !== id));
}

function push(t: { kind: ToastKind; text: string; action?: ToastAction }): number {
  const id = nextId++;
  store.update((list) => {
    const next = [...list, { id, kind: t.kind, text: t.text, action: t.action }];
    while (next.length > MAX_VISIBLE) {
      const dropped = next.shift();
      if (dropped) forget(dropped.id);
    }
    return next;
  });
  timers.set(
    id,
    setTimeout(() => dismiss(id), t.action ? ACTION_TTL : TTL[t.kind])
  );
  return id;
}

export const toasts = { subscribe: store.subscribe, push, dismiss };
