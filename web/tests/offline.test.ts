import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get, writable } from 'svelte/store';
import type { Alias } from '../src/lib/types';

const h = vi.hoisted(() => ({
  api: { listAliases: vi.fn(), patch: vi.fn(), setActive: vi.fn(), remove: vi.fn() }
}));
vi.mock('../src/lib/api', async () => {
  const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api');
  return { ...actual, api: h.api };
});
const sessionState = writable<{ cacheKey: string | null }>({ cacheKey: null });
vi.mock('../src/lib/session', () => ({
  sessionStore: {
    subscribe: (fn: (v: { cacheKey: string | null }) => void) => sessionState.subscribe(fn),
    onApiError: vi.fn(),
    dropCacheKey: () => {},
    onLogout: () => () => {}
  }
}));

const mk = (n: number, over: Partial<Alias> = {}): Alias => ({
  anonymous_id: `id${n}`, hme: `a${n}@icloud.com`, label: `l${n}`, note: null, is_active: true,
  forward_to: null, origin: null, domain: null, created_ms: 1000 - n, origin_app_name: null,
  app_bundle_id: null, ...over
});
const KEY = btoa(String.fromCharCode(...new Uint8Array(32).fill(3)))
  .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

type Mods = {
  aliasStore: typeof import('../src/lib/aliases').aliasStore;
  queue: typeof import('../src/lib/queue');
  ApiError: typeof import('../src/lib/api').ApiError;
};
let m: Mods;
let onLine = true;

beforeEach(async () => {
  for (const f of Object.values(h.api)) f.mockReset();
  onLine = true;
  // The encrypted cache lives in IndexedDB; a queue saved by one test must not reach the next.
  await new Promise((r) => {
    const q = indexedDB.deleteDatabase('aliasfox-cache');
    q.onsuccess = q.onerror = q.onblocked = () => r(null);
  });
  vi.spyOn(navigator, 'onLine', 'get').mockImplementation(() => onLine);
  sessionState.set({ cacheKey: KEY });
  vi.resetModules();
  m = {
    aliasStore: (await import('../src/lib/aliases')).aliasStore,
    queue: await import('../src/lib/queue'),
    ApiError: (await import('../src/lib/api')).ApiError
  };
  h.api.listAliases.mockResolvedValue([mk(1), mk(2)]);
  await m.aliasStore.init();
});
const netDown = () => new m.ApiError(0, 'network_error');

describe('offline queue', () => {
  it('keeps a change that failed on the network on screen and queues it', async () => {
    h.api.setActive.mockRejectedValue(netDown());
    await m.aliasStore.setActive('id1', false);
    expect(get(m.aliasStore).list[0]!.is_active).toBe(false);
    expect(get(m.queue.pending)).toBe(1);
  });

  it('does not call the server while the browser is offline', async () => {
    onLine = false;
    await m.aliasStore.update('id1', { label: 'new', note: 'n' });
    expect(h.api.patch).not.toHaveBeenCalled();
    expect(get(m.aliasStore).list[0]!.label).toBe('new');
    expect(get(m.queue.pending)).toBe(1);
  });

  it('still reverts and throws on a real server error', async () => {
    h.api.setActive.mockRejectedValue(new m.ApiError(502, 'apple_error'));
    await expect(m.aliasStore.setActive('id1', false)).rejects.toThrow();
    expect(get(m.aliasStore).list[0]!.is_active).toBe(true);
    expect(get(m.queue.pending)).toBe(0);
  });

  it('never queues a delete', async () => {
    onLine = false;
    h.api.remove.mockRejectedValue(netDown());
    await expect(m.aliasStore.remove('id1')).rejects.toThrow();
    expect(get(m.aliasStore).list).toHaveLength(2);
    expect(get(m.queue.pending)).toBe(0);
  });

  it('drops a change that lands back on its starting value', async () => {
    onLine = false;
    await m.aliasStore.setActive('id1', false);
    await m.aliasStore.setActive('id1', true);
    expect(get(m.queue.pending)).toBe(0);
  });

  it('replays on the next refresh, once', async () => {
    onLine = false;
    await m.aliasStore.setActive('id1', false);
    onLine = true;
    h.api.setActive.mockResolvedValue({ ok: true });
    await m.aliasStore.refresh();
    expect(h.api.setActive).toHaveBeenCalledExactlyOnceWith('id1', false);
    expect(get(m.queue.pending)).toBe(0);
    expect(get(m.aliasStore).list[0]!.is_active).toBe(false);
  });

  it('does not overwrite an alias that changed elsewhere', async () => {
    onLine = false;
    await m.aliasStore.update('id1', { label: 'mine', note: '' });
    onLine = true;
    h.api.listAliases.mockResolvedValue([mk(1, { label: 'theirs' }), mk(2)]);
    await m.aliasStore.refresh();
    expect(h.api.patch).not.toHaveBeenCalled();
    expect(get(m.queue.pending)).toBe(0);
    expect(get(m.aliasStore).list[0]!.label).toBe('theirs');
  });

  it('keeps the queue and the on-screen value when the network is still down', async () => {
    onLine = false;
    await m.aliasStore.setActive('id1', false);
    onLine = true;
    h.api.setActive.mockRejectedValue(netDown());
    await m.aliasStore.refresh();
    expect(get(m.queue.pending)).toBe(1);
    expect(get(m.aliasStore).list[0]!.is_active).toBe(false);
  });

  it('drops a waiting change when the same alias is changed online afterwards', async () => {
    h.api.setActive.mockRejectedValueOnce(netDown());
    await m.aliasStore.setActive('id1', false); // queued: server still has it active
    expect(get(m.queue.pending)).toBe(1);
    h.api.setActive.mockResolvedValue({ ok: true });
    await m.aliasStore.setActive('id1', true); // user changes their mind while reachable
    expect(get(m.queue.pending)).toBe(0);
    h.api.setActive.mockClear();
    await m.aliasStore.refresh();
    expect(h.api.setActive).not.toHaveBeenCalled(); // the old deactivate is never replayed
  });
});
