import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';

const h = vi.hoisted(() => ({ forwardOptions: vi.fn(), setForwardTo: vi.fn() }));
vi.mock('../src/lib/api', async (orig) => ({
  ...(await orig<typeof import('../src/lib/api')>()),
  api: { forwardOptions: h.forwardOptions }
}));
vi.mock('../src/lib/aliases', () => ({ aliasStore: { setForwardTo: h.setForwardTo } }));
vi.mock('../src/lib/session', async () => ({ sessionStore: (await import('./fakes')).createSessionStore() }));

import { ApiError } from '../src/lib/api';
import ForwardToDialog from '../src/components/ForwardToDialog.svelte';

const mount = (current = 'a@x.com') => render(ForwardToDialog, { current, onClose: vi.fn(), onReconnect: vi.fn() });

describe('ForwardToDialog options', () => {
  it('offers Apple addresses and saves the picked one', async () => {
    h.forwardOptions.mockResolvedValue({ options: ['a@x.com', 'b@x.com'], selected: 'a@x.com' });
    h.setForwardTo.mockResolvedValue(undefined);
    mount();
    const pick = await screen.findByRole('combobox', { name: /forward to/i });
    await fireEvent.change(pick, { target: { value: 'b@x.com' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(h.setForwardTo).toHaveBeenCalledWith('b@x.com'));
  });

  it('falls back to the text box when the list fails', async () => {
    h.forwardOptions.mockRejectedValue(new Error('x'));
    mount();
    expect(await screen.findByRole('textbox')).toBeTruthy();
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByRole('combobox')).toBeNull();
    const box = screen.getByRole('textbox');
    await fireEvent.input(box, { target: { value: 'me@y.com' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(h.setForwardTo).toHaveBeenCalledWith('me@y.com'));
  });

  it("explains Apple's rejection in plain words", async () => {
    h.forwardOptions.mockResolvedValue({ options: ['a@x.com'], selected: 'a@x.com' });
    h.setForwardTo.mockRejectedValue(new ApiError(502, 'apple_error', { serverMessage: 'Invalid email to share with developers.' }));
    mount();
    await screen.findByRole('combobox');
    await fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect((await screen.findByRole('alert')).textContent).toBe('Apple only accepts the addresses on your account.');
  });

  it('shows no text box while the list loads', async () => {
    h.forwardOptions.mockReturnValue(new Promise(() => {}));
    mount();
    expect(screen.queryByRole('textbox')).toBeNull();
  });
});
