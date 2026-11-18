// Pins the frontend to the backend: the TypeScript Alias key set, every api.* route (method +
// path) and every request-body field, all compared with the backend's own source. A backend
// route or field that changes without the client changing (or the reverse) fails here.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { api } from '../src/lib/api';
import { combine, normalise, scanRoutes, type Route, type Scan } from './routes';

const ROOT = resolve(__dirname, '../..');
const BACKEND = join(ROOT, 'src/aliasfox');
const WEB_SRC = resolve(__dirname, '../src');
const src = (p: string): string => readFileSync(join(BACKEND, p), 'utf8');

function pyFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) return n === '__pycache__' || n === 'static' ? [] : pyFiles(p);
    return n.endsWith('.py') ? [p] : [];
  });
}

/** Every route decorator in the backend package (all .py files), with a completeness count. */
function backendScan(): Scan {
  return combine(pyFiles(BACKEND).map((f) => scanRoutes(readFileSync(f, 'utf8'))));
}
// Routes registered by a call instead of a decorator (the scraper only reads decorators). Each one
// is pinned to its exact registration line, so a change there fails here.
const CALL_REGISTERED: { line: string; file: string; route: Route }[] = [
  {
    // single mode only: multi mode signs in through /api/auth/exchange
    file: 'auth.py',
    line: 'app.post("/api/auth/login")(login)',
    route: { method: 'POST', path: '/api/auth/login', body: 'LoginBody' }
  }
];
const backendRoutes = (): Route[] => [
  ...backendScan().routes,
  ...CALL_REGISTERED.filter((c) => src(c.file).includes(c.line)).map((c) => c.route)
];

/** Pydantic body models in api.py/auth.py: field name -> required? */
function bodyModels(): Map<string, Map<string, boolean>> {
  const models = new Map<string, Map<string, boolean>>();
  for (const text of [src('api.py'), src('auth.py'), src('exchange.py')]) {
    for (const m of text.matchAll(/class (\w+)\(BaseModel\):\n((?:[ \t]+.*\n|\n)+)/g)) {
      const fields = new Map<string, boolean>();
      for (const line of m[2]!.split('\n')) {
        const f = /^\s{4}(\w+):\s*[^=]+?(?:=\s*(.*))?$/.exec(line);
        if (!f) continue;
        const dflt = f[2]?.trim();
        // required when there is no default, or Field(...) has no positional default
        const required = dflt === undefined || /^Field\(\s*(?:\w+\s*=|\))/.test(dflt);
        fields.set(f[1]!, required);
      }
      models.set(m[1]!, fields);
    }
  }
  return models;
}

// Backend /api routes the client deliberately does not call. Keep this honest: each entry needs a
// reason, and the test fails if an entry no longer exists in the backend.
const UNUSED_BY_CLIENT: Record<string, string> = {};

// Sample arguments for every api.* method. A new method without a sample fails the coverage test.
const SAMPLES: Record<string, unknown[]> = {
  login: ['pw'],
  logout: [],
  config: [],
  exchange: ['jwt'],
  deleteAccount: [],
  session: [],
  status: [],
  listAliases: [],
  generate: [],
  reserve: [{ hme: 'a@icloud.com', label: 'l', note: 'n' }],
  patch: ['ID', { label: 'l', note: 'n' }],
  setActive: ['ID', true],
  remove: ['ID'],
  forwardOptions: [],
  setForwardTo: ['me@example.com'],
  connectCookie: ['a=b'],
  connectLogin: [{ apple_id: 'me@icloud.com', password: 'pw' }],
  connectVerify: [{ flow_id: 'f', code: '123456' }],
  connectResend: [{ flow_id: 'f' }],
  disconnect: []
};

interface Call {
  name: string;
  method: string;
  path: string;
  body: Record<string, unknown> | null;
}

