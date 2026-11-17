import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get, writable } from 'svelte/store';
import type { Alias } from '../src/lib/types';

const h = vi.hoisted(() => ({
  api: {
    listAliases: vi.fn(),
    generate: vi.fn(),
    reserve: vi.fn(),
    patch: vi.fn(),
    setActive: vi.fn(),
    remove: vi.fn(),
    setForwardTo: vi.fn()
  },
  onApiError: vi.fn(),
  onLogout: vi.fn(),
  logoutCbs: [] as Array<() => void>,
  encryptGate: null as Promise<void> | null
}));

vi.mock('../src/lib/crypto', async () => {
  const actual = await vi.importActual<typeof import('../src/lib/crypto')>('../src/lib/crypto');
  return {
    ...actual,
    encryptJson: async (k: CryptoKey, v: unknown) => {
      const gate = h.encryptGate;
      const r = await actual.encryptJson(k, v);
      if (gate) await gate;
      return r;
    }
  };
});

vi.mock('../src/lib/api', async () => {
  const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
  return { ...actual, api: h.api };
});

const sessionState = writable<{ cacheKey: string | null }>({ cacheKey: null });
vi.mock('../src/lib/session', () => ({
  sessionStore: {
    subscribe: (fn: (v: { cacheKey: string | null }) => void) => sessionState.subscribe(fn),
    onApiError: h.onApiError,
    dropCacheKey: () => sessionState.update((s) => ({ ...s, cacheKey: null })),
    onLogout: (cb: () => void) => {
      h.onLogout(cb);
      h.logoutCbs.push(cb);
      return () => {};
    }
  }
}));

