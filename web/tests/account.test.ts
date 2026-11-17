// Multi mode Account dialog, standalone and as reached from the palette's actions menu.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { tick } from 'svelte';
import { many, type createAliasStore, type createSessionStore } from './fakes';

vi.mock('../src/lib/aliases', async () => ({ aliasStore: (await import('./fakes')).createAliasStore() }));
vi.mock('../src/lib/session', async () => {
  const s = (await import('./fakes')).createSessionStore();
  return { sessionStore: { ...s, deleteAccount: vi.fn(async () => {}) } };
});
vi.mock('../src/lib/clipboard', () => ({ copyText: vi.fn(async () => 'copied') }));
vi.mock('../src/lib/toasts', () => ({ toasts: { push: vi.fn(), dismiss: vi.fn(), subscribe: vi.fn() } }));
vi.mock('../src/lib/neonAuth', () => ({
  neonAuth: Object.fromEntries(
    ['signUp', 'signIn', 'getToken', 'verifyEmailCode', 'sendVerificationCode', 'signOut', 'deleteNeonUser'].map((k) => [k, vi.fn()])
  )
}));
vi.mock('../src/lib/api', async (orig) => {
  const real = await orig<typeof import('../src/lib/api')>();
  return { ...real, api: { disconnect: vi.fn(async () => ({})), deleteAccount: vi.fn() } };
});

import { aliasStore } from '../src/lib/aliases';
import { sessionStore } from '../src/lib/session';
import { api, ApiError } from '../src/lib/api';
import { CONFIRM_GUARD_MS } from '../src/lib/keys';
import AccountDialog from '../src/components/AccountDialog.svelte';
import Palette from '../src/components/Palette.svelte';

const store = aliasStore as unknown as ReturnType<typeof createAliasStore>;
const session = sessionStore as unknown as ReturnType<typeof createSessionStore> & {
  deleteAccount: ReturnType<typeof vi.fn>;
};
const pastGuard = () => new Promise((r) => setTimeout(r, CONFIRM_GUARD_MS + 20));

beforeEach(() => {
  session.deleteAccount.mockReset().mockResolvedValue(undefined);
  session.boot.mockReset().mockResolvedValue(undefined);
  vi.mocked(api.disconnect).mockReset().mockResolvedValue({} as never);
  store.clear.mockReset().mockResolvedValue(undefined);
  session.__set({ state: 'ready', reason: null, mode: 'multi' });
  store.__set(many(2), { loading: false, refreshing: false, error: null, loadError: null, fromCache: false });
});
afterEach(() => cleanup());

describe('AccountDialog', () => {
  it('shows exactly "Disconnect Apple" and "Delete account"; disconnect hands over to the confirm', async () => {
    const onDisconnect = vi.fn();
    render(AccountDialog, { onClose: vi.fn(), onDisconnect });
    const dlg = screen.getByRole('dialog', { name: 'Account' });
    expect(within(dlg).getByRole('button', { name: 'Disconnect Apple' })).toBeInTheDocument();
    expect(within(dlg).getByRole('button', { name: 'Delete account' })).toBeInTheDocument();
    await fireEvent.click(within(dlg).getByRole('button', { name: 'Disconnect Apple' }));
    expect(onDisconnect).toHaveBeenCalledTimes(1);
    expect(session.deleteAccount).not.toHaveBeenCalled();
  });

  it('delete requires typing the word "delete"', async () => {
    render(AccountDialog, { onClose: vi.fn(), onDisconnect: vi.fn() });
    await fireEvent.click(screen.getByRole('button', { name: 'Delete account' }));
    const dlg = await screen.findByRole('dialog', { name: 'Delete your account?' });
    const word = within(dlg).getByLabelText('Type delete to confirm') as HTMLInputElement;
    expect(word).toHaveFocus();
    const confirm = within(dlg).getByRole('button', { name: 'Delete account' });
    expect(confirm).toBeDisabled();
    await fireEvent.input(word, { target: { value: 'delet' } });
    expect(confirm).toBeDisabled();
    await fireEvent.submit(word.closest('form') as HTMLFormElement);
    expect(session.deleteAccount).not.toHaveBeenCalled();
    await fireEvent.input(word, { target: { value: 'delete' } });
    expect(confirm).toBeEnabled();
    await fireEvent.submit(word.closest('form') as HTMLFormElement);
    await waitFor(() => expect(session.deleteAccount).toHaveBeenCalledTimes(1));
  });

  it('Cancel on the delete step goes back without deleting', async () => {
    render(AccountDialog, { onClose: vi.fn(), onDisconnect: vi.fn() });
    await fireEvent.click(screen.getByRole('button', { name: 'Delete account' }));
    await fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
    expect(await screen.findByRole('dialog', { name: 'Account' })).toBeInTheDocument();
    expect(session.deleteAccount).not.toHaveBeenCalled();
  });

  it('a failed delete shows fixed text and keeps the dialog', async () => {
    session.deleteAccount.mockRejectedValue(new ApiError(500, 'internal_error', { serverMessage: 'SECRET' }));
    render(AccountDialog, { onClose: vi.fn(), onDisconnect: vi.fn() });
    await fireEvent.click(screen.getByRole('button', { name: 'Delete account' }));
    const word = (await screen.findByLabelText('Type delete to confirm')) as HTMLInputElement;
    await fireEvent.input(word, { target: { value: 'delete' } });
    await fireEvent.submit(word.closest('form') as HTMLFormElement);
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent("Couldn't delete your account. Try again.")
    );
    expect(document.body.textContent).not.toContain('SECRET');
    expect(screen.getByRole('dialog', { name: 'Delete your account?' })).toBeInTheDocument();
  });
});