async function clientCalls(): Promise<Call[]> {
  const calls: Call[] = [];
  let current = '';
  const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
    const u = new URL(url, 'http://x');
    calls.push({
      name: current,
      method: String(init.method),
      path: normalise(u.pathname.replace('/ID', '/{}')),
      body: init.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null
    });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fetchMock);
  for (const [name, fn] of Object.entries(api)) {
    current = name;
    await (fn as (...a: unknown[]) => Promise<unknown>)(...(SAMPLES[name] ?? []));
  }
  return calls;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe('contract: Alias type', () => {
  it('has exactly the key set the backend returns', () => {
    const ts = readFileSync(join(WEB_SRC, 'lib/types.ts'), 'utf8');
    const body = /export interface Alias \{([^}]*)\}/.exec(ts)?.[1] ?? '';
    const tsKeys = Array.from(body.matchAll(/^\s*(\w+)\??:/gm), (m) => m[1]!).sort();
    const fn = /def alias_json\([^)]*\)[^:]*:\n\s*return \{([^}]*)\}/.exec(src('api.py'))?.[1] ?? '';
    const backendKeys = Array.from(fn.matchAll(/"(\w+)":/g), (m) => m[1]!).sort();
    const expected = [
      'anonymous_id', 'hme', 'label', 'note', 'is_active', 'forward_to', 'origin', 'domain', 'created_ms',
      'origin_app_name', 'app_bundle_id'
    ].sort();
    expect(backendKeys).toEqual(expected);
    expect(tsKeys).toEqual(expected);
  });
});

describe('contract: route scraper', () => {
  it('understands every route decorator in the backend (nothing silently ignored)', () => {
    const scan = backendScan();
    expect(scan.unparsed).toEqual([]);
    expect(scan.parsed).toBe(scan.raw);
    expect(scan.raw).toBeGreaterThan(15); // the scan sees the real routes
    expect(scan.routes.length).toBeGreaterThanOrEqual(scan.parsed);
  });

  const sample = (body: string) => scanRoutes(`router = APIRouter(prefix="/api")\n${body}\n`);

  it('parses single-line, multi-line, keyword-heavy and stacked decorators', () => {
    const scan = sample(`
@router.get("/a")
def a(): ...

@router.post(
    "/b/{item_id}",
    response_model=Thing,
    status_code=201,
)
async def b(item_id: str, body: BBody, user: int = Depends(u)): ...

@app.api_route(
    "/c",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
def c(): ...

@router.put("/d")
@limiter.limit("5/minute")
async def d(body: DBody): ...

@router.delete(path="/e")
def e(): ...

@app.exception_handler(Foo)
def handler(): ...

@app.middleware("http")
async def mw(): ...

@app.on_event("startup")
def start(): ...
`);
    expect(scan.unparsed).toEqual([]);
    expect(scan.raw).toBe(5);
    expect(scan.parsed).toBe(5);
    expect(scan.routes).toEqual([
      { method: 'GET', path: '/api/a', body: null },
      { method: 'POST', path: '/api/b/{}', body: 'BBody' },
      { method: 'GET', path: '/c', body: null },
      { method: 'HEAD', path: '/c', body: null },
      { method: 'PUT', path: '/api/d', body: 'DBody' },
      { method: 'DELETE', path: '/api/e', body: null }
    ]);
  });

  it('does not count a path or ) inside a string, a comment or the parameter list', () => {
    const scan = sample(`
@router.get("/a)b")  # a comment ) with (
def a(x: str = "(", y: int = 1): ...
`);
    expect(scan.unparsed).toEqual([]);
    expect(scan.routes).toEqual([{ method: 'GET', path: '/api/a)b', body: null }]);
  });

  it.each([
    ['a path that is a variable', '@router.get(PATH)\ndef a(): ...'],
    ['a decorator without an argument list', '@router.get\ndef a(): ...'],
    ['an unknown route-ish decorator', '@router.websocket("/ws")\nasync def ws(): ...'],
    ['api_route without a literal methods list', '@app.api_route("/x", methods=METHODS)\ndef x(): ...'],
    ['unbalanced parentheses', '@router.get("/x"\ndef x(): ...']
  ])('a deliberately weird decorator (%s) is COUNTED and flagged unparsed', (_n, body) => {
    const scan = sample(body);
    expect(scan.raw).toBe(1);
    expect(scan.unparsed.length).toBe(1);
    expect(scan.parsed).toBe(0);
    // The completeness assertion used on the real backend would fail for this sample:
    expect(scan.unparsed.length === 0 && scan.parsed === scan.raw).toBe(false);
  });
});

