import { get, writable } from 'svelte/store';
import { ApiError, api } from './api';
import type { SessionState } from './types';

export interface SessionSnapshot {
  state: SessionState;
  authEnabled: boolean;
  mode: string | null;
  cacheKey: string | null;
  reason: string | null;
  errorText: string | null;
}

const initial: SessionSnapshot = {
  state: 'loading',
  authEnabled: true,
  mode: null,
  cacheKey: null,
  reason: null,
  errorText: null
};

const store = writable<SessionSnapshot>({ ...initial });
const logoutCallbacks = new Set<() => void>();

function patch(p: Partial<SessionSnapshot>): void {
  store.update((s) => ({ ...s, ...p }));
}

function detailOf(e: unknown): string {
  return e instanceof ApiError ? e.detail : 'network_error';
}

async function boot(): Promise<void> {
  try {
    const sess = await api.session();
    patch({
      cacheKey: sess.cache_key,
      mode: sess.mode,
      authEnabled: sess.auth_enabled,
      errorText: null
    });
    const st = await api.status();
    if (st.connected) {
      patch({ state: 'ready', reason: null });
    } else if (st.reason === 'session_unreadable') {
      patch({ state: 'reconnect', reason: 'session_unreadable' });
    } else {
      patch({ state: 'connect', reason: null });
    }
  } catch (e) {
    if (e instanceof ApiError && e.detail === 'not_authenticated') {
      patch({ state: 'login', cacheKey: null, reason: null, errorText: null });
    } else if (e instanceof ApiError && e.detail === 'not_connected') {
      patch({ state: 'connect', reason: null });
    } else {
      patch({ state: 'error', errorText: detailOf(e) });
    }
  }
}

async function login(password: string): Promise<void> {
  await api.login(password);
  await boot();
}

async function logout(): Promise<void> {
  const { authEnabled } = get(store);
  try {
    await api.logout();
  } catch {
    // ignore: local state is wiped regardless
  }
  for (const cb of [...logoutCallbacks]) {
    try {
      cb();
    } catch {
      // one failing wipe must not block the others
    }
  }
  patch({ cacheKey: null, reason: null, errorText: null, state: authEnabled ? 'login' : 'ready' });
}

function onLogout(cb: () => void): () => void {
  logoutCallbacks.add(cb);
  return () => {
    logoutCallbacks.delete(cb);
  };
}

function onApiError(e: ApiError): void {
  switch (e.detail) {
    case 'not_authenticated':
      patch({ state: 'login' });
      break;
    case 'not_connected':
      patch({ state: 'connect', reason: null });
      break;
    case 'session_unreadable':
    case 'session_expired':
      patch({ state: 'reconnect', reason: e.detail });
      break;
    default:
      break;
  }
}

async function markReady(): Promise<void> {
  const sess = await api.session();
  patch({
    cacheKey: sess.cache_key,
    mode: sess.mode,
    authEnabled: sess.auth_enabled,
    state: 'ready',
    reason: null,
    errorText: null
  });
}

export const sessionStore = {
  subscribe: store.subscribe,
  boot,
  login,
  logout,
  onLogout,
  onApiError,
  markReady
};
