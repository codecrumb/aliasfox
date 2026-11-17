// App shell routing for the signed-out state: multi mode (SignIn / SignUp by path) versus single
// mode (the password Login, unchanged). Fresh modules per test so /api/config loads like a page load.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Writable } from 'svelte/store';

vi.mock('../src/lib/session', async () => {
  const { writable } = await import('svelte/store');
  const store = writable({
    state: 'loading',
    authEnabled: true,
    mode: null,
    cacheKey: null,
    reason: null,
    errorText: null,
    accountDeleted: null
  });
  return {
    __store: store,
    sessionStore: {
      subscribe: store.subscribe,
      boot: vi.fn(async () => {}),
      login: vi.fn(),
      exchange: vi.fn(),
      logout: vi.fn(async () => {}),
      onApiError: vi.fn(),
      markReady: vi.fn()
    }
  };
});
vi.mock('../src/lib/aliases', async () => ({ aliasStore: (await import('./fakes')).createAliasStore() }));
vi.mock('../src/lib/neonAuth', async (orig) => {
  const real = await orig<typeof import('../src/lib/neonAuth')>();
  return {
    ...real,
    neonAuth: Object.fromEntries(
      ['signUp', 'signIn', 'getToken', 'verifyEmailCode', 'sendVerificationCode', 'signOut', 'deleteNeonUser'].map((k) => [k, vi.fn()])
    )
  };
});

type TL = typeof import('@testing-library/svelte');
let tl: TL;
let App: typeof import('../src/App.svelte').default;
let store: Writable<Record<string, unknown>>;
let neon: Record<string, ReturnType<typeof vi.fn>>;
let calls: string[];
let configReply: () => Response;

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const MULTI = { mode: 'multi', signups_open: true, neon_auth_url: 'https://auth.example/neondb/auth', contact: null };

beforeEach(async () => {
  vi.resetModules();
  tl = await import('@testing-library/svelte');
  App = (await import('../src/App.svelte')).default;
  const sessionMod = await import('../src/lib/session');
  store = (sessionMod as unknown as { __store: Writable<Record<string, unknown>> }).__store;
  neon = (await import('../src/lib/neonAuth')).neonAuth as unknown as Record<string, ReturnType<typeof vi.fn>>;
  calls = [];
  configReply = () => json(MULTI);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit) => {
      calls.push(`${init.method ?? 'GET'} ${path}`);
      if (path === '/api/config') return configReply();
      throw new Error(`unexpected ${path}`);
    })
  );
  history.replaceState(null, '', '/');
});
afterEach(() => {
  tl.cleanup();
  vi.unstubAllGlobals();
  history.replaceState(null, '', '/');
});

function signedOut() {
  store.update((v) => ({ ...v, state: 'login' }));
}

describe('multi mode signed-out routing', () => {
  it('/login shows SignIn', async () => {
    history.replaceState(null, '', '/login');
    tl.render(App);
    signedOut();
    expect(await tl.screen.findByLabelText('Email')).toBeInTheDocument();
    expect(tl.screen.getByLabelText('Password').getAttribute('autocomplete')).toBe('current-password');
    expect(tl.screen.getByRole('heading', { level: 1 })).toHaveTextContent('Log in to AliasFox');
  });

  it('/signup shows SignUp', async () => {
    history.replaceState(null, '', '/signup');
    tl.render(App);
    signedOut();
    await tl.waitFor(() =>
      expect(tl.screen.getByLabelText('Password').getAttribute('autocomplete')).toBe('new-password')
    );
    expect(tl.screen.getByRole('heading', { level: 1 })).toHaveTextContent('Create an AliasFox account');
  });

  it('/signup with sign-ups closed still shows the form, with an invite-only note', async () => {
    configReply = () => json({ ...MULTI, signups_open: false });
    history.replaceState(null, '', '/signup');
    tl.render(App);
    signedOut();
    expect(await tl.screen.findByText(/invite-only for now/)).toBeInTheDocument();
    expect(tl.screen.getByLabelText('Email')).toBeInTheDocument();
  });

  it('an unknown path shows SignIn', async () => {
    history.replaceState(null, '', '/whatever');
    tl.render(App);
    signedOut();
    expect(await tl.screen.findByLabelText('Email')).toBeInTheDocument();
    expect(tl.screen.queryByText('Create an account', { selector: 'h2' })).toBeNull();
    expect(location.pathname).toBe('/login');
  });

  it('signed out at / (for example after logging out): the address becomes /login', async () => {
    history.replaceState(null, '', '/');
    tl.render(App);
    signedOut();
    expect(await tl.screen.findByLabelText('Email')).toBeInTheDocument();
    expect(location.pathname).toBe('/login');
  });

  it('links switch screens with pushState, and Back (popstate) returns', async () => {
    history.replaceState(null, '', '/login');
    tl.render(App);
    signedOut();
    await tl.screen.findByLabelText('Email');
    await tl.fireEvent.click(tl.screen.getByRole('link', { name: 'Create an account' }));
    expect(location.pathname).toBe('/signup');
    await tl.waitFor(() =>
      expect(tl.screen.getByLabelText('Password').getAttribute('autocomplete')).toBe('new-password')
    );
    history.replaceState(null, '', '/login');
    window.dispatchEvent(new PopStateEvent('popstate'));
    await tl.waitFor(() =>
      expect(tl.screen.getByLabelText('Password').getAttribute('autocomplete')).toBe('current-password')
    );
  });

  it('no password Login is ever shown in multi mode, even before the config arrives', async () => {
    let release: () => void = () => {};
    configReply = () => json(MULTI);
    const gate = new Promise<void>((r) => (release = r));
    vi.mocked(fetch).mockImplementationOnce(async (path) => {
      calls.push(`GET ${String(path)}`);
      await gate;
      return json(MULTI);
    });
    tl.render(App);
    signedOut();
    await new Promise((r) => setTimeout(r, 10));
    expect(tl.screen.queryByLabelText('Password')).toBeNull();
    release();
    expect(await tl.screen.findByLabelText('Email')).toBeInTheDocument();
  });
});

describe('single mode', () => {
  it.each([
    ['config says single', () => json({ mode: 'single', signups_open: false, neon_auth_url: null, contact: null })],
    ['config fails', () => json({ detail: 'internal_error' }, 500)],
    ['config is garbage', () => new Response('nope', { status: 200 })]
  ])('%s: the password Login, one /api/config request, no Neon Auth call', async (_n, reply) => {
    configReply = reply;
    history.replaceState(null, '', '/signup');
    tl.render(App);
    signedOut();
    const pw = await tl.screen.findByLabelText('Password');
    expect(pw.getAttribute('autocomplete')).toBe('current-password');
    expect(tl.screen.queryByLabelText('Email')).toBeNull();
    expect(calls).toEqual(['GET /api/config']);
    for (const fn of Object.values(neon)) expect(fn).not.toHaveBeenCalled();
    expect(location.pathname).toBe('/signup'); // single mode never rewrites the URL
  });
});
