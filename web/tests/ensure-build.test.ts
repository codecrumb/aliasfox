// The freshness rule shared by bundle.test.ts and tests/test_web_e2e.py (scripts/ensure-build.mjs):
// a built bundle older than the sources must be rebuilt, never trusted.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { EXIT, NoNpmError, bundleState, ensureBuild, newestMtime } from '../scripts/ensure-build.mjs';

const SCRIPT = resolve(__dirname, '../scripts/ensure-build.mjs');
let root: string;
let web: string;
let index: string;

const at = (p: string, seconds: number) => utimesSync(p, seconds, seconds);
function put(rel: string, seconds: number) {
  const p = join(web, rel);
  mkdirSync(join(p, '..'), { recursive: true });
  writeFileSync(p, 'x');
  at(p, seconds);
  return p;
}

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), 'ensure-build-'));
  web = join(root, 'web');
  mkdirSync(web);
  index = join(root, 'static', 'index.html');
  mkdirSync(join(root, 'static'));
  mkdirSync(join(web, 'node_modules'));
  for (const f of ['src/main.ts', 'src/lib/deep/x.ts', 'index.html', 'package.json', 'package-lock.json', 'vite.config.ts', 'svelte.config.js']) put(f, 1000);
  writeFileSync(index, '<html>');
  at(index, 2000);
});
afterEach(() => rmSync(root, { recursive: true, force: true }));

describe('bundleState', () => {
  it('is fresh when the bundle is newer than every source', () => {
    expect(bundleState(web, index)).toBe('fresh');
  });

  it('is missing without an index.html', () => {
    rmSync(index);
    expect(bundleState(web, index)).toBe('missing');
  });

  it.each(['src/main.ts', 'src/lib/deep/x.ts', 'index.html', 'package.json', 'package-lock.json', 'vite.config.ts', 'svelte.config.js'])(
    'is stale when %s is touched after the bundle',
    (rel) => {
      at(join(web, rel), 3000);
      expect(bundleState(web, index)).toBe('stale');
    }
  );

  it('ignores files that are not build inputs', () => {
    put('tests/a.test.ts', 9000);
    put('README.md', 9000);
    expect(bundleState(web, index)).toBe('fresh');
  });

  it('newestMtime copes with missing paths', () => {
    expect(newestMtime([join(web, 'nope')])).toBe(0);
  });
});

describe('ensureBuild', () => {
  it('does nothing for a fresh bundle', () => {
    const run = vi.fn();
    expect(ensureBuild({ web, index, run })).toEqual({ state: 'fresh', built: false });
    expect(run).not.toHaveBeenCalled();
  });

  it('rebuilds a stale bundle (npm run build only, node_modules exists)', () => {
    at(join(web, 'src/main.ts'), 3000);
    const run = vi.fn();
    expect(ensureBuild({ web, index, run, hasNpm: () => true })).toEqual({ state: 'stale', built: true });
    expect(run).toHaveBeenCalledExactlyOnceWith(web, ['run', 'build']);
  });

  it('builds a missing bundle, installing first only when node_modules is absent', () => {
    rmSync(index);
    rmSync(join(web, 'node_modules'), { recursive: true });
    const run = vi.fn();
    ensureBuild({ web, index, run, hasNpm: () => true });
    expect(run.mock.calls).toEqual([
      [web, ['ci', '--ignore-scripts']],
      [web, ['run', 'build']]
    ]);
  });

  it('FAILS (does not skip) for a stale bundle when npm is unavailable', () => {
    at(join(web, 'src/main.ts'), 3000);
    expect(() => ensureBuild({ web, index, run: vi.fn(), hasNpm: () => false })).toThrow(NoNpmError);
    try {
      ensureBuild({ web, index, run: vi.fn(), hasNpm: () => false });
    } catch (e) {
      expect((e as NoNpmError).state).toBe('stale');
    }
  });

  it('reports a missing bundle without npm as state=missing (callers may skip)', () => {
    rmSync(index);
    try {
      ensureBuild({ web, index, run: vi.fn(), hasNpm: () => false });
      expect.unreachable();
    } catch (e) {
      expect((e as NoNpmError).state).toBe('missing');
    }
  });
});

describe('CLI (used by the Python e2e test)', () => {
  const cli = (...a: string[]) => spawnSync('node', [SCRIPT, '--web', web, '--index', index, ...a], { encoding: 'utf8' });

  it('--check exits 0 fresh, 3 stale, 4 missing', () => {
    expect(cli('--check').status).toBe(EXIT.FRESH);
    at(join(web, 'index.html'), 3000);
    expect(cli('--check').status).toBe(EXIT.STALE);
    rmSync(index);
    expect(cli('--check').status).toBe(EXIT.MISSING);
  });

  it('without --check a fresh bundle exits 0 and builds nothing', () => {
    const r = cli();
    expect(r.status).toBe(0);
    expect(r.stdout.trim()).toBe('fresh');
  });
});
