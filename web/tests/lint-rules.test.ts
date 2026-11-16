import { ESLint } from 'eslint';
import { resolve } from 'node:path';
import { describe, expect, test } from 'vitest';

const eslint = new ESLint({ cwd: resolve(import.meta.dirname, '..') });

async function lint(code: string, filePath: string): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath });
  return (result?.messages ?? []).map((m) => `${m.ruleId ?? 'parse'}: ${m.message}`);
}

const ts = 'src/fixture.ts';
const svelte = 'src/Fixture.svelte';

describe('banned patterns are rejected in src', () => {
  const banned: Record<string, string> = {
    'innerHTML assignment': 'export function f(e: HTMLElement, s: string) { e.innerHTML = s; }',
    'outerHTML assignment': 'export function f(e: HTMLElement, s: string) { e.outerHTML = s; }',
    'bracket innerHTML assignment':
      "export function f(e: HTMLElement, s: string) { e['innerHTML'] = s; }",
    'insertAdjacentHTML call':
      "export function f(e: HTMLElement, s: string) { e.insertAdjacentHTML('beforeend', s); }",
    'document.write': 'export function f(s: string) { document.write(s); }',
    'document.writeln': 'export function f(s: string) { document.writeln(s); }',
    eval: 'export function f(s: string) { return eval(s); }',
    'new Function': "export function f(s: string) { return new Function(s); }",
    'setTimeout string': "export function f() { setTimeout('alert(1)', 10); }",
    'setInterval string': "export function f() { setInterval('alert(1)', 10); }",
    'computed assignment, variable key':
      'export function f(e: HTMLElement, k: string, v: string) { (e as never)[k] = v; }',
    'computed assignment, concatenated key':
      "export function f(e: HTMLElement, v: string) { (e as never)['inner' + 'HTML'] = v; }",
    'object literal innerHTML key':
      'export const o = (s: string) => ({ innerHTML: s });',
    'object literal quoted outerHTML key':
      "export const o = (s: string) => ({ 'outerHTML': s });",
    'Object.assign with srcdoc':
      'export function f(e: HTMLElement, s: string) { Object.assign(e, { srcdoc: s }); }',
    'Object.assign with innerHTML':
      'export function f(e: HTMLElement, s: string) { Object.assign(e, { innerHTML: s }); }',
    setHTMLUnsafe: 'export function f(e: any, s: string) { e.setHTMLUnsafe(s); }',
    parseHTMLUnsafe: 'export function f(s: string) { return (Document as any).parseHTMLUnsafe(s); }',
    createContextualFragment:
      'export function f(r: Range, s: string) { return r.createContextualFragment(s); }',
    parseFromString:
      "export function f(s: string) { return new DOMParser().parseFromString(s, 'text/html'); }",
    execCommand: "export function f(s: string) { document.execCommand('insertHTML', false, s); }",
    'srcdoc assignment': 'export function f(e: HTMLIFrameElement, s: string) { e.srcdoc = s; }',
    'setAttribute srcdoc':
      "export function f(e: HTMLElement, s: string) { e.setAttribute('srcdoc', s); }",
    'setAttribute href':
      "export function f(e: HTMLElement, s: string) { e.setAttribute('href', s); }",
    'setAttribute src': "export function f(e: HTMLElement, s: string) { e.setAttribute('src', s); }",
    'setAttribute action':
      "export function f(e: HTMLElement, s: string) { e.setAttribute('action', s); }",
    'setAttribute formaction':
      "export function f(e: HTMLElement, s: string) { e.setAttribute('formaction', s); }",
    'setAttribute onclick':
      "export function f(e: HTMLElement, s: string) { e.setAttribute('onclick', s); }",
    'setAttribute non-literal name':
      'export function f(e: HTMLElement, n: string, s: string) { e.setAttribute(n, s); }',
    'href javascript: literal':
      "export function f(a: HTMLAnchorElement) { a.href = 'javascript:alert(1)'; }",
    'src data:text/html literal':
      "export function f(a: HTMLIFrameElement) { a.src = 'data:text/html,<b>x</b>'; }",
    'createElement script': "export const e = () => document.createElement('script');",
    'createElement style': "export const e = () => document.createElement('style');",
    'createElement iframe': "export const e = () => document.createElement('iframe');",
    'createElement object': "export const e = () => document.createElement('object');",
    'createElement embed': "export const e = () => document.createElement('embed');",
    'createElement non-literal':
      'export const e = (t: string) => document.createElement(t);',
    "window['eval']": "export function f(s: string) { return (window as any)['eval'](s); }",
    'globalThis.eval': 'export function f(s: string) { return (globalThis as any).eval(s); }',
    'Function() without new': "export function f(s: string) { return Function(s); }",
    'setTimeout template literal': 'export function f(s: string) { setTimeout(`${s}`, 1); }',
    'setTimeout concatenation': "export function f(s: string) { setTimeout('a' + s, 1); }",
    'window.setInterval string': "export function f() { window.setInterval('x()', 1); }"
  };
  for (const [name, code] of Object.entries(banned)) {
    test(name, async () => {
      // Ignore style noise (e.g. no-explicit-any in fixtures): the ban itself must fire.
      const banMessages = (await lint(code, ts)).filter((m) => !m.startsWith('@typescript-eslint/'));
      expect(banMessages.length).toBeGreaterThan(0);
    });
  }

  test('{@html} inside a snippet', async () => {
    const code =
      '<script lang="ts">let s = "x";</script>\n{#snippet row()}<p>{@html s}</p>{/snippet}\n{@render row()}\n';
    const msgs = await lint(code, svelte);
    expect(msgs.some((m) => m.startsWith('svelte/no-at-html-tags'))).toBe(true);
  });

  test('<svelte:element>', async () => {
    const code =
      '<script lang="ts">let t = "div";</script>\n<svelte:element this={t}>x</svelte:element>\n';
    const msgs = await lint(code, svelte);
    expect(msgs.some((m) => m.startsWith('no-restricted-syntax'))).toBe(true);
  });

  test('{@html} in a svelte component', async () => {
    const msgs = await lint('<script lang="ts">let s = "x";</script>\n{@html s}\n', svelte);
    expect(msgs.some((m) => m.startsWith('svelte/no-at-html-tags'))).toBe(true);
  });
});

