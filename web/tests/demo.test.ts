import { describe, expect, it } from 'vitest';
import { api } from '../src/lib/api';
import { installDemo } from '../src/lib/demo';

describe('demo mode api', () => {
  it('serves sample data and applies changes in memory only', async () => {
    installDemo();
    const st = await api.status();
    expect(st.connected).toBe(true);
    const list = await api.listAliases();
    expect(list.length).toBeGreaterThan(5);
    expect(list.every((a) => a.hme.endsWith('@demo.invalid'))).toBe(true);

    const { hme } = await api.generate();
    const created = await api.reserve({ hme, label: 'Mine', note: '' });
    expect((await api.listAliases())[0]?.anonymous_id).toBe(created.anonymous_id);

    await api.setActive(created.anonymous_id, false);
    expect((await api.listAliases())[0]?.is_active).toBe(false);
    await api.remove(created.anonymous_id);
    expect((await api.listAliases()).length).toBe(list.length);
  });
});
