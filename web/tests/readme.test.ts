// The README's keyboard shortcut table is generated from KEYMAP (see rowFor) and must not drift:
// to update it, run the tests, copy the missing rows reported here into README.md.
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { KEYMAP, type KeyBinding } from '../src/lib/keys';

const README = readFileSync(resolve(__dirname, '../../README.md'), 'utf8');

export function rowFor(b: KeyBinding): string {
  // The README covers both platforms: every alternative from either display list, once.
  const alts = [...new Set([...b.keys, ...(b.keysMac ?? [])].map((alt) => alt.map((c) => `\`${c}\``).join(' + ')))];
  const keys = alts.join(' or ');
  return `| ${keys} | ${b.label} |`;
}

describe('README', () => {
  const section = /## Keyboard shortcuts\n([\s\S]*?)(?=\n## )/.exec(README)?.[1] ?? '';
  const rows = section.split('\n').filter((l) => l.startsWith('| ') && !l.startsWith('| Keys') && !l.startsWith('| ---'));

  it('has a Keyboard shortcuts section', () => {
    expect(section).not.toBe('');
  });

  it('lists every KEYMAP binding with its exact keys and label, and nothing else', () => {
    expect(rows).toEqual(KEYMAP.map(rowFor));
  });

  it('has a Release checklist for the UI covering the manual real-browser checks', () => {
    const list = /## Release checklist for the UI\n([\s\S]*?)(?=\n## |$)/.exec(README)?.[1] ?? '';
    for (const needle of [
      'Option+N',
      'Option+E',
      'Safari',
      'Chrome',
      'Ctrl+J',
      'Windows',
      'one-time-code',
      'iOS',
      'Android',
      'reduced-motion',
      'keyboard-only',
      'CSP',
      '320',
      'Lighthouse'
    ]) {
      expect(list.toLowerCase(), needle).toContain(needle.toLowerCase());
    }
  });

  it('documents the web build commands and the output/dist directories', () => {
    for (const needle of ['npm run build', 'npm test', 'npm run lint', 'npm run check', 'WEB_OUT_DIR', 'WEB_DIST_DIR']) {
      expect(README, needle).toContain(needle);
    }
  });
});
