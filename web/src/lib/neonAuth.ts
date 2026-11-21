// Browser client for Neon Auth (Managed Better Auth), plain fetch against its HTTP routes.
//
// Why not @neon/auth: no new dependency, nothing third-party in the bundle, and the CSP stays
// "script-src 'self'" with only connect-src widened to the Neon Auth origin.
//
// UNVERIFIED until the owner's live check against the real Neon Auth branch (each assumption sits in
// one function below, so a wrong guess is a one-place fix):
// - route paths and body shapes: /sign-up/email {email,password,name}, /sign-in/email,
//   /email-otp/verify-email {email,otp}, /email-otp/send-verification-otp, /sign-out, /delete-user;
// - sign-up answers {token: null} when the branch requires email verification (signUp);
// - GET /token returns {token} (JWT plugin) using the Neon Auth session cookie, which is a
//   third-party cookie from this origin and may be blocked by the browser (getToken);
// - error bodies carry Better Auth's {code} values (classify);
// - /delete-user is allowed for a signed-in user without a fresh password (deleteNeonUser);
// - the JWT carries the claims the server's exchange expects (checked server side).

import { get } from 'svelte/store';
import { appConfig } from './config';

export type NeonAuthFailure =
  | 'invalid_credentials'
  | 'email_taken'
  | 'weak_password'
  | 'invalid_email'
  | 'bad_code'
  | 'unverified'
  | 'rate_limited'
  | 'no_session'
  | 'invalid_origin'
  | 'network';

export class NeonAuthError extends Error {
  kind: NeonAuthFailure;
  constructor(kind: NeonAuthFailure) {
    super(kind); // the kind only: server text never reaches the UI
    this.name = 'NeonAuthError';
    this.kind = kind;
  }
}

export interface SignUpResult {
  needsVerification: boolean;
}

export type NeonDeleteResult = 'deleted' | 'pending';

export interface NeonAuthClient {
  signUp(email: string, password: string): Promise<SignUpResult>;
  signIn(email: string, password: string): Promise<void>;
  /** A short-lived JWT for POST /api/auth/exchange. */
  getToken(): Promise<string>;
  verifyEmailCode(email: string, code: string): Promise<void>;
  sendVerificationCode(email: string): Promise<void>;
  signOut(): Promise<void>;
  /** Best effort: never rejects; 'pending' when Neon did not confirm the deletion. */
  deleteNeonUser(): Promise<NeonDeleteResult>;
}

const MESSAGES: Record<NeonAuthFailure, string> = {
  invalid_credentials: 'Incorrect email or password.',
  email_taken: 'An account with this email already exists. Log in instead.',
  weak_password: 'Choose a stronger password.',
  invalid_email: 'Enter a valid email address.',
  bad_code: "That code didn't work. Check it, or send a new one.",
  unverified: 'Verify your email to continue.',
  rate_limited: 'Too many attempts, try again in a minute.',
  no_session:
    "Signed in, but this browser didn't keep the sign-in session. Allow cookies for this site and try again.",
  invalid_origin:
    "The sign-in service doesn't recognise this address. The site owner needs to add it as a trusted domain in Neon Auth.",
  network: "Can't reach the sign-in service. Check your connection and try again."
};

export function neonAuthMessage(e: unknown): string {
  return MESSAGES[e instanceof NeonAuthError ? e.kind : 'network'];
}

const TIMEOUT_MS = 20_000;

/** Maps a failed Better Auth response to one fixed failure class. */
function classify(status: number, code: string): NeonAuthFailure {
  if (/^USER_ALREADY_EXISTS/.test(code)) return 'email_taken';
  if (/^PASSWORD_(TOO_SHORT|TOO_LONG|COMPROMISED)/.test(code)) return 'weak_password';
  if (code === 'INVALID_EMAIL') return 'invalid_email';
  if (/OTP/.test(code)) return 'bad_code';
  if (code === 'EMAIL_NOT_VERIFIED') return 'unverified';
  if (code === 'INVALID_EMAIL_OR_PASSWORD' || code === 'INVALID_PASSWORD') return 'invalid_credentials';
  if (code === 'INVALID_ORIGIN') return 'invalid_origin';
  if (status === 429) return 'rate_limited';
  if (status === 401) return 'invalid_credentials';
  return 'network';
}

type Reply = { status: number; body: Record<string, unknown> };

export function createNeonAuth(
  base: () => string | null,
  fetchFn: typeof fetch = (input, init) => fetch(input, init)
): NeonAuthClient {
  /** Resolves with any HTTP reply; rejects only for network-level failures. */
  async function send(method: 'GET' | 'POST', path: string, body?: unknown): Promise<Reply> {
    const root = base();
    if (!root) throw new NeonAuthError('network');
    const headers: Record<string, string> = { Accept: 'application/json' };
    const init: RequestInit = { method, credentials: 'include', headers };
    if (method === 'POST') {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body ?? {});
    }
    const controller = new AbortController();
    init.signal = controller.signal;
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
      let res: Response;
      try {
        res = await fetchFn(`${root.replace(/\/+$/, '')}${path}`, init);
      } catch {
        throw new NeonAuthError('network');
      }
      let parsed: unknown = null;
      try {
        parsed = await res.json();
      } catch {
        parsed = null;
      }
      const obj = parsed !== null && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {};
      return { status: res.status, body: obj };
    } finally {
      clearTimeout(timer);
    }
  }

  /** send(), but a non-2xx reply becomes a classified NeonAuthError. */
  async function call(method: 'GET' | 'POST', path: string, body?: unknown): Promise<Record<string, unknown>> {
    const r = await send(method, path, body);
    if (r.status < 200 || r.status >= 300) {
      throw new NeonAuthError(classify(r.status, typeof r.body.code === 'string' ? r.body.code : ''));
    }
    return r.body;
  }

  return {
    async signUp(email, password) {
      // Better Auth requires a name; the part before @ is the least extra data to hand over.
      const name = email.split('@')[0] ?? '';
      const r = await call('POST', '/sign-up/email', { email, password, name });
      return { needsVerification: typeof r.token !== 'string' || r.token === '' };
    },
    async signIn(email, password) {
      await call('POST', '/sign-in/email', { email, password });
    },
    async getToken() {
      let r: Record<string, unknown>;
      try {
        r = await call('GET', '/token');
      } catch (e) {
        if (e instanceof NeonAuthError && e.kind === 'invalid_credentials') throw new NeonAuthError('no_session');
        throw e;
      }
      if (typeof r.token !== 'string' || r.token === '') throw new NeonAuthError('no_session');
      return r.token;
    },
    async verifyEmailCode(email, code) {
      await call('POST', '/email-otp/verify-email', { email, otp: code });
    },
    async sendVerificationCode(email) {
      await call('POST', '/email-otp/send-verification-otp', { email, type: 'email-verification' });
    },
    async signOut() {
      await call('POST', '/sign-out', {});
    },
    async deleteNeonUser() {
      try {
        const r = await send('POST', '/delete-user', {});
        if (r.status < 200 || r.status >= 300) return 'pending';
        // Better Auth may only email a confirmation link instead of deleting right away.
        return typeof r.body.message === 'string' && /verif|email/i.test(r.body.message) ? 'pending' : 'deleted';
      } catch {
        return 'pending';
      }
    }
  };
}

/** The app's instance; its base URL comes from /api/config at call time. Tests mock this module. */
export const neonAuth: NeonAuthClient = createNeonAuth(() => get(appConfig).neonAuthUrl);
