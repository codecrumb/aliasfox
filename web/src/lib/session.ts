import { get, writable } from 'svelte/store';
import { ApiError, api } from './api';
import { neonAuth, type NeonDeleteResult } from './neonAuth';
import type { SessionInfo, SessionState, StatusInfo } from './types';

export interface SessionSnapshot {
  state: SessionState;
  authEnabled: boolean;
  mode: string | null;
  cacheKey: string | null;
  reason: string | null;
  errorText: string | null;
  /** Apple's Hide My Email entitlement from the last status check (null: unknown). */
  hmeAvailable: boolean | null;
  /** Set after "Delete account" (multi mode) for the signed-out view; cleared by the next sign-in. */
  accountDeleted: NeonDeleteResult | null;
}

const initial: SessionSnapshot = {
  state: 'loading',
  authEnabled: true,
  mode: null,
  cacheKey: null,
  reason: null,
  errorText: null,
  hmeAvailable: null,
  accountDeleted: null
};

const store = writable<SessionSnapshot>({ ...initial });
const logoutCallbacks = new Set<() => void>();

function patch(p: Partial<SessionSnapshot>): void {
  store.update((s) => ({ ...s, ...p }));
}

function detailOf(e: unknown): string {
  return e instanceof ApiError ? e.detail : 'network_error';
}

// Bumped by every boot() and logout(); an async step whose generation is stale drops its result.
let gen = 0;

const UNEXPECTED = 'unexpected_response';

function validSession(s: unknown): s is SessionInfo {
  return (
    s !== null &&
    typeof s === 'object' &&
    typeof (s as SessionInfo).cache_key === 'string' &&
    typeof (s as SessionInfo).auth_enabled === 'boolean'
  );
}

async function boot(): Promise<void> {
  const g = ++gen;
  try {
    const sess = await api.session();
    if (g !== gen) return;
    if (!validSession(sess)) {
      patch({ state: 'error', errorText: UNEXPECTED, cacheKey: null });
      return;
    }
    patch({
      cacheKey: sess.cache_key,
      mode: typeof sess.mode === 'string' ? sess.mode : null,
      authEnabled: sess.auth_enabled,
      errorText: null
    });
    const st = await api.status();
    if (g !== gen) return;
    if (st === null || typeof st !== 'object' || typeof st.connected !== 'boolean') {
      patch({ state: 'error', errorText: UNEXPECTED });
    } else if (st.connected) {
      const hmeAvailable =
        typeof (st as StatusInfo).hme_available === 'boolean' ? (st as StatusInfo).hme_available : null;
      patch({ state: 'ready', reason: null, hmeAvailable });
    } else if (st.reason === 'session_unreadable') {
      patch({ state: 'reconnect', reason: 'session_unreadable', hmeAvailable: null });
    } else {
      patch({ state: 'connect', reason: null, hmeAvailable: null });
    }
  } catch (e) {
    if (g !== gen) return;
    if (e instanceof ApiError && e.detail === 'not_authenticated') {
      patch({ state: 'login', cacheKey: null, reason: null, errorText: null, hmeAvailable: null });
    } else if (e instanceof ApiError && e.detail === 'not_connected') {
      patch({ state: 'connect', reason: null, hmeAvailable: null });
    } else {
      patch({ state: 'error', errorText: detailOf(e), hmeAvailable: null });
    }
  }
}

async function login(password: string): Promise<void> {
  const g = gen;
  await api.login(password);
  if (g !== gen) return;
  await boot();
}

/** Multi mode: trade a Neon Auth JWT for our session cookie, then load the app. */
async function exchange(token: string): Promise<void> {
  const g = gen;
  await api.exchange(token);
  if (g !== gen) return;
  patch({ accountDeleted: null });
  await boot();
}

/** Neon Auth calls are best effort: our own cookie and local wipe never wait on them. */
async function neonSignOut(): Promise<void> {
  try {
    await neonAuth.signOut();
  } catch {
    // ignore
  }
}

