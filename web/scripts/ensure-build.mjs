#!/usr/bin/env node
// Makes sure the built web bundle (src/aliasfox/static) is FRESH: built from the current sources.
// Shared by web/tests/bundle.test.ts and tests/test_web_e2e.py so a stale bundle can never pass
// the safety tests.
//
//   node scripts/ensure-build.mjs          build when missing or stale, then exit 0
//   node scripts/ensure-build.mjs --check  only report: exit 0 fresh, 3 stale, 4 missing
//   --web DIR / --index FILE               override the locations (used by tests)
//
// Exit 5: a build was needed but npm is not available (stderr says whether the bundle is
// "missing" or "stale"). Exit 1: the build failed.
import { spawnSync } from 'node:child_process';
import { existsSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const SOURCES = ['src', 'index.html', 'package.json', 'vite.config.ts', 'svelte.config.js'];
export const EXIT = { FRESH: 0, FAILED: 1, STALE: 3, MISSING: 4, NO_NPM: 5 };

const here = dirname(fileURLToPath(import.meta.url));
export const DEFAULT_WEB = resolve(here, '..');
export const DEFAULT_INDEX = resolve(DEFAULT_WEB, '../src/aliasfox/static/index.html');

/** Newest modification time (ms) of the given files / directory trees; 0 when none exist. */
export function newestMtime(paths) {
  let newest = 0;
  const visit = (p) => {
    let st;
    try {
      st = statSync(p);
    } catch {
      return;
    }
    if (st.isDirectory()) {
      for (const n of readdirSync(p)) visit(join(p, n));
    } else {
      newest = Math.max(newest, st.mtimeMs);
    }
  };
  for (const p of paths) visit(p);
  return newest;
}

/** 'missing' | 'stale' | 'fresh' */
export function bundleState(web = DEFAULT_WEB, index = DEFAULT_INDEX) {
  if (!existsSync(index)) return 'missing';
  const newestSource = newestMtime(SOURCES.map((s) => join(web, s)));
  return newestSource > statSync(index).mtimeMs ? 'stale' : 'fresh';
}

export function npmAvailable() {
  return spawnSync('npm', ['--version'], { encoding: 'utf8' }).status === 0;
}

export class NoNpmError extends Error {
  constructor(state) {
    super(`the web bundle is ${state} and npm is not available to rebuild it`);
    this.state = state;
  }
}

function defaultRun(web, args) {
  // Build exactly like `npm run build`: a test runner's NODE_ENV=test / VITEST* would make
  // Vite/Svelte emit a DEV bundle.
  const env = { ...process.env, NODE_ENV: 'production' };
  for (const k of Object.keys(env)) if (k.startsWith('VITEST')) delete env[k];
  const r = spawnSync('npm', args, { cwd: web, encoding: 'utf8', env, timeout: 280_000 });
  if (r.status !== 0) throw new Error(`npm ${args.join(' ')} failed (${r.status}):\n${r.stdout}\n${r.stderr}`);
}

/**
 * Rebuilds when the bundle is missing or older than the sources. Returns { state, built }, where
 * `state` is what it found. Throws NoNpmError when a rebuild is needed but impossible.
 * `run(web, args)` and `hasNpm()` are injectable for tests.
 */
export function ensureBuild({ web = DEFAULT_WEB, index = DEFAULT_INDEX, run = defaultRun, hasNpm = npmAvailable } = {}) {
  const state = bundleState(web, index);
  if (state === 'fresh') return { state, built: false };
  if (!hasNpm()) throw new NoNpmError(state);
  if (!existsSync(join(web, 'node_modules'))) run(web, ['ci', '--ignore-scripts']);
  run(web, ['run', 'build']);
  return { state, built: true };
}

function arg(name) {
  const i = process.argv.indexOf(name);
  return i >= 0 ? process.argv[i + 1] : undefined;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const web = arg('--web') ? resolve(arg('--web')) : DEFAULT_WEB;
  const index = arg('--index') ? resolve(arg('--index')) : DEFAULT_INDEX;
  try {
    if (process.argv.includes('--check')) {
      const s = bundleState(web, index);
      console.log(s);
      process.exit(s === 'fresh' ? EXIT.FRESH : s === 'stale' ? EXIT.STALE : EXIT.MISSING);
    }
    const { state, built } = ensureBuild({ web, index });
    console.log(built ? `rebuilt (was ${state})` : 'fresh');
  } catch (e) {
    console.error(e instanceof Error ? e.message : String(e));
    process.exit(e instanceof NoNpmError ? EXIT.NO_NPM : EXIT.FAILED);
  }
}
