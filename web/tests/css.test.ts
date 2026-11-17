import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = resolve(import.meta.dirname, '..');

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

function sources(): { file: string; css: string; svelte: boolean }[] {
  const out: { file: string; css: string; svelte: boolean }[] = [];
  for (const f of walk(join(root, 'src'))) {
    const src = readFileSync(f, 'utf8');
    if (f.endsWith('.css')) out.push({ file: f, css: src, svelte: false });
    if (f.endsWith('.svelte')) {
      for (const m of src.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)) {
        out.push({ file: f, css: m[1] ?? '', svelte: true });
      }
    }
  }
  return out;
}

const KEYWORDS = new Set([
  'none', 'infinite', 'ease', 'ease-in', 'ease-out', 'ease-in-out', 'linear', 'step-start',
  'step-end', 'both', 'forwards', 'backwards', 'normal', 'reverse', 'alternate',
  'alternate-reverse', 'running', 'paused', 'initial', 'inherit', 'unset', 'revert'
]);

function defined(css: string, stripGlobal: boolean): Set<string> {
  const names = new Set<string>();
  for (const m of css.matchAll(/@keyframes\s+([^\s{]+)/g)) {
    let n = m[1] ?? '';
    if (stripGlobal) n = n.replace(/^-global-/, '');
    names.add(n);
  }
  return names;
}

function referenced(css: string): string[] {
  const refs: string[] = [];
  for (const m of css.matchAll(/(?:^|[;{\s])animation(-name)?\s*:\s*([^;}]+)/g)) {
    const value = (m[2] ?? '')
      .replace(/!important/g, '')
      .replace(/[a-z-]+\([^)]*\)/g, ' ') // var(), calc(), cubic-bezier()
      .split(/[\s,]+/)
      .filter(Boolean);
    for (const w of value) {
      if (/^[\d.]/.test(w) || KEYWORDS.has(w)) continue; // times, counts, keywords
      refs.push(w);
    }
  }
  return refs;
}

describe('CSS sources', () => {
  it('no plain .css file uses the Svelte-only -global- keyframes prefix', () => {
    for (const s of sources().filter((x) => !x.svelte)) {
      for (const n of defined(s.css, false)) expect(n, s.file).not.toMatch(/^-global-/);
    }
  });

  it('every animation refers to a defined @keyframes', () => {
    const all = sources();
    const names = new Set<string>();
    for (const s of all) for (const n of defined(s.css, s.svelte)) names.add(n);
    const refs = all.flatMap((s) => referenced(s.css));
    expect(refs.length).toBeGreaterThan(0);
    for (const r of refs) expect(names.has(r), `animation "${r}" has no @keyframes`).toBe(true);
  });

  it('built CSS (when present) has no -global- keyframes and resolved animation names', () => {
    const dir = resolve(root, '../src/aliasfox/static/assets');
    if (!existsSync(dir)) return;
    for (const f of readdirSync(dir).filter((n) => n.endsWith('.css'))) {
      const css = readFileSync(join(dir, f), 'utf8');
      const names = defined(css, false);
      for (const n of names) expect(n).not.toMatch(/^-global-/);
      for (const r of referenced(css)) expect(names.has(r), `built: ${r}`).toBe(true);
    }
  });

  it('safe-area insets are honoured by the shell and toasts; viewport-fit=cover', () => {
    const base = readFileSync(join(root, 'src/styles/base.css'), 'utf8');
    const toasts = readFileSync(join(root, 'src/components/Toasts.svelte'), 'utf8');
    const html = readFileSync(join(root, 'index.html'), 'utf8');
    for (const side of ['bottom', 'left', 'right']) {
      expect(toasts).toContain(`safe-area-inset-${side}`);
    }
    for (const side of ['left', 'right']) expect(base).toContain(`safe-area-inset-${side}`);
    expect(html).toContain('viewport-fit=cover');
  });
});
