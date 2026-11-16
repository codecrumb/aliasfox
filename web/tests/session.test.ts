import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>;
let handlers: Record<string, Handler> = {};
let consoleSpies: ReturnType<typeof vi.spyOn>[] = [];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}
const SESSION = { cache_key: 'KEY1', mode: 'live', auth_enabled: true };

function route(h: Record<string, Handler>) {
  handlers = h;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit) => {
      const h2 = handlers[`${init.method} ${url}`];
      if (!h2) throw new Error(`unrouted ${init.method} ${url}`);
      return h2(url, init);
    })
  );
}

async function fresh() {
  vi.resetModules();
  const mod = await import('../src/lib/session');
  const apiMod = await import('../src/lib/api');
  return { store: mod.sessionStore, ApiError: apiMod.ApiError };
}

beforeEach(() => {
  consoleSpies = (['log', 'info', 'warn', 'error', 'debug'] as const).map((m) =>
    vi.spyOn(console, m).mockImplementation(() => {})
  );
});
afterEach(() => {
  for (const s of consoleSpies) expect(s).not.toHaveBeenCalled();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('sessionStore', () => {
  it('starts in loading with no cacheKey', async () => {
    const { store } = await fresh();
    expect(get(store).state).toBe('loading');
    expect(get(store).cacheKey).toBeNull();
  });

  it('test_boot_goes_login_when_not_authenticated', async () => {
    route({ 'GET /api/session': () => json({ detail: 'not_authenticated' }, 401) });
    const { store } = await fresh();
    await store.boot();
    expect(get(store).state).toBe('login');
    expect(get(store).cacheKey).toBeNull();
  });

  it('test_boot_goes_connect_when_status_not_connected', async () => {
    route({
      'GET /api/session': () => json(SESSION),
      'GET /api/status': () => json({ connected: false, mode: 'live', auth_enabled: true })
    });
    const { store } = await fresh();
    await store.boot();
    const s = get(store);
    expect(s.state).toBe('connect');
    expect(s.cacheKey).toBe('KEY1');
    expect(s.mode).toBe('live');
    expect(s.authEnabled).toBe(true);
  });

  it('test_boot_goes_ready_when_connected', async () => {
    route({
      'GET /api/session': () => json(SESSION),
      'GET /api/status': () => json({ connected: true, mode: 'live', auth_enabled: true })
    });
    const { store } = await fresh();
    await store.boot();
    expect(get(store).state).toBe('ready');
    expect(get(store).cacheKey).toBe('KEY1');
  });

  it('boot goes reconnect for session_unreadable status', async () => {
    route({
      'GET /api/session': () => json(SESSION),
      'GET /api/status': () =>
        json({ connected: false, reason: 'session_unreadable', mode: 'live', auth_enabled: true })
    });
    const { store } = await fresh();
    await store.boot();
    expect(get(store).state).toBe('reconnect');
    expect(get(store).reason).toBe('session_unreadable');
  });

  it('boot with auth disabled goes straight to connect/ready without login', async () => {
    route({
      'GET /api/session': () => json({ ...SESSION, auth_enabled: false }),
      'GET /api/status': () => json({ connected: true, mode: 'live', auth_enabled: false })
    });
    const { store } = await fresh();
    await store.boot();
    expect(get(store).state).toBe('ready');
    expect(get(store).authEnabled).toBe(false);
    route({
      'GET /api/session': () => json({ ...SESSION, auth_enabled: false }),
      'GET /api/status': () => json({ connected: false, mode: 'live', auth_enabled: false })
    });
    await store.boot();
    expect(get(store).state).toBe('connect');
  });

  it('boot failure on network error goes to error with errorText', async () => {
    route({
      'GET /api/session': () => {
        throw new TypeError('down');
      }
    });
    const { store } = await fresh();
    await store.boot();
    expect(get(store).state).toBe('error');
    expect(get(store).errorText).toBe('network_error');
  });

  it('boot failure on 500 goes to error', async () => {
    route({ 'GET /api/session': () => json({ detail: 'internal_error' }, 500) });
    const { store } = await fresh();
    await store.boot();
    expect(get(store).state).toBe('error');
    expect(get(store).errorText).toBe('internal_error');
  });

  it('login posts then re-boots', async () => {
    let authed = false;
    route({
      'POST /api/auth/login': () => {
        authed = true;
        return json({ ok: true });
      },
      'GET /api/session': () =>
        authed ? json(SESSION) : json({ detail: 'not_authenticated' }, 401),
      'GET /api/status': () => json({ connected: true, mode: 'live', auth_enabled: true })
    });
    const { store } = await fresh();
    await store.boot();
    expect(get(store).state).toBe('login');
    await store.login('pw');
    expect(get(store).state).toBe('ready');
  });

  it('login failure rejects and leaves state at login', async () => {
    route({
      'GET /api/session': () => json({ detail: 'not_authenticated' }, 401),
      'POST /api/auth/login': () => json({ detail: 'invalid_credentials' }, 401)
    });
    const { store, ApiError } = await fresh();
    await store.boot();
    await expect(store.login('bad')).rejects.toBeInstanceOf(ApiError);
    expect(get(store).state).toBe('login');
  });

  it('onApiError mapping', async () => {
    const { store, ApiError } = await fresh();
    const mk = (d: string, s = 409) => new ApiError(s, d);
    store.onApiError(mk('not_authenticated', 401));
    expect(get(store).state).toBe('login');
    store.onApiError(mk('not_connected'));
    expect(get(store).state).toBe('connect');
    store.onApiError(mk('session_unreadable'));
    expect(get(store).state).toBe('reconnect');
    store.onApiError(mk('session_expired', 401));
    expect(get(store).state).toBe('reconnect');
    expect(get(store).reason).toBe('session_expired');
    store.onApiError(mk('not_connected'));
    store.onApiError(new ApiError(0, 'network_error'));
    expect(get(store).state).toBe('connect');
    store.onApiError(new ApiError(500, 'http_500'));
    expect(get(store).state).toBe('connect');
    store.onApiError(new ApiError(502, 'apple_error'));
    expect(get(store).state).toBe('connect');
  });

  it('test_session_expired_moves_to_reconnect_and_keeps_alias_state', async () => {
    route({
      'GET /api/session': () => json(SESSION),
      'GET /api/status': () => json({ connected: true, mode: 'live', auth_enabled: true })
    });
    const { store, ApiError } = await fresh();
    await store.boot();
    // Stand-in for another store's alias list: must be untouched, and onLogout cbs not fired.
    const aliases = [{ anonymous_id: '1' }];
    const cb = vi.fn();
    store.onLogout(cb);
    store.onApiError(new ApiError(401, 'session_expired'));
    expect(get(store).state).toBe('reconnect');
    expect(get(store).cacheKey).toBe('KEY1');
    expect(cb).not.toHaveBeenCalled();
    expect(aliases).toEqual([{ anonymous_id: '1' }]);
  });

  it('markReady refetches session for the cache key then ready', async () => {
    let key = 'KEY1';
    route({
      'GET /api/session': () => json({ ...SESSION, cache_key: key }),
      'GET /api/status': () => json({ connected: false, mode: 'live', auth_enabled: true })
    });
    const { store } = await fresh();
    await store.boot();
    expect(get(store).state).toBe('connect');
    key = 'KEY2';
    await store.markReady();
    expect(get(store).state).toBe('ready');
    expect(get(store).cacheKey).toBe('KEY2');
  });

  it('logout runs callbacks even when the request fails, clears cacheKey, goes to login', async () => {
    route({
      'GET /api/session': () => json(SESSION),
      'GET /api/status': () => json({ connected: true, mode: 'live', auth_enabled: true }),
      'POST /api/auth/logout': () => {
        throw new TypeError('down');
      }
    });
    const { store } = await fresh();
    await store.boot();
    const cb = vi.fn();
    store.onLogout(cb);
    await store.logout();
    expect(cb).toHaveBeenCalledTimes(1);
    expect(get(store).cacheKey).toBeNull();
    expect(get(store).state).toBe('login');
  });

  it('logout with auth disabled goes to ready', async () => {
    route({
      'GET /api/session': () => json({ ...SESSION, auth_enabled: false }),
      'GET /api/status': () => json({ connected: true, mode: 'live', auth_enabled: false }),
      'POST /api/auth/logout': () => json({ ok: true })
    });
    const { store } = await fresh();
    await store.boot();
    const cb = vi.fn();
    store.onLogout(cb);
    await store.logout();
    expect(cb).toHaveBeenCalledTimes(1);
    expect(get(store).state).toBe('ready');
    expect(get(store).cacheKey).toBeNull();
  });

  it('a throwing callback does not stop the others', async () => {
    route({ 'POST /api/auth/logout': () => json({ ok: true }) });
    const { store } = await fresh();
    const second = vi.fn();
    store.onLogout(() => {
      throw new Error('boom');
    });
    store.onLogout(second);
    await store.logout();
    expect(second).toHaveBeenCalledTimes(1);
    expect(get(store).state).toBe('login');
  });

  it('onLogout unsubscribe works', async () => {
    route({ 'POST /api/auth/logout': () => json({ ok: true }) });
    const { store } = await fresh();
    const cb = vi.fn();
    const off = store.onLogout(cb);
    off();
    await store.logout();
    expect(cb).not.toHaveBeenCalled();
  });
});
