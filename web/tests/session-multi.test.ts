// Session store additions for multi mode (exchange, delete account, Neon sign-out on logout).
// Real session + api modules; fetch is routed; Neon Auth is a fake.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

vi.mock('../src/lib/neonAuth', () => ({
  neonAuth: {
    signUp: vi.fn(),
    signIn: vi.fn(),
    getToken: vi.fn(),
    verifyEmailCode: vi.fn(),
    sendVerificationCode: vi.fn(),
    signOut: vi.fn(async () => {}),
    deleteNeonUser: vi.fn(async () => 'deleted')
  }
}));

import { neonAuth } from '../src/lib/neonAuth';
import { ApiError } from '../src/lib/api';
import { sessionStore } from '../src/lib/session';

type Handler = (init: RequestInit) => Response;
let routes: Record<string, Handler> = {};
let order: string[] = [];

const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status });

function route(r: Record<string, Handler>) {
  routes = r;
}

beforeEach(() => {
  order = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit) => {
      const k = `${init.method} ${url}`;
      order.push(k);
      const h = routes[k];
      if (!h) throw new Error(`unrouted ${k}`);
      return h(init);
    })
  );
  vi.mocked(neonAuth.signOut).mockReset().mockImplementation(async () => {
    order.push('neon signOut');
  });
  vi.mocked(neonAuth.deleteNeonUser).mockReset().mockImplementation(async () => {
    order.push('neon deleteNeonUser');
    return 'deleted';
  });
});
afterEach(() => vi.unstubAllGlobals());

const MULTI_SESSION = { cache_key: 'K', mode: 'multi', auth_enabled: true };
const SINGLE_SESSION = { cache_key: 'K', mode: 'single', auth_enabled: true };

async function signedIn(session: Record<string, unknown>) {
  route({
    'GET /api/session': () => json(session),
    'GET /api/status': () => json({ connected: true, mode: session.mode, auth_enabled: true }),
    'POST /api/auth/logout': () => json({ ok: true }),
    'DELETE /api/account': () => json({ status: 'deleted' })
  });
  await sessionStore.boot();
  expect(get(sessionStore).state).toBe('ready');
  order = [];
}

describe('exchange', () => {
  it('posts the token once, then loads the app (boot)', async () => {
    let authed = false;
    route({
      'POST /api/auth/exchange': (init) => {
        expect(JSON.parse(String(init.body))).toEqual({ token: 'jwt-1' });
        authed = true;
        return json({ ok: true });
      },
      'GET /api/session': () => (authed ? json(MULTI_SESSION) : json({ detail: 'not_authenticated' }, 401)),
      'GET /api/status': () => json({ connected: false, mode: 'multi', auth_enabled: true })
    });
    await sessionStore.boot();
    expect(get(sessionStore).state).toBe('login');
    order = [];
    await sessionStore.exchange('jwt-1');
    expect(order).toEqual(['POST /api/auth/exchange', 'GET /api/session', 'GET /api/status']);
    expect(get(sessionStore).state).toBe('connect');
  });

  it('a refusal rejects with the ApiError and does not boot', async () => {
    route({ 'POST /api/auth/exchange': () => json({ detail: 'signups_closed' }, 403) });
    await expect(sessionStore.exchange('jwt')).rejects.toMatchObject({ status: 403, detail: 'signups_closed' });
    expect(order).toEqual(['POST /api/auth/exchange']);
  });
});

describe('logout', () => {
  it('multi mode: Neon sign-out first, then the AliasFox logout and local wipe', async () => {
    await signedIn(MULTI_SESSION);
    const wiped = vi.fn();
    const off = sessionStore.onLogout(wiped);
    await sessionStore.logout();
    off();
    expect(order).toEqual(['neon signOut', 'POST /api/auth/logout']);
    expect(wiped).toHaveBeenCalledTimes(1);
    expect(get(sessionStore).state).toBe('login');
  });

  it('multi mode: a failing Neon sign-out is ignored', async () => {
    await signedIn(MULTI_SESSION);
    vi.mocked(neonAuth.signOut).mockRejectedValueOnce(new Error('down'));
    await sessionStore.logout();
    expect(order).toEqual(['POST /api/auth/logout']);
    expect(get(sessionStore).state).toBe('login');
  });

  it('single mode: no Neon Auth call at all', async () => {
    await signedIn(SINGLE_SESSION);
    await sessionStore.logout();
    expect(order).toEqual(['POST /api/auth/logout']);
    for (const fn of Object.values(neonAuth)) expect(fn).not.toHaveBeenCalled();
  });
});

describe('deleteAccount', () => {
  it('deletes on the server, then Neon (best effort), signs out, wipes and returns to signed-out', async () => {
    await signedIn(MULTI_SESSION);
    const wiped = vi.fn();
    const off = sessionStore.onLogout(wiped);
    await sessionStore.deleteAccount();
    off();
    expect(order).toEqual(['DELETE /api/account', 'neon deleteNeonUser', 'neon signOut']);
    expect(wiped).toHaveBeenCalledTimes(1);
    const s = get(sessionStore);
    expect(s.state).toBe('login');
    expect(s.cacheKey).toBeNull();
    expect(s.accountDeleted).toBe('deleted');
  });

  it('Neon deletion pending is recorded for the signed-out view', async () => {
    await signedIn(MULTI_SESSION);
    vi.mocked(neonAuth.deleteNeonUser).mockResolvedValueOnce('pending');
    vi.mocked(neonAuth.signOut).mockRejectedValueOnce(new Error('down'));
    await sessionStore.deleteAccount();
    expect(get(sessionStore).accountDeleted).toBe('pending');
    expect(get(sessionStore).state).toBe('login');
  });

  it('a server failure rejects and changes nothing locally', async () => {
    await signedIn(MULTI_SESSION);
    route({ ...routes, 'DELETE /api/account': () => json({ detail: 'internal_error' }, 500) });
    const wiped = vi.fn();
    const off = sessionStore.onLogout(wiped);
    await expect(sessionStore.deleteAccount()).rejects.toBeInstanceOf(ApiError);
    off();
    expect(wiped).not.toHaveBeenCalled();
    expect(neonAuth.deleteNeonUser).not.toHaveBeenCalled();
    expect(get(sessionStore).state).toBe('ready');
  });

  it('signing in again clears the deleted notice', async () => {
    await signedIn(MULTI_SESSION);
    await sessionStore.deleteAccount();
    route({
      'POST /api/auth/exchange': () => json({ ok: true }),
      'GET /api/session': () => json(MULTI_SESSION),
      'GET /api/status': () => json({ connected: true, mode: 'multi', auth_enabled: true })
    });
    await sessionStore.exchange('jwt');
    expect(get(sessionStore).accountDeleted).toBeNull();
  });
});
