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
      logout: vi.fn(async () => {})
    }
  };
});

import * as sessionMod from '../src/lib/session';
import App from '../src/App.svelte';

const store = (sessionMod as unknown as { __store: Writable<Record<string, unknown>> }).__store;
const s = sessionMod.sessionStore;

function setState(state: string, extra: Record<string, unknown> = {}) {
  store.update((v) => ({ ...v, state, ...extra }));
}

beforeEach(() => {
  vi.mocked(s.boot).mockClear();
  vi.mocked(s.logout).mockClear();
  setState('loading', { errorText: null });
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

  it('ready shows the palette placeholder with Log out', async () => {
    render(App);
    setState('ready');
    expect(await screen.findByText('Palette — coming soon')).toBeInTheDocument();
    await fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
    expect(s.logout).toHaveBeenCalledTimes(1);
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
    setState('ready');
    await screen.findByText('Palette — coming soon');
    await waitFor(() => {
      const el = document.activeElement as HTMLElement;
      expect(el.getAttribute('data-screen')).toBe('ready');
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
    setState('ready');
    await screen.findByText('Palette — coming soon');
    await tick();
    expect(btn).toHaveFocus();
    dlg.remove();
  });
});
