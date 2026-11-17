// REAL session store + REAL alias store + the real App; only fetch (and the clipboard) are faked.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { waitFor } from '@testing-library/dom';
import { get } from 'svelte/store';
import { alias } from './fakes';

// Fresh module instances per test: the stores are singletons and must start like a page load.
type Mods = {
  App: typeof import('../src/App.svelte').default;
  aliasStore: typeof import('../src/lib/aliases').aliasStore;
  ApiError: typeof import('../src/lib/api').ApiError;
  sessionStore: typeof import('../src/lib/session').sessionStore;
};
let App: Mods['App'];
let aliasStore: Mods['aliasStore'];
let ApiError: Mods['ApiError'];
let sessionStore: Mods['sessionStore'];
let render: typeof import('@testing-library/svelte').render;
let cleanup: typeof import('@testing-library/svelte').cleanup;

const KEY = 'A'.repeat(43);
const A = alias(1);
let calls: string[];
let authEnabled: boolean;
let listBody: unknown;

const dbExists = async (): Promise<boolean> => (await indexedDB.databases()).some((d) => d.name === 'aliasfox-cache');
const count = (k: string): number => calls.filter((c) => c === k).length;

beforeEach(async () => {
  vi.resetModules();
  ({ render, cleanup } = await import('@testing-library/svelte'));
  App = (await import('../src/App.svelte')).default;
  aliasStore = (await import('../src/lib/aliases')).aliasStore;
  ApiError = (await import('../src/lib/api')).ApiError;
  sessionStore = (await import('../src/lib/session')).sessionStore;
  calls = [];
  authEnabled = false;
  listBody = [A];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit) => {
      const k = `${init.method ?? 'GET'} ${path}`;
      calls.push(k);
      const body =
        k === 'GET /api/session'
          ? { cache_key: KEY, mode: 'single', auth_enabled: authEnabled }
          : k === 'GET /api/status'
            ? { connected: true, mode: 'single', auth_enabled: authEnabled }
            : k.startsWith('GET /api/aliases')
              ? listBody
              : { ok: true };
      return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
    })
  );
});
afterEach(async () => {
  cleanup();
  await aliasStore.clear();
  vi.unstubAllGlobals();
});

describe('integration with the real stores', () => {
  it('auth-off logout reloads the list (a second /api/aliases fetch and a populated list)', async () => {
    render(App);
    await waitFor(() => expect(get(aliasStore).list.length).toBe(1));
    expect(count('GET /api/aliases')).toBe(1);
    await sessionStore.logout();
    await waitFor(() => expect(count('GET /api/aliases')).toBe(2));
    await waitFor(() => expect(get(aliasStore).list.length).toBe(1));
    expect(get(sessionStore).state).toBe('ready');
    expect(count('POST /api/auth/logout')).toBe(1);
  });

  it('not_authenticated wipes like logout (list, key, IndexedDB) without calling the logout endpoint', async () => {
    authEnabled = true;
    render(App);
    await waitFor(() => expect(get(aliasStore).list.length).toBe(1));
    await waitFor(async () => expect(await dbExists()).toBe(true));
    sessionStore.onApiError(new ApiError(401, 'not_authenticated'));
    expect(get(sessionStore).state).toBe('login');
    expect(get(sessionStore).cacheKey).toBeNull();
    await waitFor(() => expect(get(aliasStore).list).toEqual([]));
    await waitFor(async () => expect(await dbExists()).toBe(false));
    expect(count('POST /api/auth/logout')).toBe(0);
  });

  it('the base64 cache key is dropped from the session store once it is imported, and caching still works', async () => {
    render(App);
    await waitFor(() => expect(get(aliasStore).list.length).toBe(1));
    expect(get(sessionStore).cacheKey).toBeNull();
    await waitFor(async () => expect(await dbExists()).toBe(true)); // saved with the imported key
    await waitFor(() => expect(get(aliasStore).refreshing).toBe(false)); // the first init has finished
    // a later ready (e.g. after reconnect) fetches a fresh key; init() imports and drops it again
    await sessionStore.markReady();
    expect(get(sessionStore).cacheKey).not.toBeNull();
    await aliasStore.init();
    expect(get(sessionStore).cacheKey).toBeNull();
    expect(await dbExists()).toBe(true);
  });
});
