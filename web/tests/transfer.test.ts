import { describe, expect, it } from 'vitest';
import { parseCsv, planImport, statsOf, toCsv } from '../src/lib/transfer';
import type { Alias } from '../src/lib/types';

function alias(over: Partial<Alias> = {}): Alias {
  return {
    anonymous_id: 'id1',
    hme: 'a.b@icloud.com',
    label: 'Shop',
    note: null,
    is_active: true,
    forward_to: null,
    origin: null,
    domain: null,
    created_ms: null,
    origin_app_name: null,
    app_bundle_id: null,
    ...over
  };
}

describe('csv', () => {
  it('round-trips quotes, commas and newlines', () => {
    const a = alias({ label: 'a, "b"', note: 'line1\nline2' });
    expect(parseCsv(toCsv([a]))[1]).toEqual([a.hme, 'a, "b"', 'line1\nline2', 'yes', '', '']);
  });

  it('prefixes formula-looking cells and import strips it again', () => {
    const a = alias({ label: '=SUM(A1)' });
    const csv = toCsv([a]);
    expect(csv).toContain("'=SUM(A1)");
    const plan = planImport(csv.replace('Shop', 'x'), [alias({ label: 'old' })]);
    expect(plan.updates[0]?.label).toBe('=SUM(A1)');
  });
});

describe('planImport', () => {
  const list = [alias(), alias({ anonymous_id: 'id2', hme: 'off@icloud.com', is_active: false })];

  it('updates changed active aliases, matching the address case-insensitively', () => {
    const p = planImport('address,label,note\nA.B@icloud.com,New,hello\n', list);
    expect(p.updates.map((u) => [u.alias.anonymous_id, u.label, u.note])).toEqual([['id1', 'New', 'hello']]);
  });

  it('counts unknown, inactive and unchanged rows', () => {
    const p = planImport('address,label\nnope@x.com,Z\noff@icloud.com,Z\na.b@icloud.com,Shop\n', list);
    expect(p).toMatchObject({ updates: [], unknown: 1, inactive: 1, unchanged: 1 });
  });

  it('keeps the existing label when the cell is blank', () => {
    const p = planImport('address,label,note\na.b@icloud.com,,only a note\n', list);
    expect(p.updates[0]).toMatchObject({ label: 'Shop', note: 'only a note' });
  });

  it('rejects a file without the needed columns', () => {
    expect(() => planImport('foo,bar\n1,2\n', list)).toThrow(/address/);
  });
});

describe('statsOf', () => {
  it('counts and ranks sites', () => {
    const now = Date.UTC(2026, 10, 21);
    const s = statsOf(
      [
        alias({ domain: 'x.com', created_ms: now - 1000 }),
        alias({ anonymous_id: '2', domain: 'x.com', is_active: false, created_ms: now - 90 * 86_400_000 }),
        alias({ anonymous_id: '3', domain: 'y.com' })
      ],
      now
    );
    expect(s).toMatchObject({ total: 3, active: 2, inactive: 1, lastMonth: 1, domains: [['x.com', 2], ['y.com', 1]] });
  });
});
