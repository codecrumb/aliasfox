// Asserts on the BUILT bundle (src/aliasfox/static), not on the sources: the CSP only holds if
// what actually ships has no inline script/style, no eval-like sinks and no stray HTML parsing.
//
// If the bundle is missing this test builds it (npm ci --ignore-scripts first only when
// web/node_modules is missing). It skips, with a stated reason, only when there is neither a
// bundle nor an npm to build one with. When a bundle exists it always runs.
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { NoNpmError, ensureBuild } from '../scripts/ensure-build.mjs';

const WEB = resolve(__dirname, '..');
const DIST = resolve(WEB, '../src/aliasfox/static');
const INDEX = join(DIST, 'index.html');
const BUILD_TIMEOUT = 300_000;

/**
 * Finds any `eval` identifier outside comments: direct calls, `(0, eval)(x)`, `window.eval(x)`,
 * `globalThis.eval`, and aliasing such as `a = eval;`. Longer words (`retrieval`, `evaluate`,
 * `$eval`) do not match.
 */
export function findEval(code: string): string | null {
  const noComments = code.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[\s;{}])\/\/[^\n]*/g, '$1 ');
  const m = /(?<![\w$])eval(?![\w$])/.exec(noComments);
  return m ? noComments.slice(Math.max(0, m.index - 20), m.index + 20) : null;
}

// Freshness: a bundle older than the sources is rebuilt (scripts/ensure-build.mjs); if a rebuild is
// needed but npm is unavailable, a STALE bundle fails the run and only a MISSING one skips.
let skipReason: string | null = null;
beforeAll(() => {
  try {
    ensureBuild();
  } catch (e) {
    if (e instanceof NoNpmError && e.state === 'missing') {
      skipReason = `no built bundle at ${INDEX} and npm is not available to build it`;
      console.warn(`bundle.test.ts skipped: ${skipReason}`);
      return;
    }
    throw e;
  }
  expect(existsSync(INDEX)).toBe(true);
}, BUILD_TIMEOUT);

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}

const read = (p: string): string => readFileSync(p, 'utf8');
const rel = (p: string): string => p.slice(DIST.length + 1);

describe('findEval (the detector used below)', () => {
  it.each([
    'x=eval("1")',
    'eval (x)',
    '(0,eval)("x")',
    '(0, eval)(code)',
    'window.eval(x)',
    'globalThis.eval(x)',
    'self.eval(x)',
    'var a=eval;a(1)',
    'f(eval)',
    '/* ok */ eval(x)',
    'var a = 1; // note\neval(x)'
  ])('flags %s', (code) => {
    expect(findEval(code)).not.toBeNull();
  });

  it.each(['retrieval(x)', 'evaluate(x)', '$eval(x)', 'my_eval(x)', '/* eval( */ x()', '// eval(x)\ny()', 'var s="a"; /* eval */'])(
    'ignores %s',
    (code) => {
      expect(findEval(code)).toBeNull();
    }
  );
});