describe('Account in the palette (multi mode)', () => {
  async function openMenu() {
    render(Palette);
    await tick();
    const search = screen.getByRole('combobox', { name: 'Search aliases' });
    await fireEvent.keyDown(search, { key: 'k', code: 'KeyK', ctrlKey: true });
    return screen.getByRole('dialog', { name: 'Actions' });
  }

  it('"Account" opens the dialog; "Disconnect Apple" uses the existing confirm and api.disconnect', async () => {
    const menu = await openMenu();
    await fireEvent.click(within(menu).getByRole('option', { name: /^Account/ }));
    const dlg = await screen.findByRole('dialog', { name: 'Account' });
    await fireEvent.click(within(dlg).getByRole('button', { name: 'Disconnect Apple' }));
    const confirm = await screen.findByRole('dialog', { name: 'Disconnect Apple account?' });
    expect(api.disconnect).not.toHaveBeenCalled();
    await pastGuard();
    await fireEvent.click(within(confirm).getByRole('button', { name: 'Disconnect' }), { detail: 1 });
    await waitFor(() => expect(api.disconnect).toHaveBeenCalledTimes(1));
  });

  it('delete account from the palette calls the session store (which returns to signed-out)', async () => {
    const menu = await openMenu();
    await fireEvent.click(within(menu).getByRole('option', { name: /^Account/ }));
    const dlg = await screen.findByRole('dialog', { name: 'Account' });
    await fireEvent.click(within(dlg).getByRole('button', { name: 'Delete account' }));
    const word = (await screen.findByLabelText('Type delete to confirm')) as HTMLInputElement;
    await fireEvent.input(word, { target: { value: 'delete' } });
    await fireEvent.submit(word.closest('form') as HTMLFormElement);
    await waitFor(() => expect(session.deleteAccount).toHaveBeenCalledTimes(1));
  });

  it('the existing "Disconnect Apple account" entry still works in multi mode', async () => {
    const menu = await openMenu();
    expect(within(menu).getByRole('option', { name: /Disconnect Apple account/ })).toBeInTheDocument();
  });

  it('single mode has no Account entry', async () => {
    session.__set({ mode: 'single' });
    const menu = await openMenu();
    expect(within(menu).queryByRole('option', { name: /^Account/ })).toBeNull();
  });
});

describe('AccountDialog accessibility (axe; jsdom-only rules disabled as in tests/a11y.test.ts)', () => {
  it('both steps have no violations', async () => {
    const { axe } = await import('vitest-axe');
    const AXE = { rules: { 'color-contrast': { enabled: false }, region: { enabled: false } } };
    render(AccountDialog, { onClose: vi.fn(), onDisconnect: vi.fn() });
    expect((await axe(document.body, AXE)).violations.map((v) => v.id)).toEqual([]);
    await fireEvent.click(screen.getByRole('button', { name: 'Delete account' }));
    await screen.findByLabelText('Type delete to confirm');
    expect((await axe(document.body, AXE)).violations.map((v) => v.id)).toEqual([]);
  });
});
