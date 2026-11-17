// Scrapes FastAPI route decorators out of Python source. Not a test file (does not end in
// .test.ts); exercised by contract.test.ts, with synthetic sources for every decorator shape.

export interface Route {
  method: string;
  path: string;
  body: string | null;
}

export interface Scan {
  routes: Route[];
  /** Route decorators found by a loose scan (everything that looks like @router.x / @app.x). */
  raw: number;
  /** Raw decorators that were fully understood (each may yield several routes, e.g. api_route). */
  parsed: number;
  /** Raw decorators the parser could not understand; the completeness check must fail on any. */
  unparsed: string[];
}

const VERBS = new Set(['get', 'post', 'put', 'patch', 'delete', 'head', 'options']);
const MULTI = new Set(['api_route', 'route']);
// Decorators on router/app that are not routes.
const NOT_ROUTES = new Set(['exception_handler', 'middleware', 'on_event']);

/** Index just past the `)` that closes the `(` at `open`, skipping string literals; -1 if unbalanced. */
function closeParen(src: string, open: number): number {
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    const c = src[i]!;
    if (c === '"' || c === "'") {
      const triple = src.startsWith(c.repeat(3), i);
      const end = triple ? src.indexOf(c.repeat(3), i + 3) : src.indexOf(c, i + 1);
      if (end < 0) return -1;
      i = triple ? end + 2 : end;
    } else if (c === '#') {
      const nl = src.indexOf('\n', i);
      if (nl < 0) return -1;
      i = nl;
    } else if (c === '(') depth++;
    else if (c === ')' && --depth === 0) return i + 1;
  }
  return -1;
}

export function routerPrefix(src: string): string {
  return /APIRouter\(\s*[^)]*?prefix\s*=\s*["']([^"']*)["']/.exec(src)?.[1] ?? '';
}

export function normalise(p: string): string {
  return p.replace(/\{[^}]*\}/g, '{}');
}

export function scanRoutes(src: string): Scan {
  const prefix = routerPrefix(src);
  const scan: Scan = { routes: [], raw: 0, parsed: 0, unparsed: [] };
  for (const m of src.matchAll(/^[ \t]*@(router|app)\s*\.\s*(\w+)/gm)) {
    const [text, owner, name] = m as unknown as [string, string, string];
    if (NOT_ROUTES.has(name)) continue;
    scan.raw++;
    const unparsed = (why: string): void => {
      scan.unparsed.push(`${text.trim()}: ${why}`);
    };
    const start = m.index! + text.length;
    const open = /^\s*\(/.exec(src.slice(start));
    if (!open) {
      unparsed('no argument list');
      continue;
    }
    const openAt = start + open[0].length - 1;
    const end = closeParen(src, openAt);
    if (end < 0) {
      unparsed('unbalanced parentheses');
      continue;
    }
    const args = src.slice(openAt + 1, end - 1);
    const path = /^\s*(?:path\s*=\s*)?(["'])(.*?)\1/s.exec(args)?.[2];
    if (path === undefined) {
      unparsed('no literal path');
      continue;
    }
    let methods: string[];
    if (VERBS.has(name)) {
      methods = [name.toUpperCase()];
    } else if (MULTI.has(name)) {
      const list = /methods\s*=\s*[[({]([^\])}]*)[\])}]/.exec(args)?.[1];
      methods = Array.from(list?.matchAll(/["'](\w+)["']/g) ?? [], (x) => x[1]!.toUpperCase());
      if (methods.length === 0) {
        unparsed('no literal methods list');
        continue;
      }
    } else {
      unparsed(`unknown decorator ${name}`);
      continue;
    }
    const def = /(?:async\s+)?def\s+\w+\s*\(/.exec(src.slice(end));
    if (!def) {
      unparsed('no function after the decorator');
      continue;
    }
    const defOpen = end + def.index + def[0].length - 1;
    const defEnd = closeParen(src, defOpen);
    const params = defEnd < 0 ? '' : src.slice(defOpen + 1, defEnd - 1);
    const body = /\bbody\s*:\s*(\w+)/.exec(params)?.[1] ?? null;
    const full = normalise((owner === 'router' ? prefix : '') + path);
    for (const method of methods) scan.routes.push({ method, path: full, body });
    scan.parsed++;
  }
  return scan;
}

export function combine(scans: Scan[]): Scan {
  return {
    routes: scans.flatMap((s) => s.routes),
    raw: scans.reduce((n, s) => n + s.raw, 0),
    parsed: scans.reduce((n, s) => n + s.parsed, 0),
    unparsed: scans.flatMap((s) => s.unparsed)
  };
}
