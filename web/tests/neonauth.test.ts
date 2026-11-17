import { afterEach, describe, expect, it, vi } from 'vitest';
import { NeonAuthError, createNeonAuth, neonAuthMessage, type NeonAuthFailure } from '../src/lib/neonAuth';

const BASE = 'https://auth.example/neondb/auth';

type Call = { url: string; init: RequestInit };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

function fake(reply: (c: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init: RequestInit) => {
    const c = { url, init };
    calls.push(c);
    return reply(c);
  });
  return { calls, client: createNeonAuth(() => BASE, fn as unknown as typeof fetch) };
}

async function failure(p: Promise<unknown>): Promise<NeonAuthFailure> {
  try {
    await p;
  } catch (e) {
    expect(e).toBeInstanceOf(NeonAuthError);
    return (e as NeonAuthError).kind;
  }
  throw new Error('expected a failure');
}

afterEach(() => vi.restoreAllMocks());

describe('neonAuth client: requests', () => {
  it('signIn posts email and password to /sign-in/email with credentials included', async () => {
    const { calls, client } = fake(() => json({ token: 't', user: { id: 'u' } }));
    await client.signIn('a@b.example', 'pw');
    expect(calls).toHaveLength(1);
    const c = calls[0]!;
    expect(c.url).toBe(`${BASE}/sign-in/email`);
    expect(c.init.method).toBe('POST');
    expect(c.init.credentials).toBe('include');
    expect(JSON.parse(String(c.init.body))).toEqual({ email: 'a@b.example', password: 'pw' });
    expect((c.init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
  });

  it('a trailing slash on the base URL is not doubled', async () => {
    const calls: Call[] = [];
    const client = createNeonAuth(() => `${BASE}/`, (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return json({});
    }) as unknown as typeof fetch);
    await client.signOut();
    expect(calls[0]!.url).toBe(`${BASE}/sign-out`);
  });

  it('signUp posts to /sign-up/email and reports whether verification is needed', async () => {
    const { calls, client } = fake((c) =>
      json(c.url.endsWith('/sign-up/email') ? { token: null, user: { id: 'u' } } : {})
    );
    expect(await client.signUp('a@b.example', 'pw')).toEqual({ needsVerification: true });
    const body = JSON.parse(String(calls[0]!.init.body));
    expect(body.email).toBe('a@b.example');
    expect(body.password).toBe('pw');
    expect(typeof body.name).toBe('string');
  });

  it('signUp with a session token needs no verification', async () => {
    const { client } = fake(() => json({ token: 'session', user: { id: 'u' } }));
    expect(await client.signUp('a@b.example', 'pw')).toEqual({ needsVerification: false });
  });

  it('getToken GETs /token and returns the JWT', async () => {
    const { calls, client } = fake(() => json({ token: 'eyJ.jwt.sig' }));
    expect(await client.getToken()).toBe('eyJ.jwt.sig');
    expect(calls[0]!.url).toBe(`${BASE}/token`);
    expect(calls[0]!.init.method).toBe('GET');
    expect(calls[0]!.init.credentials).toBe('include');
  });

  it('getToken without a token (e.g. the session cookie was blocked) is no_session', async () => {
    expect(await failure(fake(() => json({})).client.getToken())).toBe('no_session');
    expect(await failure(fake(() => json({ token: '' })).client.getToken())).toBe('no_session');
    expect(await failure(fake(() => json({ code: 'UNAUTHORIZED' }, 401)).client.getToken())).toBe('no_session');
  });

  it('verifyEmailCode posts email and otp to /email-otp/verify-email', async () => {
    const { calls, client } = fake(() => json({ status: true }));
    await client.verifyEmailCode('a@b.example', '123456');
    expect(calls[0]!.url).toBe(`${BASE}/email-otp/verify-email`);
    expect(JSON.parse(String(calls[0]!.init.body))).toEqual({ email: 'a@b.example', otp: '123456' });
  });

  it('sendVerificationCode posts to /email-otp/send-verification-otp', async () => {
    const { calls, client } = fake(() => json({ success: true }));
    await client.sendVerificationCode('a@b.example');
    expect(calls[0]!.url).toBe(`${BASE}/email-otp/send-verification-otp`);
    expect(JSON.parse(String(calls[0]!.init.body))).toEqual({ email: 'a@b.example', type: 'email-verification' });
  });

  it('signOut posts to /sign-out', async () => {
    const { calls, client } = fake(() => json({ success: true }));
    await client.signOut();
    expect(calls[0]!.url).toBe(`${BASE}/sign-out`);
    expect(calls[0]!.init.method).toBe('POST');
  });

  it('without a configured base URL nothing is fetched and the failure is network', async () => {
    const fn = vi.fn();
    const client = createNeonAuth(() => null, fn as unknown as typeof fetch);
    expect(await failure(client.signIn('a@b.example', 'pw'))).toBe('network');
    expect(fn).not.toHaveBeenCalled();
  });
});

describe('neonAuth client: deleteNeonUser is best effort', () => {
  it('2xx is deleted', async () => {
    const { calls, client } = fake(() => json({ success: true }));
    expect(await client.deleteNeonUser()).toBe('deleted');
    expect(calls[0]!.url).toBe(`${BASE}/delete-user`);
    expect(calls[0]!.init.method).toBe('POST');
  });

  it('2xx that only sent a confirmation email is pending', async () => {
    const { client } = fake(() => json({ success: true, message: 'Verification email sent' }));
    expect(await client.deleteNeonUser()).toBe('pending');
  });

  it.each([403, 404, 500])('HTTP %i is pending, not a failure', async (status) => {
    const { client } = fake(() => json({ code: 'X' }, status));
    expect(await client.deleteNeonUser()).toBe('pending');
  });

  it('a network error is pending, not a failure', async () => {
    const { client } = fake(() => {
      throw new TypeError('down');
    });
    expect(await client.deleteNeonUser()).toBe('pending');
  });
});

describe('neonAuth client: failure classes', () => {
  const cases: [string, number, unknown, NeonAuthFailure][] = [
    ['wrong password', 401, { code: 'INVALID_EMAIL_OR_PASSWORD', message: 'Invalid email or password' }, 'invalid_credentials'],
    ['unknown user', 401, { code: 'INVALID_EMAIL_OR_PASSWORD' }, 'invalid_credentials'],
    ['bare 401', 401, {}, 'invalid_credentials'],
    ['email taken', 422, { code: 'USER_ALREADY_EXISTS' }, 'email_taken'],
    ['email taken (newer code)', 422, { code: 'USER_ALREADY_EXISTS_USE_ANOTHER_EMAIL' }, 'email_taken'],
    ['short password', 400, { code: 'PASSWORD_TOO_SHORT' }, 'weak_password'],
    ['long password', 400, { code: 'PASSWORD_TOO_LONG' }, 'weak_password'],
    ['compromised password', 400, { code: 'PASSWORD_COMPROMISED' }, 'weak_password'],
    ['bad email', 400, { code: 'INVALID_EMAIL' }, 'invalid_email'],
    ['wrong code', 400, { code: 'INVALID_OTP' }, 'bad_code'],
    ['expired code', 400, { code: 'OTP_EXPIRED' }, 'bad_code'],
    ['unverified email', 403, { code: 'EMAIL_NOT_VERIFIED' }, 'unverified'],
    ['rate limited', 429, { message: 'Too many requests' }, 'rate_limited'],
    ['server error', 500, { message: 'SECRET stack trace' }, 'network'],
    ['non-JSON body', 502, 'not json', 'network']
  ];

  it.each(cases)('%s -> %s', async (_name, status, body, kind) => {
    const { client } = fake(() =>
      typeof body === 'string' ? new Response(body, { status }) : json(body, status)
    );
    const e = await failure(client.signIn('a@b.example', 'pw'));
    expect(e).toBe(kind);
  });

  it('a thrown fetch (offline, CORS, CSP block) is network', async () => {
    const { client } = fake(() => {
      throw new TypeError('Failed to fetch');
    });
    expect(await failure(client.signUp('a@b.example', 'pw'))).toBe('network');
  });

  it('verifyEmailCode wrong code is bad_code', async () => {
    const { client } = fake(() => json({ code: 'INVALID_OTP' }, 400));
    expect(await failure(client.verifyEmailCode('a@b.example', '000000'))).toBe('bad_code');
  });

  it('messages are fixed text and never echo the server', async () => {
    const { client } = fake(() => json({ code: 'WHATEVER', message: 'SECRET <b>html</b>' }, 500));
    let err: unknown;
    try {
      await client.signIn('a@b.example', 'pw');
    } catch (e) {
      err = e;
    }
    const text = neonAuthMessage(err);
    expect(text).not.toContain('SECRET');
    expect((err as Error).message).not.toContain('SECRET');
    expect(neonAuthMessage(new NeonAuthError('invalid_credentials'))).toBe('Incorrect email or password.');
    expect(neonAuthMessage(new NeonAuthError('email_taken'))).toMatch(/already/);
    expect(neonAuthMessage(new NeonAuthError('weak_password'))).toMatch(/password/i);
    expect(neonAuthMessage(new NeonAuthError('bad_code'))).toMatch(/code/i);
    expect(neonAuthMessage(new NeonAuthError('network'))).toMatch(/reach/i);
    expect(neonAuthMessage(new Error('anything'))).toBe(neonAuthMessage(new NeonAuthError('network')));
  });
});
