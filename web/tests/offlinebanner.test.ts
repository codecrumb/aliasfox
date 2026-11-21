import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import { writable } from 'svelte/store';

const h = vi.hoisted(() => ({ alias: null as unknown, online: null as unknown, pending: null as unknown }));
vi.mock('../src/lib/aliases', async () => {
  const { writable } = await import('svelte/store');
  h.alias = writable({ list: [{}], loadError: null, refreshing: false });
  return { aliasStore: { subscribe: (h.alias as ReturnType<typeof writable>).subscribe, refresh: vi.fn() } };
});
vi.mock('../src/lib/queue', async () => {
  const { writable } = await import('svelte/store');
  h.online = writable(true);
  h.pending = writable(0);
  return { online: h.online, pending: h.pending };
});
vi.mock('../src/lib/session', async () => {
  const { writable } = await import('svelte/store');
  return { sessionStore: writable({ state: 'ready' }) };
});
import OfflineBanner from '../src/components/OfflineBanner.svelte';

const set = (s: unknown, v: unknown) => (s as ReturnType<typeof writable>).set(v);

describe('OfflineBanner', () => {
  it('is hidden when online, fresh and nothing waits', () => {
    render(OfflineBanner);
    expect(screen.queryByRole('status')).toBeNull();
  });
  it('says the list is a saved copy when a refresh failed while online', () => {
    set(h.alias, { list: [{}], loadError: 'network_error', refreshing: false });
    render(OfflineBanner);
    expect(screen.getByRole('status').textContent).toContain('showing your last saved list');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });
  it('shows offline with the waiting count and no button', () => {
    set(h.alias, { list: [{}], loadError: null, refreshing: false });
    set(h.online, false);
    set(h.pending, 2);
    render(OfflineBanner);
    expect(screen.getByRole('status').textContent).toContain('Offline · 2 changes waiting to sync');
    expect(screen.queryByRole('button')).toBeNull();
  });
  it('says syncing while a refresh replays waiting changes', () => {
    set(h.alias, { list: [{}], loadError: null, refreshing: true });
    set(h.online, true);
    set(h.pending, 1);
    render(OfflineBanner);
    expect(screen.getByRole('status').textContent).toContain('Syncing 1 change…');
    expect(screen.queryByRole('button')).toBeNull();
  });
});