describe('built bundle', () => {
  let all: string[] = [];
  let js: string[] = [];
  let css: string[] = [];
  let html = '';
  let doc: Document;
  beforeEach((ctx) => {
    if (skipReason) ctx.skip(skipReason);
  });
  beforeAll(() => {
    if (skipReason) return;
    all = files(DIST);
    js = all.filter((f) => f.endsWith('.js'));
    css = all.filter((f) => f.endsWith('.css'));
    html = read(INDEX);
    doc = new DOMParser().parseFromString(html, 'text/html');
  });

  it('contains a shell, at least one JS file and one CSS file', () => {
    expect(js.length).toBeGreaterThan(0);
    expect(css.length).toBeGreaterThan(0);
  });

  describe('index.html', () => {
    it('has only external module scripts (no inline script)', () => {
      const scripts = Array.from(doc.querySelectorAll('script'));
      expect(scripts.length).toBeGreaterThan(0);
      for (const s of scripts) {
        expect(s.getAttribute('src')).toMatch(/^\/assets\/[^/]+\.js$/);
        expect(s.getAttribute('type')).toBe('module');
        expect((s.textContent ?? '').trim()).toBe('');
      }
    });

    it('has no <style> element, style= attribute or on*= handler', () => {
      expect(doc.querySelectorAll('style').length).toBe(0);
      const bad: string[] = [];
      for (const el of Array.from(doc.querySelectorAll('*'))) {
        for (const a of Array.from(el.attributes)) {
          if (a.name === 'style' || a.name.startsWith('on')) bad.push(`${el.tagName}.${a.name}`);
        }
      }
      expect(bad).toEqual([]);
    });

    it('has no remote or protocol-relative URL and no javascript: URL', () => {
      expect(html).not.toMatch(/https?:/i);
      expect(html).not.toMatch(/(?<!:)\/\/[a-z0-9.-]+/i);
      expect(html.toLowerCase()).not.toContain('javascript:');
      for (const el of Array.from(doc.querySelectorAll('[src], [href]'))) {
        const v = el.getAttribute('src') ?? el.getAttribute('href') ?? '';
        expect(v.startsWith('/') && !v.startsWith('//')).toBe(true);
      }
    });
  });

  describe('CSS', () => {
    it('has no @import and no remote url()', () => {
      for (const f of css) {
        const text = read(f);
        expect(text, rel(f)).not.toContain('@import');
        for (const m of text.matchAll(/url\(\s*['"]?\s*([^'")\s]+)/gi)) {
          expect(m[1], rel(f)).not.toMatch(/^(https?:)?\/\//i);
        }
        expect(text, rel(f)).not.toMatch(/https?:/i);
      }
    });
  });

  describe('JS', () => {
    it('has no eval (call, indirect, window.eval, alias) or new Function', () => {
      for (const f of js) {
        const t = read(f);
        expect(findEval(t), rel(f)).toBeNull();
        expect(t, rel(f)).not.toMatch(/new\s+Function\b/);
        expect(t, rel(f)).not.toMatch(/(?<![\w$.])Function\s*\(/);
      }
    });

    it('has no document.write, javascript: URL, runtime stylesheet injection or HTML-parsing sinks other than the Svelte one', () => {
      for (const f of js) {
        const t = read(f);
        expect(t, rel(f)).not.toMatch(/document\s*\.\s*write(ln)?\s*\(/);
        expect(t.toLowerCase(), rel(f)).not.toContain('javascript:');
        expect(t, rel(f)).not.toContain('insertRule');
        expect(t, rel(f)).not.toContain('<style');
        expect(t, rel(f)).not.toMatch(/createElement\(\s*[`"']style[`"']\s*\)/);
        expect(t, rel(f)).not.toContain('outerHTML');
        expect(t, rel(f)).not.toContain('insertAdjacentHTML');
        expect(t, rel(f)).not.toContain('createContextualFragment');
        expect(t, rel(f)).not.toContain('srcdoc');
        expect(t, rel(f)).not.toContain('DOMParser');
        expect(t, rel(f)).not.toContain('importScripts');
      }
    });

    // The Svelte 5 runtime legitimately contains `innerHTML` in exactly TWO places (verified
    // against the built output with Svelte 5.57.3; svelte/src/internal/client):
    //
    //  1. create_fragment_from_html(): parses the COMPILER-GENERATED static template markup
    //     (the literal HTML of our .svelte templates, never user data):
    //       function Tr(e){var t=sn(`template`);return t.innerHTML=wr(e.replaceAll(`<!>`,`<!---->`)),t.content}
    //     (`wr` is the optional Trusted Types createHTML wrapper). This is the ONLY assignment.
    //  2. a property filter in the runtime's setters list, which only COMPARES the name and never
    //     writes it:
    //       r[s].set&&s!==`innerHTML`&&s!==`textContent`&&s!==`innerText`
    //
    // Any other `innerHTML` (for example `{@html}` or an app-code assignment) changes the count or
    // the shape and fails this test. If a Svelte upgrade changes these, review the new snippet
    // and update the shapes here deliberately.
    it('contains innerHTML only in the two known Svelte-internal places', () => {
      const id = '[\\w$]+';
      const templateAssign = new RegExp(
        `function ${id}\\(${id}\\)\\{var ${id}=${id}\\(\`template\`\\);return ${id}\\.innerHTML=${id}\\(${id}\\.replaceAll\\(\`<!>\`,\`<!---->\`\\)\\),${id}\\.content\\}`,
        'g'
      );
      const setterFilter = /&&[\w$]+!==`innerHTML`&&[\w$]+!==`textContent`&&[\w$]+!==`innerText`/g;
      let total = 0;
      let assigns = 0;
      for (const f of js) {
        const t = read(f);
        total += (t.match(/innerHTML/g) ?? []).length;
        assigns += (t.match(/\.innerHTML\s*=(?!=)/g) ?? []).length;
        expect((t.match(templateAssign) ?? []).length, `${rel(f)} template snippet`).toBe(
          (t.match(/\.innerHTML\s*=(?!=)/g) ?? []).length
        );
      }
      const known = js.reduce(
        (n, f) => n + (read(f).match(templateAssign) ?? []).length + (read(f).match(setterFilter) ?? []).length,
        0
      );
      expect(assigns).toBe(1); // exactly one assignment, and it is the Svelte template one
      expect(total).toBe(2); // that assignment + the setter-name filter
      expect(known).toBe(2); // and both match their known shapes
    });

    it('references no remote URL other than inert namespaces, Svelte error docs, and the iCloud+ link', () => {
      const allowed = [
        /^http:\/\/www\.w3\.org\/\d{4}\/[\w/]+$/,
        /^https:\/\/svelte\.dev\/e\/[a-z_]+$/,
        // The one deliberate outbound link: where to subscribe to iCloud+.
        /^https:\/\/www\.apple\.com\/icloud\/$/
      ];
      for (const f of js) {
        for (const m of read(f).matchAll(/https?:\/\/[^\s"'`)\\]+/g)) {
          expect(allowed.some((a) => a.test(m[0])), `${rel(f)}: ${m[0]}`).toBe(true);
        }
      }
    });
  });

  describe('source maps', () => {
    it('ships no .map files and no sourceMappingURL', () => {
      expect(all.filter((f) => f.endsWith('.map')).map(rel)).toEqual([]);
      for (const f of all.filter((x) => /\.(js|css|html)$/.test(x))) {
        expect(read(f), rel(f)).not.toMatch(/sourceMappingURL/);
      }
    });
  });
});
