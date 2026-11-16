import { get, writable } from 'svelte/store';
import { ApiError, api } from './api';
import { cache } from './cache';
import { importCacheKey } from './crypto';
import { sessionStore } from './session';
import { buildIndex, type Index } from './search';
import type { Alias } from './types';

export interface AliasState {
  list: Alias[];
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  fromCache: boolean;
  index: Index;
}

function initialState(): AliasState {
  return {
    list: [],
    loading: false,
    refreshing: false,
    error: null,
    fromCache: false,
    index: buildIndex([])
  };
}

const store = writable<AliasState>(initialState());

// The decrypted-capable key lives only here (non-extractable CryptoKey), never in a store.
let cryptoKey: CryptoKey | null = null;
// Bumped by clear(); an async step from an older generation drops its result.
let gen = 0;
// Bumped by every local list mutation; lets a fetch response detect that it is stale.
let mutSeq = 0;
let initPromise: Promise<void> | null = null;
let refreshPromise: Promise<void> | null = null;
let reservePromise: Promise<Alias> | null = null;

const UNEXPECTED = 'unexpected_response';

function setList(list: Alias[], extra: Partial<AliasState> = {}): void {
  store.update((s) => ({ ...s, ...extra, list, index: buildIndex(list) }));
}

function patchState(p: Partial<AliasState>): void {
  store.update((s) => ({ ...s, ...p }));
}

function listNow(): Alias[] {
  return get(store).list;
}

function detailOf(e: unknown): string {
  return e instanceof ApiError ? e.detail : 'network_error';
}

function fail(e: unknown): void {
  patchState({ error: detailOf(e) });
  if (e instanceof ApiError) sessionStore.onApiError(e);
}

async function persist(g: number): Promise<void> {
  const key = cryptoKey;
  if (key === null || g !== gen) return;
  await cache.save(key, listNow());
}

function validList(v: unknown): v is Alias[] {
  return Array.isArray(v) && v.every((a) => a !== null && typeof a === 'object' && 'anonymous_id' in a);
}

async function fetchAndApply(g: number, refresh: boolean): Promise<void> {
  patchState({ refreshing: true });
  try {
    let res: unknown;
    // A local mutation during the request makes the response stale: re-fetch once.
    for (let attempt = 0; attempt < 2; attempt++) {
      const seq = mutSeq;
      res = await api.listAliases(refresh);
      if (g !== gen) return;
      if (seq === mutSeq) break;
    }
    if (!validList(res)) {
      patchState({ error: UNEXPECTED });
      return;
    }
    setList(res, { fromCache: false, error: null });
    await persist(g);
  } catch (e) {
    if (g !== gen) return;
    fail(e);
  } finally {
    if (g === gen) patchState({ refreshing: false, loading: false });
  }
}

async function runInit(g: number): Promise<void> {
  const hadList = listNow().length > 0;
  patchState({ loading: !hadList, error: null });
  const ck = get(sessionStore).cacheKey;
  cryptoKey = null;
  if (ck !== null) {
    try {
      const k = await importCacheKey(ck);
      if (g !== gen) return;
      cryptoKey = k;
    } catch {
      if (g !== gen) return;
      cryptoKey = null; // unusable key: memory-only mode
    }
  }
  if (cryptoKey !== null) {
    const seq = mutSeq;
    const cached = await cache.load(cryptoKey);
    if (g !== gen) return;
    if (cached !== null && seq === mutSeq && listNow().length === 0) {
      setList(cached, { fromCache: true, loading: false });
    }
  }
  await fetchAndApply(g, false);
}

function init(): Promise<void> {
  if (initPromise !== null) return initPromise;
  const g = gen;
  const p: Promise<void> = runInit(g).finally(() => {
    if (initPromise === p) initPromise = null;
  });
  initPromise = p;
  return p;
}

function refresh(): Promise<void> {
  if (refreshPromise !== null) return refreshPromise;
  const g = gen;
  const p: Promise<void> = fetchAndApply(g, true).finally(() => {
    if (refreshPromise === p) refreshPromise = null;
  });
  refreshPromise = p;
  return p;
}

