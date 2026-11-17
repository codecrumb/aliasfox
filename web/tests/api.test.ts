import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api } from '../src/lib/api';

interface Call {
  url: string;
  init: RequestInit;
}

let calls: Call[] = [];
let consoleSpies: ReturnType<typeof vi.spyOn>[] = [];

function respond(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  const text = body === undefined ? '' : typeof body === 'string' ? body : JSON.stringify(body);
  return new Response(text, { status, headers });
}

function mockFetch(handler: (url: string, init: RequestInit) => Response | Promise<Response>) {
  calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return handler(url, init);
    })
  );
}

async function catchErr(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (e) {
    return e as ApiError;
  }
  throw new Error('expected rejection');
}

beforeEach(() => {
  consoleSpies = (['log', 'info', 'warn', 'error', 'debug'] as const).map((m) =>
    vi.spyOn(console, m).mockImplementation(() => {})
  );
});

afterEach(() => {
  for (const s of consoleSpies) {
    expect(s).not.toHaveBeenCalled();
  }
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('api client', () => {
  it('test_login_posts_password_json_and_never_logs', async () => {
    mockFetch(() => respond({ ok: true }));
    await api.login('s3cret pw');
    expect(calls).toHaveLength(1);
    const { url, init } = calls[0]!;
    expect(url).toBe('/api/auth/login');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('same-origin');
    expect(init.body).toBe(JSON.stringify({ password: 's3cret pw' }));
    const h = init.headers as Record<string, string>;
    expect(h['Content-Type']).toBe('application/json');
    expect(h['Accept']).toBe('application/json');
    expect(url).not.toContain('s3cret');
    // console spies asserted in afterEach
  });

  it('test_error_detail_parsed_not_status', async () => {
    mockFetch(() => respond({ detail: 'not_connected' }, 409));
    const e = await catchErr(api.listAliases());
    expect(e).toBeInstanceOf(ApiError);
    expect(e).toBeInstanceOf(Error);
    expect(e.status).toBe(409);
    expect(e.detail).toBe('not_connected');
    expect(e.message).toBe('not_connected');
  });

  it('test_422_body_never_echoed_into_error_message', async () => {
    mockFetch(() =>
      respond(
        { detail: [{ loc: ['body', 'password'], msg: 'PASSWORD-LEAK', type: 'x', input: 'hunter2' }] },
        422
      )
    );
    const e = await catchErr(api.connectLogin({ apple_id: 'a@b.c', password: 'hunter2' }));
    expect(e.status).toBe(422);
    expect(e.detail).toBe('validation_error');
    expect(e.message).toBe('validation_error');
    expect(JSON.stringify(e)).not.toContain('hunter2');
    expect(JSON.stringify(e)).not.toContain('PASSWORD-LEAK');
    expect(e.stack ?? '').not.toContain('hunter2');
  });

  it('test_retry_after_header_parsed', async () => {
    mockFetch(() => respond({ detail: 'rate_limited' }, 429, { 'Retry-After': '42' }));
    const e = await catchErr(api.generate());
    expect(e.detail).toBe('rate_limited');
    expect(e.retryAfter).toBe(42);
  });

  it('ignores a non-integer Retry-After', async () => {
    mockFetch(() => respond({ detail: 'busy' }, 429, { 'Retry-After': 'soon' }));
    const e = await catchErr(api.generate());
    expect(e.retryAfter).toBeUndefined();
  });

  it('test_network_error_becomes_apple_unreachable_style_retry_not_crash', async () => {
    mockFetch(() => {
      throw new TypeError('Failed to fetch');
    });
    const e = await catchErr(api.status());
    expect(e).toBeInstanceOf(ApiError);
    expect(e.status).toBe(0);
    expect(e.detail).toBe('network_error');
    expect(e.message).toBe('network_error');
  });

  it('timeout produces network_error', async () => {
    vi.useFakeTimers();
    mockFetch(
      (_u, init) =>
        new Promise<Response>((_res, rej) => {
          init.signal!.addEventListener('abort', () => rej(new DOMException('aborted', 'AbortError')));
        })
    );
    const p = catchErr(api.listAliases());
    await vi.advanceTimersByTimeAsync(45_000);
    const e = await p;
    expect(e.status).toBe(0);
    expect(e.detail).toBe('network_error');
  });

  it('non-JSON or empty error body gives http_<status>', async () => {
    mockFetch(() => respond('<html>boom</html>', 502));
    expect((await catchErr(api.status())).detail).toBe('http_502');
    mockFetch(() => respond(undefined, 500));
    const e = await catchErr(api.status());
    expect(e.detail).toBe('http_500');
    expect(e.message).toBe('http_500');
  });

  it('apple_error copies server message capped to 200 chars', async () => {
    mockFetch(() => respond({ detail: 'apple_error', code: 1, message: 'x'.repeat(500) }, 502));
    const e = await catchErr(api.generate());
    expect(e.detail).toBe('apple_error');
    expect(e.serverMessage).toBe('x'.repeat(200));
    expect(e.message).toBe('apple_error');
  });

  it('table: every method hits the right method/URL/body', async () => {
    const rows: [string, () => Promise<unknown>, string, string, unknown][] = [
      ['logout', () => api.logout(), 'POST', '/api/auth/logout', undefined],
      ['session', () => api.session(), 'GET', '/api/session', undefined],
      ['status', () => api.status(), 'GET', '/api/status', undefined],
      ['list', () => api.listAliases(), 'GET', '/api/aliases', undefined],
      ['listRefresh', () => api.listAliases(true), 'GET', '/api/aliases?refresh=true', undefined],
      ['generate', () => api.generate(), 'POST', '/api/aliases/generate', undefined],
      [
        'reserve',
        () => api.reserve({ hme: 'a@icloud.com', label: 'L', note: 'N' }),
        'POST',
        '/api/aliases',
        { hme: 'a@icloud.com', label: 'L', note: 'N' }
      ],
      ['patch', () => api.patch('id1', { label: 'L', note: 'N' }), 'PATCH', '/api/aliases/id1', { label: 'L', note: 'N' }],
      ['patchNoNote', () => api.patch('id1', { label: 'L' }), 'PATCH', '/api/aliases/id1', { label: 'L' }],
      ['setActive', () => api.setActive('id1', false), 'POST', '/api/aliases/id1/active', { active: false }],
      ['remove', () => api.remove('id1'), 'DELETE', '/api/aliases/id1', undefined],
      ['forward', () => api.setForwardTo('me@x.y'), 'PUT', '/api/forward-to', { address: 'me@x.y' }],
      ['cookie', () => api.connectCookie('c=1'), 'POST', '/api/connect/cookie', { cookie: 'c=1' }],
      [
        'connectLogin',
        () => api.connectLogin({ apple_id: 'a@b.c', password: 'pw', china: true }),
        'POST',
        '/api/connect/login',
        { apple_id: 'a@b.c', password: 'pw', china: true }
      ],
      [
        'verify',
        () => api.connectVerify({ flow_id: 'f1', code: '123456' }),
        'POST',
        '/api/connect/verify',
        { flow_id: 'f1', code: '123456' }
      ],
      ['disconnect', () => api.disconnect(), 'DELETE', '/api/connect', undefined]
    ];
    for (const [name, fn, method, url, body] of rows) {
      mockFetch(() => respond(name.startsWith('list') ? [] : { ok: true }));
      await fn();
      const c = calls[0]!;
      expect(c.url, name).toBe(url);
      expect(c.init.method, name).toBe(method);
      expect(c.init.credentials, name).toBe('same-origin');
      if (body === undefined) {
        expect(c.init.body, name).toBeUndefined();
      } else {
        expect(JSON.parse(c.init.body as string), name).toEqual(body);
      }
    }
  });

  it('returns parsed bodies', async () => {
    mockFetch(() => respond({ hme: 'x@icloud.com' }));
    expect(await api.generate()).toEqual({ hme: 'x@icloud.com' });
    mockFetch(() => respond({ status: 'needs_2fa', flow_id: 'f' }));
    expect(await api.connectLogin({ apple_id: 'a', password: 'b' })).toEqual({
      status: 'needs_2fa',
      flow_id: 'f'
    });
  });

  it('ids are URL-encoded', async () => {
    mockFetch(() => respond({ ok: true }));
    await api.patch('a/b?c', { label: 'x' });
    await api.setActive('a/b?c', true);
    await api.remove('a/b?c');
    expect(calls.map((c) => c.url)).toEqual([
      '/api/aliases/a%2Fb%3Fc',
      '/api/aliases/a%2Fb%3Fc/active',
      '/api/aliases/a%2Fb%3Fc'
    ]);
  });

  it('stalled response body times out as network_error', async () => {
    vi.useFakeTimers();
    mockFetch(
      () => ({ ok: true, status: 200, headers: new Headers(), json: () => new Promise(() => {}) }) as unknown as Response
    );
    const p = catchErr(api.listAliases());
    await vi.advanceTimersByTimeAsync(45_000);
    const e = await p;
    expect(e.status).toBe(0);
    expect(e.detail).toBe('network_error');
  });

  it('2xx with invalid JSON is bad_response, not network_error', async () => {
    mockFetch(() => respond('not json', 200));
    const e = await catchErr(api.status());
    expect(e.status).toBe(200);
    expect(e.detail).toBe('bad_response');
    expect(e.message).toBe('bad_response');
  });

  it('serverMessage is capped by code points, not UTF-16 units', async () => {
    const msg = 'a'.repeat(199) + '\u{1F600}' + 'zzz';
    mockFetch(() => respond({ detail: 'apple_error', message: msg }, 502));
    const e = await catchErr(api.generate());
    expect(e.serverMessage).toBe('a'.repeat(199) + '\u{1F600}');
    expect(Array.from(e.serverMessage!)).toHaveLength(200);
  });

  it('invalid_cookie copies the server message too', async () => {
    mockFetch(() => respond({ detail: 'invalid_cookie', message: 'bad cookie' }, 400));
    const e = await catchErr(api.connectCookie('x'));
    expect(e.detail).toBe('invalid_cookie');
    expect(e.serverMessage).toBe('bad cookie');
  });

  it('other details do not carry serverMessage', async () => {
    mockFetch(() => respond({ detail: 'not_connected', message: 'leak' }, 409));
    expect((await catchErr(api.status())).serverMessage).toBeUndefined();
  });
});
