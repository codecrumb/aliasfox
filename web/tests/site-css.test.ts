// The public pages' stylesheet is generated (Tailwind) and committed. Fail when someone edits
// pages/site.css or the page templates without rebuilding it.
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import { expect, test } from 'vitest';

test('src/aliasfox/page_templates/site.css is current (run `npm run build:site`)', () => {
  const run = spawnSync(process.execPath, [resolve(__dirname, '../scripts/build-site.mjs'), '--check'], {
    encoding: 'utf8'
  });
  expect(run.stderr).toBe('');
  expect(run.status).toBe(0);
}, 30_000);