describe('allowed patterns pass', () => {
  test('textContent assignment', async () => {
    const code = 'export function f(e: HTMLElement, s: string) { e.textContent = s; }\n';
    expect(await lint(code, ts)).toEqual([]);
  });

  test('setTimeout with a function', async () => {
    const code = 'export function f() { setTimeout(() => {}, 10); }\n';
    expect(await lint(code, ts)).toEqual([]);
  });

  const allowed: Record<string, string> = {
    'setAttribute aria-label':
      "export function f(e: HTMLElement, s: string) { e.setAttribute('aria-label', s); }",
    'setAttribute class':
      "export function f(e: HTMLElement, s: string) { e.setAttribute('class', s); }",
    "'innerHTML' inside a plain string": "export const note = 'do not use innerHTML here';",
    'literal-key assignment on a record': "export function f(o: Record<string, string>) { o['a'] = 'b'; }",
    'createElement div': "export const e = () => document.createElement('div');",
    'setTimeout with identifier': 'export function f(cb: () => void) { setTimeout(cb, 1); }',
    'href to a relative literal':
      "export function f(a: HTMLAnchorElement) { a.href = '/assets/x'; }",
    'object literal with normal keys': 'export const o = { text: 1, name: "x" };'
  };
  for (const [name, code] of Object.entries(allowed)) {
    test(name, async () => {
      expect(await lint(code + '\n', ts)).toEqual([]);
    });
  }

  test('tests may read container.innerHTML', async () => {
    const code = 'export const h = (c: HTMLElement) => c.innerHTML;\n';
    expect(await lint(code, 'tests/fixture.test.ts')).toEqual([]);
  });
});