// The local wipe shared by logout and a server-side "not authenticated": every registered wipe
// (alias list, in-memory CryptoKey, IndexedDB cache) runs; one failing callback cannot block the others.
function wipeLocal(): void {
  for (const cb of [...logoutCallbacks]) {
    try {
      cb();
    } catch {
      // continue with the others
    }
  }
}

async function logout(): Promise<void> {
  ++gen; // invalidate any in-flight boot/markReady immediately
  const { authEnabled, mode } = get(store);
  if (mode === 'multi') void neonSignOut(); // never awaited: our logout and wipe don't wait on Neon
  try {
    await api.logout();
  } catch {
    // ignore: local state is wiped regardless
  }
  ++gen;
  wipeLocal();
  // Auth off: go through 'loading' (not ready -> ready) so the palette unmounts and runs init()
  // again when boot() brings the state back to ready.
  patch({ cacheKey: null, reason: null, errorText: null, state: authEnabled ? 'login' : 'loading' });
  if (!authEnabled) await boot(); // fresh cacheKey/state; guarded by the generation counter
}

/**
 * Multi mode "Delete account": the server deletes everything it holds (and clears our cookie), then
 * local data is wiped at once. Only afterwards, in the background, the Neon Auth user is deleted best
 * effort and the browser signs out of Neon; a 'pending' result adds the notice line later.
 * A server failure rejects before anything local changes.
 */
async function deleteAccount(): Promise<void> {
  await api.deleteAccount();
  ++gen;
  wipeLocal();
  patch({ state: 'login', cacheKey: null, reason: null, errorText: null, accountDeleted: 'deleted' });
  const g = gen;
  void (async () => {
    let neon: NeonDeleteResult;
    try {
      neon = await neonAuth.deleteNeonUser(); // needs the Neon session: before signOut
    } catch {
      neon = 'pending';
    }
    await neonSignOut();
    // Only if nothing happened since (a new sign-in boots, which bumps gen).
    if (neon === 'pending' && g === gen && get(store).accountDeleted === 'deleted') {
      patch({ accountDeleted: 'pending' });
    }
  })();
}

/** The aliases store imported the key into a non-extractable CryptoKey: drop the base64 copy. */
function dropCacheKey(): void {
  patch({ cacheKey: null });
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
      ++gen; // in-flight boot/markReady results belong to the dead session
      wipeLocal(); // same wipe as logout, minus the logout request (the server already forgot us)
      patch({ state: 'login', cacheKey: null, reason: null });
      break;
    case 'not_connected':
      patch({ state: 'connect', reason: null });
      break;
    case 'session_unreadable':
    case 'session_expired':
      patch({ state: 'reconnect', reason: e.detail });
      break;
    case 'hme_unavailable':
      // Apple says this account has no Hide My Email entitlement (iCloud+).
      patch({ hmeAvailable: false });
      break;
    default:
      break;
  }
}

/** Ask Apple again for the Hide My Email entitlement (after subscribing or cancelling). */
async function recheck(): Promise<boolean | null> {
  const g = gen;
  try {
    const res = await api.statusRecheck();
    if (g !== gen) return null;
    const available = typeof res.hme_available === 'boolean' ? res.hme_available : null;
    patch({ hmeAvailable: available });
    return available;
  } catch (e) {
    if (g !== gen) return null;
    if (e instanceof ApiError) onApiError(e);
    throw e;
  }
}

async function markReady(): Promise<void> {
  const g = gen;
  let sess: SessionInfo;
  try {
    sess = await api.session();
  } catch (e) {
    if (g !== gen) return;
    throw e;
  }
  if (g !== gen) return;
  if (!validSession(sess)) {
    patch({ state: 'error', errorText: UNEXPECTED, cacheKey: null });
    return;
  }
  patch({
    cacheKey: sess.cache_key,
    mode: typeof sess.mode === 'string' ? sess.mode : null,
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
  exchange,
  logout,
  deleteAccount,
  dropCacheKey,
  onLogout,
  onApiError,
  markReady,
  recheck
};
