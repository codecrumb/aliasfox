// Asserts on the BUILT bundle (src/aliasfox/static), not on the sources: the CSP only holds if
// what actually ships has no inline script/style, no eval-like sinks and no stray HTML parsing.
//
// If the bundle is missing this test builds it (npm ci --ignore-scripts first only when
// web/node_modules is missing). It skips, with a stated reason, only when there is neither a
// bundle nor an npm to build one with. When a bundle exists it always runs.
import { describe, expect, it } from 'vitest';
import { spawnSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';

const WEB = resolve(__dirname, '..');
const DIST = resolve(WEB, '../src/aliasfox/static');
const INDEX = join(DIST, 'index.html');

function run(args: string[]): void {
  // The vitest worker sets NODE_ENV=test (and VITEST*), which would make Vite/Svelte emit a DEV
  // build (unminified runtime, dev-only code paths): build exactly like `npm run build` does.
  const env: NodeJS.ProcessEnv = { ...process.env, NODE_ENV: 'production' };
  for (const k of Object.keys(env)) if (k.startsWith('VITEST')) delete env[k];
  const r = spawnSync('npm', args, { cwd: WEB, encoding: 'utf8', timeout: 280_000, env });
  if (r.status !== 0) {
    throw new Error(`npm ${args.join(' ')} failed (${r.status}):\n${r.stdout}\n${r.stderr}`);
  }
}

function ensureBundle(): string | null {
  if (existsSync(INDEX)) return null;
  if (spawnSync('npm', ['--version'], { encoding: 'utf8' }).status !== 0) {
    return `no built bundle at ${INDEX} and npm is not on PATH, so it cannot be built`;
  }
  if (!existsSync(join(WEB, 'node_modules'))) run(['ci', '--ignore-scripts']);
  run(['run', 'build']);
  if (!existsSync(INDEX)) throw new Error('npm run build did not produce index.html');
  return null;
}

const skipReason = ensureBundle();
const suite = skipReason === null ? describe : describe.skip;
if (skipReason !== null) console.warn(`bundle.test.ts skipped: ${skipReason}`);

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? files(p) : [p];
  });
}

const read = (p: string): string => readFileSync(p, 'utf8');
const rel = (p: string): string => p.slice(DIST.length + 1);

suite('built bundle', () => {
  const all = existsSync(DIST) ? files(DIST) : [];
  const js = all.filter((f) => f.endsWith('.js'));
  const css = all.filter((f) => f.endsWith('.css'));
  const html = read(INDEX);

  it('contains a shell, at least one JS file and one CSS file', () => {
    expect(js.length).toBeGreaterThan(0);
    expect(css.length).toBeGreaterThan(0);
  });

  describe('index.html', () => {
    const doc = new DOMParser().parseFromString(html, 'text/html');

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
    it('has no eval( or new Function', () => {
      for (const f of js) {
        const t = read(f);
        expect(t, rel(f)).not.toMatch(/(?<![\w$.])eval\s*\(/);
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

    it('references no remote URL other than inert namespace and Svelte error-doc strings', () => {
      const allowed = [/^http:\/\/www\.w3\.org\/\d{4}\/[\w/]+$/, /^https:\/\/svelte\.dev\/e\/[a-z_]+$/];
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