async function generate(): Promise<string> {
  const g = gen;
  try {
    const res = await api.generate();
    if (res === null || typeof res !== 'object' || typeof res.hme !== 'string') {
      throw new ApiError(0, UNEXPECTED);
    }
    return res.hme;
  } catch (e) {
    if (g === gen) fail(e);
    throw e;
  }
}

async function runReserve(g: number, b: { hme: string; label: string; note: string }): Promise<Alias> {
  try {
    const created = await api.reserve(b);
    if (g !== gen) return created;
    if (created === null || typeof created !== 'object' || typeof created.anonymous_id !== 'string') {
      throw new ApiError(0, UNEXPECTED);
    }
    mutSeq++;
    setList([created, ...listNow()], { error: null });
    await persist(g);
    return created;
  } catch (e) {
    if (g === gen) fail(e);
    throw e;
  }
}

function reserve(b: { hme: string; label: string; note: string }): Promise<Alias> {
  if (reservePromise !== null) return reservePromise;
  const p: Promise<Alias> = runReserve(gen, b).finally(() => {
    if (reservePromise === p) reservePromise = null;
  });
  reservePromise = p;
  return p;
}

// Applies `apply` to the list now, runs the API call, and on failure undoes exactly that change.
async function optimistic(
  apply: (list: Alias[]) => Alias[],
  call: () => Promise<unknown>,
  undo: (current: Alias[], previous: Alias[]) => Alias[]
): Promise<void> {
  const g = gen;
  const previous = listNow();
  const applied = apply(previous);
  mutSeq++;
  setList(applied, { error: null });
  try {
    await call();
  } catch (e) {
    if (g === gen) {
      mutSeq++;
      const current = listNow();
      // Untouched since we applied: restore the exact previous list. Otherwise revert only ours.
      setList(current === applied ? previous : undo(current, previous));
      fail(e);
    }
    throw e;
  }
  await persist(g);
}

function replaceById(list: Alias[], id: string, f: (a: Alias) => Alias): Alias[] {
  return list.map((a) => (a.anonymous_id === id ? f(a) : a));
}

function update(id: string, b: { label: string; note: string }): Promise<void> {
  return optimistic(
    (l) => replaceById(l, id, (a) => ({ ...a, label: b.label, note: b.note })),
    () => api.patch(id, { label: b.label, note: b.note }),
    (cur, prev) => {
      const old = prev.find((a) => a.anonymous_id === id);
      return old === undefined ? cur : replaceById(cur, id, (a) => ({ ...a, label: old.label, note: old.note }));
    }
  );
}

function setActive(id: string, active: boolean): Promise<void> {
  return optimistic(
    (l) => replaceById(l, id, (a) => ({ ...a, is_active: active })),
    () => api.setActive(id, active),
    (cur, prev) => {
      const old = prev.find((a) => a.anonymous_id === id);
      return old === undefined ? cur : replaceById(cur, id, (a) => ({ ...a, is_active: old.is_active }));
    }
  );
}

function remove(id: string): Promise<void> {
  return optimistic(
    (l) => l.filter((a) => a.anonymous_id !== id),
    () => api.remove(id),
    (cur, prev) => {
      const at = prev.findIndex((a) => a.anonymous_id === id);
      const removed = prev[at];
      if (removed === undefined || cur.some((a) => a.anonymous_id === id)) return cur;
      const next = cur.slice();
      next.splice(Math.min(at, next.length), 0, removed);
      return next;
    }
  );
}

async function setForwardTo(address: string): Promise<void> {
  const g = gen;
  try {
    await api.setForwardTo(address);
  } catch (e) {
    if (g === gen) fail(e);
    throw e;
  }
  if (g === gen) await refresh();
}

async function clear(): Promise<void> {
  gen++;
  mutSeq++;
  cryptoKey = null;
  initPromise = null;
  refreshPromise = null;
  reservePromise = null;
  store.set(initialState());
  await cache.clear();
}

export const aliasStore = {
  subscribe: store.subscribe,
  init,
  refresh,
  generate,
  reserve,
  update,
  setActive,
  remove,
  setForwardTo,
  clear
};

// Registered once per module instance; the unsubscribe is intentionally dropped.
sessionStore.onLogout(() => {
  void aliasStore.clear();
});
