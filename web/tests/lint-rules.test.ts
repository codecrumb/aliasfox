import { ESLint } from 'eslint';
import { resolve } from 'node:path';
import { describe, expect, test, vi } from 'vitest';

// Every test here runs ESLint (the first one also loads the whole TypeScript/Svelte toolchain),
// which is CPU heavy: allow generous time so a loaded machine cannot fail them by timeout.
vi.setConfig({ testTimeout: 30_000 });

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
    'new Function with no args': 'export const f = new Function();',
    'new globalThis.Function': 'export function f(s: string) { return new globalThis.Function(s); }',
    "new globalThis['Function']": "export function f(s: string) { return new globalThis['Function'](s); }",
    'Object.assign onto a node (identifier target)':
      "export function f(e: HTMLElement) { Object.assign(e, { title: 'x' }); }",
    'Object.assign onto a node (query result)':
      "export const f = () => Object.assign(document.body, { className: 'x' });",
    "Object['assign'] onto a node":
      "export function f(e: HTMLElement) { Object['assign'](e, { title: 'x' }); }",
    'Object.defineProperty on a node':
      "export function f(e: HTMLElement, v: string) { Object.defineProperty(e, 'innerHTML', { value: v }); }",
    'Object.defineProperties on a node':
      "export function f(e: HTMLElement, v: string) { Object.defineProperties(e, { innerHTML: { value: v } }); }",
    'Reflect.set on a node': "export function f(e: HTMLElement, k: string, v: string) { Reflect.set(e, k, v); }",
    'Reflect.defineProperty on a node':
      "export function f(e: HTMLElement, k: string, v: string) { Reflect.defineProperty(e, k, { value: v }); }",
    'setTimeout string': "export function f() { setTimeout('alert(1)', 10); }",
    'setInterval string': "export function f() { setInterval('alert(1)', 10); }",
    'computed assignment, call key':
      'export function f(e: HTMLElement, v: string) { (e as never)[String(v)] = v; }',
    'computed assignment, template key':
      'export function f(e: HTMLElement, v: string) { (e as never)[`inner${"HTML"}`] = v; }',
    'computed assignment, conditional key':
      "export function f(e: HTMLElement, v: string) { (e as never)[v ? 'a' : 'b'] = v; }",
    'computed assignment, logical key':
      "export function f(e: HTMLElement, v: string) { (e as never)[v || 'a'] = v; }",
    'computed assignment, sequence key':
      "export function f(e: HTMLElement, v: string) { (e as never)[(v, 'a')] = v; }",
    'computed assignment, template literal sink name':
      'export function f(e: HTMLElement, v: string) { (e as never)[`innerHTML`] = v; }',
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
    'setTimeout String() call': 'export function f(s: string) { setTimeout(String(s), 1); }',
    'setTimeout conditional': "export function f(s: string) { setTimeout(s ? 'a' : 'b', 1); }",
    'setTimeout logical': "export function f(s: string) { setTimeout(s || 'a', 1); }",
    'setTimeout call result': 'export function f(s: string, mk: (x: string) => never) { setTimeout(mk(s), 1); }',
    'globalThis.setTimeout string-ish':
      'export function f(s: string) { globalThis.setTimeout(String(s), 1); }',
    'window.setInterval conditional':
      "export function f(s: string) { window.setInterval(s ? 'a' : 'b', 1); }",
    'setAttributeNS href name':
      "export function f(e: Element, s: string) { e.setAttributeNS(null, 'href', s); }",
    'setAttributeNS onclick name':
      "export function f(e: Element, s: string) { e.setAttributeNS(null, 'onclick', s); }",
    'setAttributeNS srcdoc name':
      "export function f(e: Element, s: string) { e.setAttributeNS(null, 'srcdoc', s); }",
    'setAttributeNS non-literal name':
      "export function f(e: Element, n: string, s: string) { e.setAttributeNS(null, n, s); }",
    'computed href javascript: literal':
      "export function f(a: HTMLAnchorElement) { a['href'] = 'javascript:x'; }",
    'computed src data:text/html literal':
      "export function f(a: HTMLIFrameElement) { a['src'] = 'data:text/html,<b>x</b>'; }",
    'createElementNS script':
      "export const e = () => document.createElementNS('http://www.w3.org/1999/xhtml', 'script');",
    'createElementNS style':
      "export const e = () => document.createElementNS('http://www.w3.org/1999/xhtml', 'style');",
    'createElementNS iframe':
      "export const e = () => document.createElementNS('http://www.w3.org/1999/xhtml', 'iframe');",
    'createElementNS non-literal name':
      "export const e = (t: string) => document.createElementNS('http://www.w3.org/1999/xhtml', t);",
    'window.Function()': 'export function f(s: string) { return window.Function(s); }',
    'globalThis.Function()': 'export function f(s: string) { return globalThis.Function(s); }',
    'indirect eval': 'export function f(s: string) { return (0, eval)(s); }',
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
    'Object.assign into a plain object literal': 'export const f = (a: object) => Object.assign({}, a, { b: 1 });',
    'setAttribute aria-label':
      "export function f(e: HTMLElement, s: string) { e.setAttribute('aria-label', s); }",
    'setAttribute class':
      "export function f(e: HTMLElement, s: string) { e.setAttribute('class', s); }",
    "'innerHTML' inside a plain string": "export const note = 'do not use innerHTML here';",
    'array index assignment': 'export function f(a: number[], i: number, v: number) { a[i] = v; }',
    'array arithmetic index assignment':
      'export function f(a: number[], i: number, v: number) { a[i + 1] = v; }',
    'record identifier key assignment':
      'export function f(o: Record<string, string>, key: string, v: string) { o[key] = v; }',
    'numeric literal index assignment': 'export function f(m: number[], v: number) { m[0] = v; }',
    'member-expression key assignment':
      'export function f(o: Record<string, string>, k: { id: string }, v: string) { o[k.id] = v; }',
    'literal-key assignment on a record': "export function f(o: Record<string, string>) { o['a'] = 'b'; }",
    'createElement div': "export const e = () => document.createElement('div');",
    'setTimeout with arrow': 'export function f(x: () => void) { setTimeout(() => x(), 100); }',
    'setTimeout with member':
      'export const o = { tick() {}, go() { setTimeout(this.tick, 100); } };',
    'setTimeout with indexed member':
      'export function f(fns: Array<() => void>) { setTimeout(fns[0]!, 1); }',
    'window.setTimeout with arrow':
      'export function f(x: () => void) { window.setTimeout(() => x(), 1); }',
    'setAttributeNS benign xlink name':
      "export function f(e: Element, s: string) { e.setAttributeNS('http://www.w3.org/1999/xlink', 'xlink:title', s); }",
    'createElementNS svg':
      "export const e = () => document.createElementNS('http://www.w3.org/2000/svg', 'svg');",
    'createElementNS path':
      "export const e = () => document.createElementNS('http://www.w3.org/2000/svg', 'path');",
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
