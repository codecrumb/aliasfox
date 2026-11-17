import { writable } from 'svelte/store';
import { api } from './api';

// Public server configuration (GET /api/config, no auth). Anything missing, malformed or failed
// counts as single mode, so single mode never depends on this request.

export interface AppConfig {
  loaded: boolean;
  mode: 'single' | 'multi';
  signupsOpen: boolean;
  neonAuthUrl: string | null;
  contact: string | null;
}

const SINGLE: AppConfig = { loaded: true, mode: 'single', signupsOpen: false, neonAuthUrl: null, contact: null };

export const appConfig = writable<AppConfig>({ ...SINGLE, loaded: false });

function httpUrl(v: unknown): string | null {
  if (typeof v !== 'string') return null;
  try {
    const u = new URL(v);
    return u.protocol === 'https:' || u.protocol === 'http:' ? v : null;
  } catch {
    return null;
  }
}

export function parseConfig(raw: unknown): AppConfig {
  if (raw === null || typeof raw !== 'object') return { ...SINGLE };
  const r = raw as Record<string, unknown>;
  if (r.mode !== 'multi') return { ...SINGLE };
  return {
    loaded: true,
    mode: 'multi',
    signupsOpen: r.signups_open === true,
    neonAuthUrl: httpUrl(r.neon_auth_url),
    contact: typeof r.contact === 'string' && r.contact.trim() !== '' ? r.contact.trim() : null
  };
}

let pending: Promise<void> | null = null;

/** Loads once per page; later calls share the first request. Never rejects. */
export function loadConfig(): Promise<void> {
  pending ??= (async () => {
    let next: AppConfig;
    try {
      next = parseConfig(await api.config());
    } catch {
      next = { ...SINGLE };
    }
    appConfig.set(next);
  })();
  return pending;
}
