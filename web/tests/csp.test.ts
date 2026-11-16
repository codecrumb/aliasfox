import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

describe('CSP source rules', () => {
  const files = walk(resolve(import.meta.dirname, '../src')).filter((f) => f.endsWith('.svelte'));

  it('finds the components', () => {
    expect(files.length).toBeGreaterThanOrEqual(4);
  });

  it('no authored style="" attributes or Svelte transition directives', () => {
    for (const f of files) {
      const src = readFileSync(f, 'utf8');
      expect(src, f).not.toMatch(/\sstyle\s*=/);
      expect(src, f).not.toMatch(/\s(transition|animate|in|out):\w/);
    }
  });
});
