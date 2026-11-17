// Pins the frontend to the backend: the TypeScript Alias key set, every api.* route (method +
// path) and every request-body field, all compared with the backend's own source. A backend
// route or field that changes without the client changing (or the reverse) fails here.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { api } from '../src/lib/api';

const ROOT = resolve(__dirname, '../..');
const BACKEND = join(ROOT, 'src/aliasfox');
const WEB_SRC = resolve(__dirname, '../src');
const src = (p: string): string => readFileSync(join(BACKEND, p), 'utf8');

/** `/api/aliases/{alias_id}` and `/api/aliases/ID` both normalise to `/api/aliases/{}`. */
const normalise = (p: string): string => p.replace(/\{[^}]*\}/g, '{}');

interface Route {
  method: string;
  path: string;
  body: string | null;
}

function backendRoutes(): Route[] {
  const out: Route[] = [];
  const apiPy = src('api.py');
  const prefix = /APIRouter\(prefix="([^"]*)"\)/.exec(apiPy)?.[1] ?? '';
  const re = /@router\.(get|post|put|patch|delete)\("([^"]*)"\)\s*\n\s*(?:async\s+)?def\s+\w+\(([^)]*)\)/g;
  for (const m of apiPy.matchAll(re)) {
    out.push({ method: m[1]!.toUpperCase(), path: normalise(prefix + m[2]!), body: /body:\s*(\w+)/.exec(m[3]!)?.[1] ?? null });
  }
  for (const file of ['auth.py', 'main.py']) {
    const text = src(file);
    for (const m of text.matchAll(/@app\.(get|post|put|patch|delete)\("([^"]*)"\)\s*\n\s*(?:async\s+)?def\s+\w+\(([^)]*)\)/g)) {
      out.push({ method: m[1]!.toUpperCase(), path: normalise(m[2]!), body: /body:\s*(\w+)/.exec(m[3]!)?.[1] ?? null });
    }
    for (const m of text.matchAll(/@app\.api_route\(\s*"([^"]*)"\s*,\s*methods=\[([^\]]*)\]/g)) {
      for (const meth of m[2]!.match(/"(\w+)"/g) ?? []) {
        out.push({ method: meth.replace(/"/g, ''), path: normalise(m[1]!), body: null });
      }
    }
  }
  return out;
}

/** Pydantic body models in api.py/auth.py: field name -> required? */
function bodyModels(): Map<string, Map<string, boolean>> {
  const models = new Map<string, Map<string, boolean>>();
  for (const text of [src('api.py'), src('auth.py')]) {
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
  session: [],
  status: [],
  listAliases: [],
  generate: [],
  reserve: [{ hme: 'a@icloud.com', label: 'l', note: 'n' }],
  patch: ['ID', { label: 'l', note: 'n' }],
  setActive: ['ID', true],
  remove: ['ID'],
  setForwardTo: ['me@example.com'],
  connectCookie: ['a=b'],
  connectLogin: [{ apple_id: 'me@icloud.com', password: 'pw' }],
  connectVerify: [{ flow_id: 'f', code: '123456' }],
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
        else if (/\.(ts|svelte)$/.test(n) && p !== join(WEB_SRC, 'lib/api.ts')) {
          if (/(?<![\w.])fetch\s*\(|\bXMLHttpRequest\b|\bnew\s+(WebSocket|EventSource)\b|\bsendBeacon\b/.test(readFileSync(p, 'utf8'))) offenders.push(p.slice(WEB_SRC.length + 1));
        }
      }
    };
    walk(WEB_SRC);
    expect(offenders).toEqual([]);
  });
});
