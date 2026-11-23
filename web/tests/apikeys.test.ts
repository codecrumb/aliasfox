import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';

const h = vi.hoisted(() => ({ listKeys: vi.fn(), createKey: vi.fn(), revokeKey: vi.fn() }));
vi.mock('../src/lib/api', async (orig) => ({
  ...(await orig<typeof import('../src/lib/api')>()),
  api: { listKeys: h.listKeys, createKey: h.createKey, revokeKey: h.revokeKey }
}));
vi.mock('../src/lib/aliases', async () => ({ aliasStore: (await import('./fakes')).createAliasStore() }));
vi.mock('../src/lib/session', async () => ({ sessionStore: (await import('./fakes')).createSessionStore() }));
vi.mock('../src/lib/clipboard', () => ({ copyText: vi.fn(async () => 'copied') }));
vi.mock('../src/lib/toasts', () => ({ toasts: { push: vi.fn(), dismiss: vi.fn(), subscribe: vi.fn() } }));

import { ApiError } from '../src/lib/api';
import { copyText } from '../src/lib/clipboard';
import { toasts } from '../src/lib/toasts';
import { CONFIRM_GUARD_MS } from '../src/lib/keys';
import Palette from '../src/components/Palette.svelte';
import ApiKeysDialog from '../src/components/ApiKeysDialog.svelte';

const FULL_KEY = 'afk_abcd1234_SECRETSECRETSECRET';
const row = (id: number, extra = {}) => ({
  id, name: `Key ${id}`, preset: 'bitwarden', scopes: ['create'], prefix: 'abcd1234',
  created_at: '2026-11-01T10:00:00Z', last_used_at: null, expires_at: null, ...extra
});
const mount = () => render(ApiKeysDialog, { onClose: vi.fn() });
const box = (name: string | RegExp) => screen.getByRole('checkbox', { name }) as HTMLInputElement;
const create = () => screen.getByRole('button', { name: 'Create key' }) as HTMLButtonElement;

beforeEach(() => {
  h.listKeys.mockReset().mockResolvedValue([]);
  h.createKey.mockReset();
  h.revokeKey.mockReset();
  vi.mocked(copyText).mockClear();
  vi.mocked(toasts.push).mockClear();
});
afterEach(cleanup);

