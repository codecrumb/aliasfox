import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { tick } from 'svelte';
import type { Writable } from 'svelte/store';

vi.mock('../src/lib/session', async () => {
  const { writable } = await import('svelte/store');
  const store = writable({
    state: 'loading',
    authEnabled: true,
    mode: null,
    cacheKey: null,
    reason: null,
    errorText: null
  });
  return {
    __store: store,
    sessionStore: {
      subscribe: store.subscribe,
      boot: vi.fn(async () => {}),
      login: vi.fn(),
      logout: vi.fn(async () => {}),
      onApiError: vi.fn(),
      markReady: vi.fn(async () => {
        store.update((v) => ({ ...v, state: 'ready', reason: null }));
      })
    }
  };
});
vi.mock('../src/lib/aliases', async () => ({ aliasStore: (await import('./fakes')).createAliasStore() }));
vi.mock('../src/lib/clipboard', () => ({ copyText: vi.fn(async () => 'copied') }));
vi.mock('../src/lib/api', async (orig) => {
  const real = await orig<typeof import('../src/lib/api')>();
  return { ...real, api: { disconnect: vi.fn(), connectLogin: vi.fn(), connectVerify: vi.fn(), connectCookie: vi.fn() } };
});

import * as sessionMod from '../src/lib/session';
import { aliasStore } from '../src/lib/aliases';
import App from '../src/App.svelte';
import { many, type createAliasStore } from './fakes';

const aliases = aliasStore as unknown as ReturnType<typeof createAliasStore>;

const store = (sessionMod as unknown as { __store: Writable<Record<string, unknown>> }).__store;
const s = sessionMod.sessionStore;

function setState(state: string, extra: Record<string, unknown> = {}) {
  store.update((v) => ({ ...v, state, ...extra }));
}

beforeEach(() => {
  vi.mocked(s.boot).mockClear();
  vi.mocked(s.logout).mockClear();
  setState('loading', { errorText: null });
  aliases.__set([]);
  aliases.init.mockClear();
});
afterEach(() => cleanup());

describe('App shell', () => {
  it('calls boot once on mount', () => {
    render(App);
    expect(s.boot).toHaveBeenCalledTimes(1);
  });

  it('loading shows the splash', () => {
    render(App);
    expect(screen.getByText('AliasFox')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/loading/i);
  });

  it('login shows the Login screen', async () => {
    render(App);
    setState('login');
    expect(await screen.findByLabelText('Password')).toBeInTheDocument();
  });

  it.each(['connect', 'reconnect'])('%s renders ConnectApple with Log out and focuses its container', async (st) => {
    render(App);
    setState(st, { reason: st === 'reconnect' ? 'session_expired' : null });
    expect(await screen.findByLabelText('Apple ID')).toBeInTheDocument();
    expect(document.querySelector('[data-screen="connect"]')).not.toBeNull();
    await waitFor(() =>
      expect(document.activeElement?.closest('[data-screen="connect"]')).not.toBeNull()
    );
    await fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
    expect(s.logout).toHaveBeenCalledTimes(1);
    setState('connect', { reason: null });
  });

  it('ready shows the palette and initialises the alias store', async () => {
    render(App);
    setState('ready');
    expect(await screen.findByRole('combobox', { name: 'Search aliases' })).toBeInTheDocument();
    expect(screen.queryByText(/coming soon/)).toBeNull();
    expect(aliases.init).toHaveBeenCalledTimes(1);
  });

  it('reconnect with aliases keeps the same palette mounted, shows the banner, and does not steal focus', async () => {
    aliases.__set(many(3));
    render(App);
    setState('ready');
    const search = await screen.findByRole('combobox', { name: 'Search aliases' });
    const root = screen.getByTestId('palette-root');
    await waitFor(() => expect(search).toHaveFocus());
    setState('reconnect', { reason: 'session_expired' });
    await tick();
    expect(screen.getByTestId('palette-root')).toBe(root);
    expect(screen.getByText('Your Apple session expired. Reconnect to keep editing.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Apple ID')).toBeNull();
    expect(search).toHaveFocus();
    await fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));
    const overlay = await screen.findByRole('dialog', { name: 'Reconnect your Apple account' });
    expect(overlay).toContainElement(screen.getByLabelText('Apple ID'));
    await s.markReady();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByTestId('palette-root')).toBe(root);
  });

  it('reconnect with an empty list shows the full-screen connect flow', async () => {
    render(App);
    setState('reconnect', { reason: 'session_expired' });
    expect(await screen.findByLabelText('Apple ID')).toBeInTheDocument();
    expect(screen.queryByTestId('palette-root')).toBeNull();
  });

  it('error shows errorText as text and Retry calls boot', async () => {
    render(App);
    setState('error', { errorText: '<i>internal_error</i>' });
    expect(await screen.findByText('<i>internal_error</i>')).toBeInTheDocument();
    expect(document.querySelector('i')).toBeNull();
    await fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(s.boot).toHaveBeenCalledTimes(2);
  });

  it('always mounts the toast container', () => {
    const { container } = render(App);
    expect(container.querySelector('.toasts[aria-live="polite"]')).not.toBeNull();
  });

  it('moves focus to the new screen container when the old one unmounts', async () => {
    render(App);
    setState('login');
    const pw = await screen.findByLabelText('Password');
    expect(pw).toHaveFocus(); // Login focuses its own input: already inside the screen
    setState('error', { errorText: 'x' });
    await screen.findByRole('button', { name: 'Retry' });
    await waitFor(() => {
      const el = document.activeElement as HTMLElement;
      expect(el.getAttribute('data-screen')).toBe('error');
    });
  });

  it('focuses the error screen container', async () => {
    render(App);
    setState('error', { errorText: 'x' });
    await screen.findByRole('button', { name: 'Retry' });
    await waitFor(() =>
      expect((document.activeElement as HTMLElement).getAttribute('data-screen')).toBe('error')
    );
  });

  it('does not steal focus when an input inside the new screen has it', async () => {
    render(App);
    setState('login');
    const pw = await screen.findByLabelText('Password');
    await tick();
    expect(pw).toHaveFocus();
    expect(document.activeElement?.getAttribute('data-screen')).toBeNull();
  });

  it('does not steal focus from elsewhere (e.g. an open dialog)', async () => {
    render(App);
    const dlg = document.createElement('div');
    dlg.setAttribute('role', 'dialog');
    const btn = document.createElement('button');
    dlg.appendChild(btn);
    document.body.appendChild(dlg);
    btn.focus();
    setState('error', { errorText: 'x' });
    await screen.findByRole('button', { name: 'Retry' });
    await tick();
    expect(btn).toHaveFocus();
    dlg.remove();
  });
});