describe('contract: client routes', () => {
  it('has a sample for every api.* method and no stale samples', () => {
    expect(Object.keys(SAMPLES).sort()).toEqual(Object.keys(api).sort());
  });

  it('every api.* method and path exists in the backend', async () => {
    const backend = new Set(backendRoutes().map((r) => `${r.method} ${r.path}`));
    expect(backend.size).toBeGreaterThan(10); // the route scraper itself works
    const missing = (await clientCalls())
      .map((c) => `${c.method} ${c.path}`)
      .filter((k) => !backend.has(k));
    expect(missing).toEqual([]);
  });

  it('every backend /api route is used by the client or explicitly allow-listed with a reason', async () => {
    const used = new Set((await clientCalls()).map((c) => `${c.method} ${c.path}`));
    const routes = backendRoutes().filter((r) => r.path.startsWith('/api/'));
    const unused = routes.map((r) => `${r.method} ${r.path}`).filter((k) => !used.has(k));
    const allowed = new Set(Object.keys(UNUSED_BY_CLIENT).map((k) => `${k.split(' ')[0]} ${normalise(k.split(' ')[1]!)}`));
    expect(unused.filter((k) => !allowed.has(k))).toEqual([]);
    for (const [k, reason] of Object.entries(UNUSED_BY_CLIENT)) {
      expect(reason.length, k).toBeGreaterThan(10);
      expect(routes.some((r) => `${r.method} ${r.path}` === `${k.split(' ')[0]} ${normalise(k.split(' ')[1]!)}`), `${k} no longer exists`).toBe(true);
      expect(used.has(`${k.split(' ')[0]} ${normalise(k.split(' ')[1]!)}`), `${k} is used now: remove it from the allowlist`).toBe(false);
    }
  });

  it('request bodies carry only fields the backend model has, and every required one', async () => {
    const models = bodyModels();
    const routes = backendRoutes();
    for (const c of await clientCalls()) {
      const route = routes.find((r) => r.method === c.method && r.path === c.path);
      expect(route, `${c.method} ${c.path}`).toBeDefined();
      if (route?.body == null) {
        expect(c.body, `${c.name} sends a body to a route without one`).toBeNull();
        continue;
      }
      const model = models.get(route.body);
      expect(model, `model ${route.body}`).toBeDefined();
      const sent = Object.keys(c.body ?? {});
      const unknown = sent.filter((k) => !model!.has(k));
      expect(unknown, `${c.name}: fields the backend does not know`).toEqual([]);
      const required = Array.from(model!.entries()).filter(([, r]) => r).map(([k]) => k);
      expect(required.filter((k) => !sent.includes(k)), `${c.name}: missing required fields`).toEqual([]);
    }
  });

  it('only lib/api.ts talks to the network', () => {
    const offenders: string[] = [];
    const walk = (dir: string): void => {
      for (const n of readdirSync(dir)) {
        const p = join(dir, n);
        if (statSync(p).isDirectory()) walk(p);
        // lib/neonAuth.ts is the one other client: it talks only to the Neon Auth origin (multi mode).
        else if (/\.(ts|svelte)$/.test(n) && p !== join(WEB_SRC, 'lib/api.ts') && p !== join(WEB_SRC, 'lib/neonAuth.ts')) {
          if (/(?<![\w.])fetch\s*\(|\bXMLHttpRequest\b|\bnew\s+(WebSocket|EventSource)\b|\bsendBeacon\b/.test(readFileSync(p, 'utf8'))) offenders.push(p.slice(WEB_SRC.length + 1));
        }
      }
    };
    walk(WEB_SRC);
    expect(offenders).toEqual([]);
  });
});