function b64url(bytes: Uint8Array): string {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
const KEY = b64url(new Uint8Array(32).fill(3));

function mk(n: number, over: Partial<Alias> = {}): Alias {
  return {
    anonymous_id: `id${n}`,
    hme: `a${n}@icloud.com`,
    label: `label${n}`,
    note: null,
    is_active: true,
    forward_to: null,
    origin: null,
    domain: null,
    created_ms: 1000 - n,
    origin_app_name: null,
    app_bundle_id: null,
    ...over
  };
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const tick = () => new Promise<void>((r) => setTimeout(r, 0));
async function until(cond: () => boolean): Promise<void> {
  for (let i = 0; i < 200 && !cond(); i++) await tick();
  expect(cond()).toBe(true);
}

type Mods = {
  aliasStore: typeof import('../src/lib/aliases').aliasStore;
  cache: typeof import('../src/lib/cache').cache;
  crypto: typeof import('../src/lib/crypto');
  ApiError: typeof import('../src/lib/api').ApiError;
};

async function fresh(): Promise<Mods> {
  vi.resetModules();
  const { aliasStore } = await import('../src/lib/aliases');
  const { cache } = await import('../src/lib/cache');
  const crypto = await import('../src/lib/crypto');
  const { ApiError } = await import('../src/lib/api');
  return { aliasStore, cache, crypto, ApiError };
}

let m: Mods;
let consoleSpies: ReturnType<typeof vi.spyOn>[] = [];

beforeEach(async () => {
  for (const f of Object.values(h.api)) f.mockReset();
  h.onApiError.mockReset();
  h.onLogout.mockReset();
  h.logoutCbs.length = 0;
  h.encryptGate = null;
  sessionState.set({ cacheKey: KEY });
  consoleSpies = (['log', 'info', 'warn', 'error', 'debug'] as const).map((x) =>
    vi.spyOn(console, x).mockImplementation(() => {})
  );
  const { cache } = await import('../src/lib/cache');
  await cache.clear();
  m = await fresh();
});
afterEach(() => {
  for (const s of consoleSpies) expect(s).not.toHaveBeenCalled();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function seedCache(list: Alias[]): Promise<void> {
  const key = await m.crypto.importCacheKey(KEY);
  await m.cache.save(key, list);
}

async function loaded(list: Alias[]): Promise<void> {
  h.api.listAliases.mockResolvedValue(list);
  await m.aliasStore.init();
  expect(get(m.aliasStore).list).toEqual(list);
}

describe('aliasStore init/refresh', () => {
  it('test_store_shows_cache_then_fresh_data', async () => {
    const old = [mk(1), mk(2)];
    const fresher = [mk(3), mk(4), mk(5)];
    await seedCache(old);
    const d = deferred<Alias[]>();
    h.api.listAliases.mockReturnValue(d.promise);
    const p = m.aliasStore.init();
    await until(() => get(m.aliasStore).list.length === 2);
    let s = get(m.aliasStore);
    expect(s.fromCache).toBe(true);
    expect(s.loading).toBe(false);
    expect(s.refreshing).toBe(true);
    expect(s.list).toEqual(old);
    d.resolve(fresher);
    await p;
    s = get(m.aliasStore);
    expect(s.list).toEqual(fresher);
    expect(s.fromCache).toBe(false);
    expect(s.refreshing).toBe(false);
    expect(s.error).toBeNull();
    const key = await m.crypto.importCacheKey(KEY);
    expect(await m.cache.load(key)).toEqual(fresher);
    expect(h.api.listAliases).toHaveBeenCalledTimes(1);
    expect(h.api.listAliases).toHaveBeenCalledWith(false);
  });

  it('index is rebuilt when the list changes', async () => {
    await loaded([mk(1, { label: 'Zebra stripes' })]);
    const idx = get(m.aliasStore).index;
    expect(idx.aliases).toHaveLength(1);
    h.api.remove.mockResolvedValue({ ok: true });
    await m.aliasStore.remove('id1');
    expect(get(m.aliasStore).index.aliases).toHaveLength(0);
  });

  it('no cacheKey means no IndexedDB access at all', async () => {
    sessionState.set({ cacheKey: null });
    const open = vi.spyOn(indexedDB, 'open');
    await loaded([mk(1)]);
    h.api.reserve.mockResolvedValue(mk(9));
    await m.aliasStore.reserve({ hme: 'a', label: 'b', note: '' });
    expect(open).not.toHaveBeenCalled();
  });

  it('works when indexedDB is undefined', async () => {
    vi.stubGlobal('indexedDB', undefined);
    await loaded([mk(1)]);
    expect(get(m.aliasStore).list).toHaveLength(1);
  });

  it('second init while one is in flight is a no-op', async () => {
    const d = deferred<Alias[]>();
    h.api.listAliases.mockReturnValue(d.promise);
    const p1 = m.aliasStore.init();
    const p2 = m.aliasStore.init();
    d.resolve([mk(1)]);
    await Promise.all([p1, p2]);
    expect(h.api.listAliases).toHaveBeenCalledTimes(1);
    expect(get(m.aliasStore).list).toHaveLength(1);
  });

  it('refresh uses refresh=true and init uses false', async () => {
    await loaded([mk(1)]);
    expect(h.api.listAliases).toHaveBeenLastCalledWith(false);
    h.api.listAliases.mockResolvedValue([mk(2)]);
    await m.aliasStore.refresh();
    expect(h.api.listAliases).toHaveBeenLastCalledWith(true);
    expect(get(m.aliasStore).list).toEqual([mk(2)]);
  });

  it('fetch failure keeps stale list, sets error and reports to the session', async () => {
    const old = [mk(1)];
    await loaded(old);
    const err = new m.ApiError(401, 'session_expired');
    h.api.listAliases.mockRejectedValue(err);
    await m.aliasStore.refresh();
    const s = get(m.aliasStore);
    expect(s.list).toEqual(old);
    expect(s.error).toBe('session_expired');
    expect(s.refreshing).toBe(false);
    expect(h.onApiError).toHaveBeenCalledWith(err);
  });

  it('late init response after clear() is ignored', async () => {
    const d = deferred<Alias[]>();
    h.api.listAliases.mockReturnValue(d.promise);
    const p = m.aliasStore.init();
    await tick();
    await m.aliasStore.clear();
    d.resolve([mk(1)]);
    await p;
    expect(get(m.aliasStore).list).toEqual([]);
    const key = await m.crypto.importCacheKey(KEY);
    expect(await m.cache.load(key)).toBeNull();
    // a new init after clear works again
    h.api.listAliases.mockResolvedValue([mk(2)]);
    await m.aliasStore.init();
    expect(get(m.aliasStore).list).toEqual([mk(2)]);
  });

  it('a mutation during a pending init response forces one re-fetch', async () => {
    const all = [mk(1), mk(2), mk(3)];
    await seedCache(all);
    const d1 = deferred<Alias[]>();
    const d2 = deferred<Alias[]>();
    h.api.listAliases.mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);
    h.api.remove.mockResolvedValue({ ok: true });
    const p = m.aliasStore.init();
    await until(() => get(m.aliasStore).list.length === 3);
    await m.aliasStore.remove('id2');
    expect(get(m.aliasStore).list.map((a) => a.anonymous_id)).toEqual(['id1', 'id3']);
    d1.resolve(all); // stale: still contains id2
    await until(() => h.api.listAliases.mock.calls.length === 2);
    expect(get(m.aliasStore).list.map((a) => a.anonymous_id)).toEqual(['id1', 'id3']);
    d2.resolve([mk(1), mk(3)]);
    await p;
    expect(get(m.aliasStore).list.map((a) => a.anonymous_id)).toEqual(['id1', 'id3']);
    expect(get(m.aliasStore).refreshing).toBe(false);
  });
});

describe('aliasStore actions', () => {
  it('generate returns the address and leaves the list alone', async () => {
    await loaded([mk(1)]);
    h.api.generate.mockResolvedValue({ hme: 'new@icloud.com' });
    expect(await m.aliasStore.generate()).toBe('new@icloud.com');
    expect(get(m.aliasStore).list).toHaveLength(1);
  });

  it('a created alias without a timestamp still sorts first', async () => {
    await loaded([mk(1, { created_ms: 1000 })]);
    h.api.reserve.mockResolvedValue(mk(9, { hme: 'n@icloud.com', created_ms: null }));
    const a = await m.aliasStore.reserve({ hme: 'n@icloud.com', label: 'L', note: '' });
    expect(typeof a?.created_ms).toBe('number');
    expect(get(m.aliasStore).index.newestFirst.map((x) => x.anonymous_id)).toEqual(['id9', 'id1']);
  });

  it('test_double_reserve_sends_one_request', async () => {
    await loaded([mk(1)]);
    const d = deferred<Alias>();
    h.api.reserve.mockReturnValue(d.promise);
    const p1 = m.aliasStore.reserve({ hme: 'n@icloud.com', label: 'L', note: '' });
    const p2 = m.aliasStore.reserve({ hme: 'n@icloud.com', label: 'L', note: '' });
    expect(p2).toBe(p1);
    d.resolve(mk(9, { hme: 'n@icloud.com', created_ms: 5000 }));
    await Promise.all([p1, p2]);
    expect(h.api.reserve).toHaveBeenCalledTimes(1);
    expect(get(m.aliasStore).list.map((a) => a.anonymous_id)).toEqual(['id9', 'id1']);
    // guard released afterwards
    h.api.reserve.mockResolvedValue(mk(10));
    await m.aliasStore.reserve({ hme: 'x', label: 'y', note: '' });
    expect(h.api.reserve).toHaveBeenCalledTimes(2);
    const key = await m.crypto.importCacheKey(KEY);
    expect((await m.cache.load(key))?.map((a) => a.anonymous_id)).toEqual(['id10', 'id9', 'id1']);
  });

  it('reserve failure leaves list, sets error, reports, rethrows and releases the guard', async () => {
    await loaded([mk(1)]);
    const err = new m.ApiError(429, 'rate_limited');
    h.api.reserve.mockRejectedValueOnce(err);
    await expect(m.aliasStore.reserve({ hme: 'x', label: 'y', note: '' })).rejects.toBe(err);
    expect(get(m.aliasStore).list).toHaveLength(1);
    expect(get(m.aliasStore).error).toBe('rate_limited');
    expect(h.onApiError).toHaveBeenCalledWith(err);
    h.api.reserve.mockResolvedValueOnce(mk(2));
    await m.aliasStore.reserve({ hme: 'x', label: 'y', note: '' });
    expect(get(m.aliasStore).list).toHaveLength(2);
  });

  it('test_optimistic_toggle_rolls_back_on_error', async () => {
    const list = [mk(1), mk(2)];
    await loaded(list);
    const d = deferred<{ ok: boolean }>();
    h.api.setActive.mockReturnValue(d.promise);
    const p = m.aliasStore.setActive('id1', false);
    expect(get(m.aliasStore).list[0]?.is_active).toBe(false); // applied before the API answers
    const err = new m.ApiError(502, 'apple_error');
    d.reject(err);
    await expect(p).rejects.toBe(err);
    expect(get(m.aliasStore).list).toEqual(list);
    expect(get(m.aliasStore).error).toBe('apple_error');
    expect(h.onApiError).toHaveBeenCalledWith(err);
    expect(h.api.setActive).toHaveBeenCalledWith('id1', false);
  });

  it('setActive success keeps the change and persists it', async () => {
    await loaded([mk(1)]);
    h.api.setActive.mockResolvedValue({ ok: true });
    await m.aliasStore.setActive('id1', false);
    expect(get(m.aliasStore).list[0]?.is_active).toBe(false);
    const key = await m.crypto.importCacheKey(KEY);
    expect((await m.cache.load(key))?.[0]?.is_active).toBe(false);
  });

  it('optimistic remove rollback restores order', async () => {
    const list = [mk(1), mk(2), mk(3)];
    await loaded(list);
    const err = new m.ApiError(0, 'network_error');
    h.api.remove.mockRejectedValue(err);
    await expect(m.aliasStore.remove('id2')).rejects.toBe(err);
    expect(get(m.aliasStore).list.map((a) => a.anonymous_id)).toEqual(['id1', 'id2', 'id3']);
    expect(get(m.aliasStore).list).toEqual(list);
  });

  it('update applies locally and rolls back on error', async () => {
    const list = [mk(1, { label: 'old', note: 'n' })];
    await loaded(list);
    const d = deferred<{ ok: boolean }>();
    h.api.patch.mockReturnValue(d.promise);
    const p = m.aliasStore.update('id1', { label: 'new', note: 'm' });
    expect(get(m.aliasStore).list[0]).toMatchObject({ label: 'new', note: 'm' });
    d.reject(new m.ApiError(500, 'apple_error'));
    await expect(p).rejects.toBeTruthy();
    expect(get(m.aliasStore).list).toEqual(list);
    expect(h.api.patch).toHaveBeenCalledWith('id1', { label: 'new', note: 'm' });
  });

  it('rollback after a concurrent change only reverts its own change', async () => {
    await loaded([mk(1), mk(2)]);
    const d = deferred<{ ok: boolean }>();
    h.api.setActive.mockReturnValueOnce(d.promise);
    h.api.remove.mockResolvedValue({ ok: true });
    const p = m.aliasStore.setActive('id1', false);
    await m.aliasStore.remove('id2');
    d.reject(new m.ApiError(500, 'x'));
    await expect(p).rejects.toBeTruthy();
    expect(get(m.aliasStore).list).toEqual([mk(1)]);
  });

  it('setForwardTo calls the API then refreshes with refresh=true', async () => {
    await loaded([mk(1)]);
    h.api.setForwardTo.mockResolvedValue({ ok: true });
    h.api.listAliases.mockResolvedValue([mk(1, { forward_to: 'z@x.test' })]);
    await m.aliasStore.setForwardTo('z@x.test');
    expect(h.api.setForwardTo).toHaveBeenCalledWith('z@x.test');
    expect(h.api.listAliases).toHaveBeenLastCalledWith(true);
    expect(get(m.aliasStore).list[0]?.forward_to).toBe('z@x.test');
  });

  it('API errors on actions are passed to sessionStore.onApiError', async () => {
    await loaded([mk(1)]);
    const err = new m.ApiError(401, 'not_authenticated');
    h.api.generate.mockRejectedValue(err);
    await expect(m.aliasStore.generate()).rejects.toBe(err);
    expect(h.onApiError).toHaveBeenCalledWith(err);
    expect(get(m.aliasStore).list).toHaveLength(1);
  });
});

describe('logout wipe', () => {
  it('test_logout_clears_store_and_cache', async () => {
    await seedCache([mk(1)]);
    await loaded([mk(1), mk(2)]);
    expect(h.logoutCbs).toHaveLength(1);
    h.logoutCbs[0]?.();
    await tick();
    await tick();
    const s = get(m.aliasStore);
    expect(s.list).toEqual([]);
    expect(s.index.aliases).toEqual([]);
    expect(s.error).toBeNull();
    const key = await m.crypto.importCacheKey(KEY);
    expect(await m.cache.load(key)).toBeNull();
  });

  it('registers the logout hook exactly once per module instance', async () => {
    h.onLogout.mockReset();
    vi.resetModules();
    await import('../src/lib/aliases');
    await import('../src/lib/aliases');
    expect(h.onLogout).toHaveBeenCalledTimes(1);
    vi.resetModules();
    await import('../src/lib/aliases');
    expect(h.onLogout).toHaveBeenCalledTimes(2);
  });
});

describe('review fixes', () => {
  const ids = () => get(m.aliasStore).list.map((a) => a.anonymous_id);

  it('logout while a mutation persist is in flight leaves no cache', async () => {
    await loaded([mk(1), mk(2)]);
    h.api.setActive.mockResolvedValue({ ok: true });
    let release!: () => void;
    h.encryptGate = new Promise<void>((r) => (release = r));
    const p = m.aliasStore.setActive('id1', false);
    await tick();
    await tick();
    h.logoutCbs[0]?.();
    h.encryptGate = null;
    release();
    await p;
    for (let i = 0; i < 6; i++) await tick();
    expect((await indexedDB.databases()).some((d) => d.name === 'aliasfox-cache')).toBe(false);
    const key = await m.crypto.importCacheKey(KEY);
    expect(await m.cache.load(key)).toBeNull();
    expect(get(m.aliasStore).list).toEqual([]);
  });

  it('a stale list response during a pending toggle is dropped, then one refresh=true re-fetch applies', async () => {
    const list = [mk(1), mk(2)];
    await loaded(list);
    const toggle = deferred<{ ok: boolean }>();
    h.api.setActive.mockReturnValue(toggle.promise);
    const stale = deferred<Alias[]>();
    h.api.listAliases.mockReset();
    h.api.listAliases.mockReturnValueOnce(stale.promise);
    const rp = m.aliasStore.refresh();
    const tp = m.aliasStore.setActive('id1', false);
    stale.resolve(list); // pre-mutation data
    await tick();
    await tick();
    expect(get(m.aliasStore).list[0]?.is_active).toBe(false); // not clobbered
    expect(h.api.listAliases).toHaveBeenCalledTimes(1);
    h.api.listAliases.mockResolvedValue([mk(1, { is_active: false }), mk(2)]);
    toggle.resolve({ ok: true });
    await tp;
    await rp;
    expect(h.api.listAliases).toHaveBeenCalledTimes(2);
    expect(h.api.listAliases).toHaveBeenLastCalledWith(true);
    expect(get(m.aliasStore).list).toEqual([mk(1, { is_active: false }), mk(2)]);
    expect(get(m.aliasStore).error).toBeNull();
  });

  it('a mutation applied before the fetch started and still pending is not clobbered', async () => {
    const list = [mk(1), mk(2)];
    await loaded(list);
    const toggle = deferred<{ ok: boolean }>();
    h.api.setActive.mockReturnValue(toggle.promise);
    const tp = m.aliasStore.setActive('id1', false);
    const stale = deferred<Alias[]>();
    h.api.listAliases.mockReset();
    h.api.listAliases.mockReturnValueOnce(stale.promise);
    const rp = m.aliasStore.refresh();
    stale.resolve(list);
    await tick();
    await tick();
    expect(get(m.aliasStore).list[0]?.is_active).toBe(false);
    h.api.listAliases.mockResolvedValue([mk(1, { is_active: false }), mk(2)]);
    toggle.resolve({ ok: true });
    await Promise.all([tp, rp]);
    expect(h.api.listAliases).toHaveBeenLastCalledWith(true);
    expect(get(m.aliasStore).list[0]?.is_active).toBe(false);
  });

  it('re-fetching is bounded under continuous mutations', async () => {
    await loaded([mk(1)]);
    h.api.setActive.mockResolvedValue({ ok: true });
    h.api.listAliases.mockReset();
    const pending: Promise<void>[] = [];
    h.api.listAliases.mockImplementation(async () => {
      pending.push(m.aliasStore.setActive('id1', pending.length % 2 === 0));
      return [mk(1)];
    });
    await m.aliasStore.refresh();
    await Promise.all(pending);
    for (let i = 0; i < 6; i++) await tick();
    expect(h.api.listAliases.mock.calls.length).toBeLessThanOrEqual(3);
    expect(h.api.listAliases.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it('a rate_limited re-fetch keeps the optimistic state without an error', async () => {
    await loaded([mk(1)]);
    h.api.setActive.mockResolvedValue({ ok: true });
    const stale = deferred<Alias[]>();
    h.api.listAliases.mockReset();
    h.api.listAliases.mockReturnValueOnce(stale.promise);
    h.api.listAliases.mockRejectedValue(new m.ApiError(429, 'rate_limited'));
    const toggle = deferred<{ ok: boolean }>();
    h.api.setActive.mockReturnValue(toggle.promise);
    const rp = m.aliasStore.refresh();
    const tp = m.aliasStore.setActive('id1', false);
    stale.resolve([mk(1)]);
    toggle.resolve({ ok: true });
    await Promise.all([rp, tp]);
    expect(get(m.aliasStore).list[0]?.is_active).toBe(false);
    expect(get(m.aliasStore).error).toBeNull();
    expect(h.onApiError).not.toHaveBeenCalled();
  });

  it('same hme reserves share one request; different hmes run concurrently', async () => {
    await loaded([]);
    const d1 = deferred<Alias>();
    const d2 = deferred<Alias>();
    h.api.reserve.mockReturnValueOnce(d1.promise).mockReturnValueOnce(d2.promise);
    const a1 = m.aliasStore.reserve({ hme: 'one@icloud.com', label: 'a', note: '' });
    const a1b = m.aliasStore.reserve({ hme: 'one@icloud.com', label: 'a', note: '' });
    const a2 = m.aliasStore.reserve({ hme: 'two@icloud.com', label: 'b', note: '' });
    expect(a1b).toBe(a1);
    expect(a2).not.toBe(a1);
    expect(h.api.reserve).toHaveBeenCalledTimes(2);
    d2.resolve(mk(2, { hme: 'two@icloud.com' }));
    d1.resolve(mk(1, { hme: 'one@icloud.com' }));
    const [r1, r2] = await Promise.all([a1, a2]);
    expect(r1?.anonymous_id).toBe('id1');
    expect(r2?.anonymous_id).toBe('id2');
    expect(ids().sort()).toEqual(['id1', 'id2']);
  });

  it('a failed reserve releases its hme so a retry sends again', async () => {
    await loaded([]);
    h.api.reserve.mockRejectedValueOnce(new m.ApiError(500, 'apple_error'));
    await expect(m.aliasStore.reserve({ hme: 'r@icloud.com', label: 'a', note: '' })).rejects.toBeTruthy();
    h.api.reserve.mockResolvedValueOnce(mk(7));
    await m.aliasStore.reserve({ hme: 'r@icloud.com', label: 'a', note: '' });
    expect(h.api.reserve).toHaveBeenCalledTimes(2);
  });

  it('generate has a single in-flight guard', async () => {
    await loaded([]);
    const d = deferred<{ hme: string }>();
    h.api.generate.mockReturnValue(d.promise);
    const p1 = m.aliasStore.generate();
    const p2 = m.aliasStore.generate();
    expect(p2).toBe(p1);
    d.resolve({ hme: 'g@icloud.com' });
    expect(await p1).toBe('g@icloud.com');
    expect(h.api.generate).toHaveBeenCalledTimes(1);
    h.api.generate.mockResolvedValue({ hme: 'h@icloud.com' });
    expect(await m.aliasStore.generate()).toBe('h@icloud.com');
  });

  it('loadError is set only by list fetch failures, never by mutation failures', async () => {
    await loaded([mk(1)]);
    expect(get(m.aliasStore).loadError).toBeNull();
    h.api.generate.mockRejectedValueOnce(new m.ApiError(429, 'rate_limited'));
    await expect(m.aliasStore.generate()).rejects.toBeTruthy();
    h.api.setActive.mockRejectedValueOnce(new m.ApiError(502, 'apple_error'));
    await expect(m.aliasStore.setActive('id1', false)).rejects.toBeTruthy();
    expect(get(m.aliasStore).error).toBe('apple_error'); // backward compatible: last error of any kind
    expect(get(m.aliasStore).loadError).toBeNull();
    h.api.listAliases.mockRejectedValueOnce(new m.ApiError(502, 'apple_unreachable'));
    await m.aliasStore.refresh();
    expect(get(m.aliasStore).loadError).toBe('apple_unreachable');
    h.api.listAliases.mockResolvedValueOnce([mk(1)]);
    await m.aliasStore.refresh();
    expect(get(m.aliasStore).loadError).toBeNull();
  });

  it('a stale loadError is cleared when the list goes from empty to non-empty, so create-then-delete cannot resurrect it', async () => {
    h.api.listAliases.mockRejectedValueOnce(new m.ApiError(502, 'apple_unreachable'));
    await m.aliasStore.init();
    expect(get(m.aliasStore).loadError).toBe('apple_unreachable');
    expect(get(m.aliasStore).list).toEqual([]);
    h.api.reserve.mockResolvedValueOnce(mk(7));
    await m.aliasStore.reserve({ hme: 'a7@icloud.com', label: 'x', note: '' });
    expect(get(m.aliasStore).loadError).toBeNull();
    h.api.remove.mockResolvedValueOnce({ ok: true });
    await m.aliasStore.remove('id7');
    expect(get(m.aliasStore).list).toEqual([]);
    expect(get(m.aliasStore).loadError).toBeNull();
  });

  it('a successful mutation clears loadError even when the list stays non-empty', async () => {
    await loaded([mk(1), mk(2)]);
    h.api.listAliases.mockRejectedValueOnce(new m.ApiError(502, 'apple_unreachable'));
    await m.aliasStore.refresh();
    expect(get(m.aliasStore).loadError).toBe('apple_unreachable');
    h.api.setActive.mockResolvedValueOnce({ ok: true });
    await m.aliasStore.setActive('id1', false);
    expect(get(m.aliasStore).loadError).toBeNull();
  });

  it('a failed mutation keeps an existing loadError', async () => {
    await loaded([mk(1)]);
    h.api.listAliases.mockRejectedValueOnce(new m.ApiError(502, 'apple_unreachable'));
    await m.aliasStore.refresh();
    h.api.setActive.mockRejectedValueOnce(new m.ApiError(502, 'apple_error'));
    await expect(m.aliasStore.setActive('id1', false)).rejects.toBeTruthy();
    expect(get(m.aliasStore).loadError).toBe('apple_unreachable');
  });

  it('a reserve that settles after clear() resolves null instead of an unvalidated value', async () => {
    await loaded([mk(1)]);
    const d = deferred<unknown>();
    h.api.reserve.mockReturnValueOnce(d.promise);
    const p = m.aliasStore.reserve({ hme: 'g@icloud.com', label: 'a', note: '' });
    await m.aliasStore.clear();
    d.resolve(undefined);
    await expect(p).resolves.toBeNull();
    expect(get(m.aliasStore).list).toEqual([]);
  });
});
