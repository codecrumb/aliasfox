import { get, writable } from 'svelte/store';
import { ApiError, api } from './api';
import { cache } from './cache';
import { importCacheKey } from './crypto';
import { sessionStore } from './session';
import { buildIndex, type Index } from './search';
import * as queue from './queue';
import { toasts } from './toasts';
import type { Alias } from './types';

export interface AliasState {
  list: Alias[];
  loading: boolean;
  refreshing: boolean;
  /** Last error of any kind (kept for compatibility). */
  error: string | null;
  /** Only list-load failures: drives the palette's "Couldn't load" state. Mutation failures are
   * reported by the caller (toast or dialog) and never set this. */
  loadError: string | null;
  fromCache: boolean;
  index: Index;
}

function initialState(): AliasState {
  return {
    list: [],
    loading: false,
    refreshing: false,
    error: null,
    loadError: null,
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
const reserves = new Map<string, Promise<Alias | null>>();
let generatePromise: Promise<string> | null = null;
// An address generated ahead of the New alias dialog, so it opens with one ready.
const SPARE_TTL_MS = 5 * 60 * 1000; // shortcut: Apple's expiry for a generated address is unknown (unverified); stay short
let spare: { p: Promise<string>; at: number } | null = null;
// Mutations currently awaiting their API call, plus waiters released when none are pending.
let pendingMutations = 0;
let idleWaiters: Array<() => void> = [];
let refetchPromise: Promise<FetchOutcome> | null = null;
const MAX_REFETCHES = 2;

type FetchOutcome = 'applied' | 'stale' | 'gone' | 'kept';

function mutationStart(): void {
  pendingMutations++;
  mutSeq++;
}

function mutationEnd(g: number): void {
  if (g !== gen) return; // clear() already reset the counters
  pendingMutations--;
  mutSeq++;
  if (pendingMutations === 0) wakeIdle();
}

function wakeIdle(): void {
  const w = idleWaiters;
  idleWaiters = [];
  for (const f of w) f();
}

function whenIdle(): Promise<void> {
  if (pendingMutations === 0) return Promise.resolve();
  return new Promise<void>((resolve) => idleWaiters.push(resolve));
}

const UNEXPECTED = 'unexpected_response';

function setList(list: Alias[], extra: Partial<AliasState> = {}): void {
  store.update((s) => ({
    ...s,
    // A list that goes from empty to non-empty has plainly loaded: a stale load error must not
    // come back later (e.g. create, then delete, with no reload in between).
    ...(s.list.length === 0 && list.length > 0 ? { loadError: null } : {}),
    ...extra,
    list,
    index: buildIndex(list)
  }));
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

function fail(e: unknown, load = false): void {
  const d = detailOf(e);
  patchState(load ? { error: d, loadError: d } : { error: d });
  if (e instanceof ApiError) sessionStore.onApiError(e);
}

async function persist(g: number): Promise<void> {
  const key = cryptoKey;
  if (key === null || g !== gen) return;
  await cache.save(key, listNow()); // cache.save itself aborts if clear() ran meanwhile
}

function validList(v: unknown): v is Alias[] {
  return Array.isArray(v) && v.every((a) => a !== null && typeof a === 'object' && 'anonymous_id' in a);
}

// One list request. The response is applied only if no mutation is pending and none started or
// settled since the request began; otherwise it is stale and dropped.
async function attempt(g: number, refresh: boolean): Promise<FetchOutcome> {
  const seq = mutSeq;
  const res: unknown = await api.listAliases(refresh);
  if (g !== gen) return 'gone';
  if (pendingMutations > 0 || seq !== mutSeq) return 'stale';
  if (!validList(res)) {
    patchState({ error: UNEXPECTED, loadError: UNEXPECTED });
    return 'applied';
  }
  if (queue.size() === 0) {
    setList(res, { fromCache: false, error: null, loadError: null });
    await persist(g);
    return 'applied';
  }
  // Offline changes are waiting: replay them against this fresh list before showing it.
  mutationStart();
  const s0 = mutSeq;
  let out: queue.FlushResult;
  try {
    out = await queue.flush(res);
  } finally {
    const moved = mutSeq !== s0;
    mutationEnd(g);
    if (moved) staleFlush = true;
  }
  if (g !== gen) return 'gone';
  reportFlush(out);
  await persistQueue(g);
  if (staleFlush) {
    staleFlush = false;
    return 'stale';
  }
  setList(queue.overlay(out.list), { fromCache: false, error: null, loadError: null });
  await persist(g);
  return 'applied';
}

let staleFlush = false;

function reportFlush(out: queue.FlushResult): void {
  if (out.error instanceof ApiError) sessionStore.onApiError(out.error);
  if (out.applied > 0) {
    toasts.push({ kind: 'ok', text: `Synced ${out.applied} offline ${out.applied === 1 ? 'change' : 'changes'}` });
  }
  if (out.notes.length > 0) {
    const more = out.notes.length > 1 ? ` (+${out.notes.length - 1} more)` : '';
    toasts.push({ kind: 'error', text: out.notes[0]! + more });
  }
}

async function persistQueue(g: number): Promise<void> {
  const key = cryptoKey;
  if (key === null || g !== gen) return;
  await cache.saveQueue(key, queue.snapshot());
}

// A single queued re-fetch after pending mutations settle. refresh=true bypasses the server's
// cached list; a rate limit keeps the optimistic state quietly.
function refetch(g: number): Promise<FetchOutcome> {
  if (refetchPromise !== null) return refetchPromise;
  const p: Promise<FetchOutcome> = (async (): Promise<FetchOutcome> => {
    await whenIdle();
    if (g !== gen) return 'gone';
    try {
      return await attempt(g, true);
    } catch (e) {
      if (g !== gen) return 'gone';
      if (e instanceof ApiError && e.detail === 'rate_limited') return 'kept';
      throw e;
    }
  })().finally(() => {
    if (refetchPromise === p) refetchPromise = null;
  });
  refetchPromise = p;
  return p;
}

async function fetchAndApply(g: number, refresh: boolean): Promise<void> {
  patchState({ refreshing: true });
  try {
    let out = await attempt(g, refresh);
    for (let n = 0; out === 'stale' && n < MAX_REFETCHES; n++) out = await refetch(g);
  } catch (e) {
    if (g !== gen) return;
    fail(e, true);
  } finally {
    if (g === gen) patchState({ refreshing: false, loading: false });
  }
}

async function runInit(g: number): Promise<void> {
  const hadList = listNow().length > 0;
  patchState({ loading: !hadList, error: null, loadError: null });
  const ck = get(sessionStore).cacheKey;
  // No fresh key (it was already imported and dropped from the session store): keep the imported
  // CryptoKey. clear() (logout, not_authenticated) is what discards it.
  if (ck !== null) {
    cryptoKey = null;
    try {
      const k = await importCacheKey(ck);
      if (g !== gen) return;
      cryptoKey = k;
      sessionStore.dropCacheKey(); // the base64 string must not linger in a store
    } catch {
      if (g !== gen) return;
      cryptoKey = null; // unusable key: memory-only mode
    }
  }
  if (cryptoKey !== null) {
    const seq = mutSeq;
    const cached = await cache.load(cryptoKey);
    if (g !== gen) return;
    if (queue.size() === 0) queue.restore(await cache.loadQueue(cryptoKey));
    if (g !== gen) return;
    if (cached !== null && seq === mutSeq && listNow().length === 0) {
      setList(cached, { fromCache: true, loading: false });
    }
  }
  await fetchAndApply(g, false);
  if (g === gen) warm();
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

async function runGenerate(g: number, quiet: boolean): Promise<string> {
  try {
    const res = await api.generate();
    if (res === null || typeof res !== 'object' || typeof res.hme !== 'string') {
      throw new ApiError(0, UNEXPECTED);
    }
    return res.hme;
  } catch (e) {
    if (g === gen && !quiet) fail(e);
    throw e;
  }
}

function generateNow(quiet = false): Promise<string> {
  if (generatePromise !== null) return generatePromise;
  const p: Promise<string> = runGenerate(gen, quiet).finally(() => {
    if (generatePromise === p) generatePromise = null;
  });
  generatePromise = p;
  return p;
}

// Starts generating a spare address in the background; a failure here is silent.
function warm(): void {
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return;
  if (spare !== null && Date.now() - spare.at < SPARE_TTL_MS) return;
  const s = { p: generateNow(true), at: Date.now() };
  s.p.catch(() => {
    if (spare === s) spare = null;
  });
  spare = s;
}

// Uses the spare when there is a fresh one (`fresh` skips it); otherwise asks Apple now.
function generate(fresh = false): Promise<string> {
  const s = spare;
  spare = null;
  if (fresh || s === null || Date.now() - s.at >= SPARE_TTL_MS) return generateNow();
  return s.p.catch(() => generateNow()); // the quiet attempt failed: try again out loud
}

function validAlias(v: unknown): v is Alias {
  return v !== null && typeof v === 'object' && typeof (v as Alias).anonymous_id === 'string' && typeof (v as Alias).hme === 'string';
}

// Resolves the created alias, or null when the store was cleared (logout/disconnect) while the
// request was in flight: the result belongs to a session that no longer exists.
async function runReserve(g: number, b: { hme: string; label: string; note: string }): Promise<Alias | null> {
  mutationStart();
  try {
    const created: unknown = await api.reserve(b);
    if (g !== gen) return null;
    if (!validAlias(created)) {
      throw new ApiError(0, UNEXPECTED);
    }
    // Newest-first sorting puts an alias without a timestamp last; a just-created one is the newest.
    // Apple can also answer with a zero timestamp (1970), which is no timestamp at all.
    const stamped: Alias =
      typeof created.created_ms === 'number' && created.created_ms > 0 ? created : { ...created, created_ms: Date.now() };
    setList([stamped, ...listNow()], { error: null, loadError: null });
    await persist(g);
    warm(); // the next New alias opens with an address ready
    return stamped;
  } catch (e) {
    if (g === gen) fail(e);
    throw e;
  } finally {
    mutationEnd(g);
  }
}

// Guarded per hme: a double trigger for the same address shares one request, while a different
// address runs its own.
function reserve(b: { hme: string; label: string; note: string }): Promise<Alias | null> {
  const existing = reserves.get(b.hme);
  if (existing !== undefined) return existing;
  const p: Promise<Alias | null> = runReserve(gen, b).finally(() => {
    if (reserves.get(b.hme) === p) reserves.delete(b.hme);
  });
  reserves.set(b.hme, p);
  return p;
}

// Applies `apply` to the list now, runs the API call, and on failure undoes exactly that change.
async function optimistic(
  apply: (list: Alias[]) => Alias[],
  call: () => Promise<unknown>,
  undo: (current: Alias[], previous: Alias[]) => Alias[],
  // Set only for changes that are safe to keep and repeat later when the network is down.
  offlineOp?: (previous: Alias[]) => queue.QueuedOp | null
): Promise<void> {
  const g = gen;
  const previous = listNow();
  const applied = apply(previous);
  mutationStart();
  setList(applied, { error: null });
  try {
    if (offlineOp && !queue.isOnline()) throw new ApiError(0, 'network_error');
    await call();
    const sent = offlineOp?.(previous) ?? null;
    if (sent !== null && g === gen) {
      const before = queue.size();
      queue.supersede(sent);
      if (queue.size() !== before) await persistQueue(g);
    }
  } catch (e) {
    const op = offlineOp && queue.isNetworkError(e) ? offlineOp(previous) : null;
    if (op !== null && g === gen) {
      // Keep the change on screen and send it when the connection is back.
      queue.add(op);
      await persistQueue(g);
      await persist(g);
      return;
    }
    if (g === gen) {
      const current = listNow();
      // Untouched since we applied: restore the exact previous list. Otherwise revert only ours.
      setList(current === applied ? previous : undo(current, previous));
      fail(e);
    }
    throw e;
  } finally {
    mutationEnd(g);
  }
  if (g === gen) patchState({ loadError: null }); // a successful mutation proves the API works
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
    },
    (prev) => {
      const old = prev.find((a) => a.anonymous_id === id);
      return old === undefined
        ? null
        : { kind: 'patch', id, from: { label: old.label, note: old.note }, to: { label: b.label, note: b.note } };
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
    },
    (prev) => {
      const old = prev.find((a) => a.anonymous_id === id);
      return old === undefined ? null : { kind: 'active', id, from: old.is_active, to: active };
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
  reserves.clear();
  generatePromise = null;
  spare = null;
  refetchPromise = null;
  pendingMutations = 0;
  wakeIdle();
  store.set(initialState());
  const dropped = queue.reset();
  if (dropped > 0) {
    toasts.push({ kind: 'error', text: `${dropped} offline ${dropped === 1 ? 'change was' : 'changes were'} not synced and discarded.` });
  }
  await cache.clear();
}

export const aliasStore = {
  subscribe: store.subscribe,
  init,
  refresh,
  generate,
  warm,
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

// Back online: refresh, which also replays any waiting offline changes.
if (typeof window !== 'undefined') {
  window.addEventListener('online', () => {
    if (queue.size() > 0) void aliasStore.refresh();
  });
}
