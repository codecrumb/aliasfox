// Test doubles shared by the palette tests. Not a test file (does not end in .test.ts).
import { vi } from 'vitest';
import { get, writable } from 'svelte/store';
import { buildIndex, type Index } from '../src/lib/search';
import type { Alias } from '../src/lib/types';

export function alias(i: number, extra: Partial<Alias> = {}): Alias {
  return {
    anonymous_id: `id-${i}`,
    hme: `addr${i}@icloud.com`,
    label: `Label ${i}`,
    note: null,
    is_active: true,
    forward_to: 'me@example.com',
    origin: 'ON_DEMAND',
    domain: null,
    created_ms: 1_700_000_000_000 + i * 1000,
    origin_app_name: null,
    app_bundle_id: null,
    ...extra
  };
}

export function many(n: number): Alias[] {
  return Array.from({ length: n }, (_, i) => alias(i));
}

export interface FakeAliasState {
  list: Alias[];
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  fromCache: boolean;
  index: Index;
}

export function createAliasStore() {
  const store = writable<FakeAliasState>({
    list: [],
    loading: false,
    refreshing: false,
    error: null,
    fromCache: false,
    index: buildIndex([])
  });
  const setList = (list: Alias[], extra: Partial<FakeAliasState> = {}) =>
    store.update((s) => ({ ...s, ...extra, list, index: buildIndex(list) }));
  const patch = (p: Partial<FakeAliasState>) => store.update((s) => ({ ...s, ...p }));
  const listNow = () => get(store).list;
  const replace = (id: string, f: (a: Alias) => Alias) =>
    setList(listNow().map((a) => (a.anonymous_id === id ? f(a) : a)));

  const api = {
    subscribe: store.subscribe,
    init: vi.fn(async () => {}),
    refresh: vi.fn(async () => {}),
    generate: vi.fn(async () => 'fresh@icloud.com'),
    reserve: vi.fn(async (b: { hme: string; label: string; note: string }) => {
      const created = alias(99999, {
        anonymous_id: 'new-id',
        hme: b.hme,
        label: b.label,
        note: b.note,
        created_ms: 1_900_000_000_000
      });
      setList([created, ...listNow()]);
      return created;
    }),
    update: vi.fn(async (id: string, b: { label: string; note: string }) => {
      replace(id, (a) => ({ ...a, label: b.label, note: b.note }));
    }),
    setActive: vi.fn(async (id: string, active: boolean) => {
      replace(id, (a) => ({ ...a, is_active: active }));
    }),
    remove: vi.fn(async (id: string) => {
      setList(listNow().filter((a) => a.anonymous_id !== id));
    }),
    setForwardTo: vi.fn(async () => {}),
    clear: vi.fn(async () => {
      setList([]);
    }),
    // test helpers
    __set: setList,
    __patch: patch,
    __get: () => get(store)
  };
  return api;
}

export function createSessionStore() {
  const store = writable({
    state: 'ready',
    authEnabled: true,
    mode: null as string | null,
    cacheKey: null as string | null,
    reason: null as string | null,
    errorText: null as string | null
  });
  return {
    subscribe: store.subscribe,
    boot: vi.fn(async () => {}),
    login: vi.fn(async () => {}),
    logout: vi.fn(async () => {}),
    onLogout: vi.fn(() => () => {}),
    onApiError: vi.fn(),
    markReady: vi.fn(async () => {
      store.update((s) => ({ ...s, state: 'ready', reason: null }));
    }),
    __set: (p: Record<string, unknown>) => store.update((s) => ({ ...s, ...p }))
  };
}