describe('ApiKeysDialog', () => {
  it('preset Bitwarden checks only create', async () => {
    mount();
    await fireEvent.click(await screen.findByRole('radio', { name: 'Bitwarden' }));
    expect(['read', 'create', 'edit', 'deactivate', 'delete'].filter((s) => box(s).checked)).toEqual(['create']);
  });

  it('Full access requires the delete confirm before Create enables', async () => {
    mount();
    await fireEvent.input(await screen.findByLabelText('Name'), { target: { value: 'all' } });
    await fireEvent.click(screen.getByRole('radio', { name: 'Full access' }));
    expect(screen.getByText(/can permanently delete aliases/i)).toBeTruthy();
    expect(create().disabled).toBe(true);
    await fireEvent.click(screen.getByRole('checkbox', { name: /I understand/i }));
    expect(create().disabled).toBe(false);
  });

  it('created key is shown once with Copy, absent after close and on reopen (prefix only)', async () => {
    h.createKey.mockResolvedValue({ ...row(7), key: FULL_KEY });
    const { unmount } = mount();
    await fireEvent.input(await screen.findByLabelText('Name'), { target: { value: 'bw' } });
    await fireEvent.click(screen.getByRole('radio', { name: 'Bitwarden' }));
    await fireEvent.click(create());
    expect(h.createKey).toHaveBeenCalledWith({ name: 'bw', preset: 'bitwarden' });
    const field = (await screen.findByLabelText('Your new API key')) as HTMLInputElement;
    expect(field.value).toBe(FULL_KEY);
    expect(field.readOnly).toBe(true);
    expect(screen.getByText(/won't see this again/i)).toBeTruthy();
    expect(document.body.textContent).toContain('Forwarded email alias');
    await fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    expect(copyText).toHaveBeenCalledWith(FULL_KEY);
    await fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(screen.queryByLabelText('Your new API key')).toBeNull();
    expect(document.body.textContent).not.toContain('SECRETSECRET');
    unmount();
    h.listKeys.mockResolvedValue([row(7, { name: 'bw' })]);
    mount();
    await screen.findByText('bw');
    expect(document.body.textContent).toContain('afk_abcd1234…');
    expect(document.body.textContent).not.toContain('SECRETSECRET');
  });

  it('shows expiry on key rows: Expires <date> or Expired', async () => {
    const soon = new Date(Date.now() + 5 * 864e5).toISOString();
    h.listKeys.mockResolvedValue([
      row(1, { expires_at: soon }), row(2, { expires_at: '2020-01-01T00:00:00Z' }), row(3)
    ]);
    mount();
    await screen.findByText('Key 1');
    const t = document.body.textContent ?? '';
    expect(t).toContain(`Expires ${new Date(soon).toLocaleDateString()}`);
    expect(t.match(/Expired/g)).toHaveLength(1);
    expect(t.match(/Expires \d/g)).toHaveLength(1);
  });

  it('Escape does nothing while a fresh key is shown, and closes the list view', async () => {
    h.createKey.mockResolvedValue({ ...row(7), key: FULL_KEY });
    const onClose = vi.fn();
    render(ApiKeysDialog, { onClose });
    await fireEvent.input(await screen.findByLabelText('Name'), { target: { value: 'bw' } });
    await fireEvent.click(screen.getByRole('radio', { name: 'Bitwarden' }));
    await fireEvent.click(create());
    const field = await screen.findByLabelText('Your new API key');
    await fireEvent.keyDown(field, { key: 'Escape', code: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('Your new API key')).toBeTruthy();
    await fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows the MCP command with the key for the assistant preset', async () => {
    h.createKey.mockResolvedValue({ ...row(8, { preset: 'assistant' }), key: FULL_KEY });
    mount();
    await fireEvent.input(await screen.findByLabelText('Name'), { target: { value: 'ai' } });
    await fireEvent.click(screen.getByRole('radio', { name: 'Assistant (MCP)' }));
    await fireEvent.click(create());
    await screen.findByLabelText('Your new API key');
    expect(document.body.textContent).toContain(
      `claude mcp add --transport http aliasfox ${window.location.origin}/mcp --header "Authorization: Bearer ${FULL_KEY}"`
    );
  });

  it('revoke asks for confirmation then calls api.revokeKey', async () => {
    h.listKeys.mockResolvedValue([row(3), row(4)]);
    h.revokeKey.mockResolvedValue({ ok: true });
    mount();
    await screen.findByText('Key 3');
    await fireEvent.click(screen.getAllByRole('button', { name: /^Revoke/ })[0]!);
    expect(h.revokeKey).not.toHaveBeenCalled();
    const dlg = await screen.findByRole('dialog', { name: /revoke/i });
    await new Promise((r) => setTimeout(r, CONFIRM_GUARD_MS + 20));
    await fireEvent.click(within(dlg).getByRole('button', { name: 'Revoke' }));
    await waitFor(() => expect(h.revokeKey).toHaveBeenCalledWith(3));
    await waitFor(() => expect(screen.queryByText('Key 3')).toBeNull());
    expect(screen.getByText('Key 4')).toBeTruthy();
    expect(toasts.push).toHaveBeenCalled();
  });

  it('409 too_many_keys shows a clear message', async () => {
    h.createKey.mockRejectedValue(new ApiError(409, 'too_many_keys'));
    mount();
    await fireEvent.input(await screen.findByLabelText('Name'), { target: { value: 'x' } });
    await fireEvent.click(screen.getByRole('radio', { name: 'Read only' }));
    await fireEvent.click(create());
    expect(await screen.findByText('You can have at most 20 API keys')).toBeTruthy();
  });

  it('never writes the key to localStorage or IndexedDB', async () => {
    const set = vi.spyOn(Storage.prototype, 'setItem');
    const idb = typeof indexedDB === 'undefined' ? null : vi.spyOn(indexedDB, 'open');
    h.createKey.mockResolvedValue({ ...row(9), key: FULL_KEY });
    mount();
    await fireEvent.input(await screen.findByLabelText('Name'), { target: { value: 'n' } });
    await fireEvent.click(screen.getByRole('radio', { name: 'Bitwarden' }));
    await fireEvent.click(create());
    await screen.findByLabelText('Your new API key');
    expect(set.mock.calls.flat().join('|')).not.toContain('SECRETSECRET');
    expect(idb?.mock.calls.length ?? 0).toBe(0);
    set.mockRestore();
    idb?.mockRestore();
  });
});

describe('palette entry', () => {
  it('finds the action by "bitwarden" and opens the dialog', async () => {
    render(Palette);
    const search = screen.getByRole('combobox', { name: 'Search aliases' });
    await fireEvent.keyDown(search, { key: 'k', code: 'KeyK', ctrlKey: true });
    await fireEvent.input(await screen.findByRole('combobox', { name: 'Filter actions' }), { target: { value: 'bitwarden' } });
    await fireEvent.click(await screen.findByRole('option', { name: /API keys/ }));
    expect(await screen.findByRole('dialog', { name: 'API keys' })).toBeTruthy();
  });
});
