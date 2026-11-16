import { ESLint } from 'eslint';
import { describe, expect, test } from 'vitest';

const eslint = new ESLint({ cwd: process.cwd() });

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
    'setInterval string': "export function f() { setInterval('alert(1)', 10); }"
  };
  for (const [name, code] of Object.entries(banned)) {
    test(name, async () => {
      expect((await lint(code, ts)).length).toBeGreaterThan(0);
    });
  }

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

  test('tests may read container.innerHTML', async () => {
    const code = 'export const h = (c: HTMLElement) => c.innerHTML;\n';
    expect(await lint(code, 'tests/fixture.test.ts')).toEqual([]);
  });
});
