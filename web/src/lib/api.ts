import type { Alias, ApiKey, ConnectLoginResult, CreatedApiKey, SessionInfo, StatusInfo } from './types';

const TIMEOUT_MS = 45_000;
// /api/config is fetched on every page load; single mode must not wait long for it.
const CONFIG_TIMEOUT_MS = 5_000;
const SERVER_MESSAGE_MAX = 200;

export class ApiError extends Error {
  status: number;
  detail: string;
  retryAfter?: number;
  serverMessage?: string;

  constructor(status: number, detail: string, extra: { retryAfter?: number; serverMessage?: string } = {}) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    if (extra.retryAfter !== undefined) this.retryAfter = extra.retryAfter;
    if (extra.serverMessage !== undefined) this.serverMessage = extra.serverMessage;
  }
}

function parseRetryAfter(res: Response): number | undefined {
  const raw = res.headers.get('Retry-After');
  if (raw === null || !/^\d+$/.test(raw.trim())) return undefined;
  return Number.parseInt(raw.trim(), 10);
}

async function toError(res: Response, signal: AbortSignal): Promise<ApiError> {
  const retryAfter = parseRetryAfter(res);
  const extra: { retryAfter?: number; serverMessage?: string } = {};
  if (retryAfter !== undefined) extra.retryAfter = retryAfter;
  let detail = `http_${res.status}`;
  try {
    const body: unknown = await readJson(res, signal);
    if (body !== null && typeof body === 'object' && 'detail' in body) {
      const d = (body as { detail: unknown }).detail;
      if (typeof d === 'string' && d !== '') {
        detail = d;
        if ((d === 'apple_error' || d === 'invalid_cookie') && 'message' in body) {
          const m = (body as { message: unknown }).message;
          if (typeof m === 'string') {
            extra.serverMessage = Array.from(m).slice(0, SERVER_MESSAGE_MAX).join('');
          }
        }
      } else if (Array.isArray(d)) {
        detail = 'validation_error';
      }
    }
  } catch {
    // non-JSON or empty body: keep http_<status>
  }
  if (signal.aborted) return new ApiError(0, 'network_error');
  return new ApiError(res.status, detail, extra);
}

// Reads the body but gives up as soon as the timeout aborts the request.
function readJson(res: Response, signal: AbortSignal): Promise<unknown> {
  return new Promise<unknown>((resolve, reject) => {
    if (signal.aborted) {
      reject(new Error('aborted'));
      return;
    }
    signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
    res.json().then(resolve, reject);
  });
}

async function request<T>(method: string, path: string, body?: unknown, timeoutMs = TIMEOUT_MS): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  const init: RequestInit = { method, credentials: 'same-origin', headers };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const controller = new AbortController();
  init.signal = controller.signal;
  // The timer covers the whole exchange, including reading the body.
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    let res: Response;
    try {
      res = await fetch(path, init);
    } catch {
      throw new ApiError(0, 'network_error');
    }
    if (!res.ok) throw await toError(res, controller.signal);
    try {
      return (await readJson(res, controller.signal)) as T;
    } catch {
      if (controller.signal.aborted) throw new ApiError(0, 'network_error');
      throw new ApiError(res.status, 'bad_response');
    }
  } finally {
    clearTimeout(timer);
  }
}

const seg = (id: string): string => encodeURIComponent(id);

export const api = {
  login: (password: string) => request<{ ok: boolean }>('POST', '/api/auth/login', { password }),
  logout: () => request<{ ok: boolean }>('POST', '/api/auth/logout'),
  config: () => request<unknown>('GET', '/api/config', undefined, CONFIG_TIMEOUT_MS),
  exchange: (token: string) => request<{ ok: boolean }>('POST', '/api/auth/exchange', { token }),
  deleteAccount: () => request<{ status: string }>('DELETE', '/api/account'),
  session: () => request<SessionInfo>('GET', '/api/session'),
  status: () => request<StatusInfo>('GET', '/api/status'),
  statusRecheck: () => request<{ connected: boolean; hme_available: boolean | null }>('POST', '/api/status/recheck'),
  listAliases: (refresh?: boolean) =>
    request<Alias[]>('GET', refresh ? '/api/aliases?refresh=true' : '/api/aliases'),
  generate: () => request<{ hme: string }>('POST', '/api/aliases/generate'),
  reserve: (b: { hme: string; label: string; note: string }) => request<Alias>('POST', '/api/aliases', b),
  patch: (id: string, b: { label: string; note?: string }) =>
    request<{ ok: boolean }>('PATCH', `/api/aliases/${seg(id)}`, b),
  setActive: (id: string, active: boolean) =>
    request<{ ok: boolean }>('POST', `/api/aliases/${seg(id)}/active`, { active }),
  remove: (id: string) => request<{ ok: boolean }>('DELETE', `/api/aliases/${seg(id)}`),
  forwardOptions: () => request<{ options: string[]; selected: string }>('GET', '/api/forward-to'),
  setForwardTo: (address: string) => request<{ ok: boolean }>('PUT', '/api/forward-to', { address }),
  connectCookie: (cookie: string) => request<{ status: 'ok' }>('POST', '/api/connect/cookie', { cookie }),
  connectLogin: (b: { apple_id: string; password: string; china?: boolean }) =>
    request<ConnectLoginResult>('POST', '/api/connect/login', b),
  connectVerify: (b: { flow_id: string; code: string }) =>
    request<{ status: 'ok' }>('POST', '/api/connect/verify', b),
  connectResend: (b: { flow_id: string }) => request<{ status: 'ok' }>('POST', '/api/connect/resend', b),
  disconnect: () => request<{ status: string }>('DELETE', '/api/connect'),
  listKeys: () => request<ApiKey[]>('GET', '/api/keys'),
  createKey: (b: { name: string; preset?: string; scopes?: string[]; expires_days?: number }) =>
    request<CreatedApiKey>('POST', '/api/keys', b),
  revokeKey: (id: number) => request<{ ok: boolean }>('DELETE', `/api/keys/${id}`)
};
