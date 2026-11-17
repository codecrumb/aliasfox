import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { tick } from 'svelte';
import { ApiError } from '../src/lib/api';
import { alias, many, type createAliasStore, type createSessionStore } from './fakes';

vi.mock('../src/lib/aliases', async () => ({ aliasStore: (await import('./fakes')).createAliasStore() }));
vi.mock('../src/lib/session', async () => ({ sessionStore: (await import('./fakes')).createSessionStore() }));
vi.mock('../src/lib/clipboard', () => ({ copyText: vi.fn(async () => 'copied') }));
vi.mock('../src/lib/toasts', () => ({ toasts: { push: vi.fn(), dismiss: vi.fn(), subscribe: vi.fn() } }));
vi.mock('../src/lib/api', async (orig) => {
  const real = await orig<typeof import('../src/lib/api')>();
  return { ...real, api: { disconnect: vi.fn(), connectLogin: vi.fn(), connectVerify: vi.fn(), connectCookie: vi.fn() } };
});

import { aliasStore as realAliasStore } from '../src/lib/aliases';
import { sessionStore as realSession } from '../src/lib/session';
import { copyText } from '../src/lib/clipboard';
import { toasts } from '../src/lib/toasts';
import Palette from '../src/components/Palette.svelte';

const store = realAliasStore as unknown as ReturnType<typeof createAliasStore>;
const session = realSession as unknown as ReturnType<typeof createSessionStore>;
const copy = vi.mocked(copyText);
const push = vi.mocked(toasts.push);
const search = () => screen.getByRole('combobox', { name: 'Search aliases' }) as HTMLInputElement;
const altN = () => fireEvent.keyDown(search(), { key: 'n', code: 'KeyN', altKey: true });
const type = (el: HTMLInputElement | HTMLTextAreaElement, value: string) => fireEvent.input(el, { target: { value } });
const enter = (el: Element) => fireEvent.keyDown(el, { key: 'Enter', code: 'Enter' });

let generateImpl: unknown;
let reserveImpl: unknown;
beforeEach(() => {
  generateImpl ??= store.generate.getMockImplementation();
  reserveImpl ??= store.reserve.getMockImplementation();
  store.generate.mockReset().mockImplementation(generateImpl as () => Promise<string>);
  store.reserve.mockReset().mockImplementation(reserveImpl as (b: { hme: string; label: string; note: string }) => Promise<never>);
  copy.mockReset().mockResolvedValue('copied');
  push.mockReset();
  session.__set({ state: 'ready', reason: null });
  store.__set(many(3), { loading: false, error: null });
});
afterEach(() => cleanup());

async function openReady() {
  render(Palette);
  await tick();
  await altN();
  const dlg = screen.getByRole('dialog', { name: 'New alias' });
  await within(dlg).findByText('fresh@icloud.com');
  const label = within(dlg).getByLabelText('Label') as HTMLInputElement;
  return { dlg, label };
}

describe('new alias flow', () => {
  it('test_alt_n_generates_then_enter_reserves_with_label_once', async () => {
    const { dlg, label } = await openReady();
    expect(store.generate).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(label).toHaveFocus());
    expect(dlg).toHaveTextContent('Where will you use this? e.g. a site name');
    await type(label, 'Shop');
    await type(within(dlg).getByLabelText(/Note/) as HTMLTextAreaElement, 'n1');
    await enter(label);
    await enter(label);
    expect(store.reserve).toHaveBeenCalledTimes(1);
    expect(store.reserve).toHaveBeenCalledWith({ hme: 'fresh@icloud.com', label: 'Shop', note: 'n1' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(copy).toHaveBeenCalledWith('fresh@icloud.com'));
    await waitFor(() => expect(push).toHaveBeenCalledWith({ kind: 'ok', text: 'Created and copied' }));
    const sel = screen.getAllByRole('option').find((o) => o.getAttribute('aria-selected') === 'true');
    expect(sel).toHaveAttribute('data-id', 'new-id');
    await waitFor(() => expect(search()).toHaveFocus());
  });

  it('double Enter while the reserve is pending sends one request and disables the button', async () => {
    let done: (v: unknown) => void = () => {};
    store.reserve.mockImplementation(() => new Promise((r) => (done = r)) as never);
    const { dlg, label } = await openReady();
    await type(label, 'Shop');
    await enter(label);
    await enter(label);
    await fireEvent.click(within(dlg).getByRole('button', { name: /Creat/ }));
    expect(store.reserve).toHaveBeenCalledTimes(1);
    expect(within(dlg).getByRole('button', { name: /Creating/ })).toBeDisabled();
    done(alias(777, { anonymous_id: 'made', hme: 'fresh@icloud.com', label: 'Shop' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('Esc while the reserve is pending does nothing, so the result is always reported', async () => {
    let done: (v: unknown) => void = () => {};
    store.reserve.mockImplementation(() => new Promise((r) => (done = r)) as never);
    const { dlg, label } = await openReady();
    await type(label, 'Shop');
    await enter(label);
    await fireEvent.keyDown(label, { key: 'Escape', code: 'Escape' });
    expect(screen.getByRole('dialog', { name: 'New alias' })).toBe(dlg);
    expect(within(dlg).getByRole('button', { name: 'Cancel' })).toBeDisabled();
    done(alias(778, { anonymous_id: 'made2', hme: 'fresh@icloud.com', label: 'Shop' }));
    await waitFor(() => expect(push).toHaveBeenCalledWith({ kind: 'ok', text: 'Created and copied' }));
  });

  it('a reserve that resolves undefined closes, refreshes the list and nothing throws', async () => {
    const errors: unknown[] = [];
    const onRej = (e: PromiseRejectionEvent | unknown) => errors.push(e);
    process.on('unhandledRejection', onRej);
    try {
      store.reserve.mockResolvedValue(undefined as never);
      store.refresh.mockClear();
      const { label } = await openReady();
      await type(label, 'Shop');
      await enter(label);
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      await waitFor(() => expect(store.refresh).toHaveBeenCalledTimes(1));
      await new Promise((r) => setTimeout(r, 20));
      expect(errors).toEqual([]);
      expect(push).toHaveBeenCalledWith({ kind: 'ok', text: 'Created' });
    } finally {
      process.off('unhandledRejection', onRej);
    }
  });

  it('held Enter in the label sends one reserve', async () => {
    store.reserve.mockRejectedValueOnce(new ApiError(502, 'apple_unreachable'));
    const { dlg, label } = await openReady();
    await type(label, 'Shop');
    await enter(label);
    await waitFor(() => expect(within(dlg).getByRole('alert')).toHaveTextContent("Can't reach Apple"));
    for (let i = 0; i < 5; i++) await fireEvent.keyDown(label, { key: 'Enter', code: 'Enter', repeat: true });
    await tick();
    expect(store.reserve).toHaveBeenCalledTimes(1);
    await enter(label); // a fresh press retries
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    for (let i = 0; i < 3; i++) await fireEvent.keyDown(search(), { key: 'Enter', code: 'Enter', repeat: true });
    expect(store.reserve).toHaveBeenCalledTimes(2);
    expect(copy).toHaveBeenCalledTimes(1); // the created address only; held Enter does not copy again
  });

  it('test_alt_n_escape_does_not_reserve', async () => {
    const { label } = await openReady();
    await type(label, 'Shop');
    await fireEvent.keyDown(label, { key: 'Escape', code: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(store.reserve).not.toHaveBeenCalled();
    await waitFor(() => expect(search()).toHaveFocus());
  });

  it('shows a loading state while generating; Esc then cancels without reserving', async () => {
    let resolve: (v: string) => void = () => {};
    store.generate.mockImplementation(() => new Promise<string>((r) => (resolve = r)));
    render(Palette);
    await tick();
    await altN();
    const dlg = screen.getByRole('dialog', { name: 'New alias' });
    expect(dlg).toHaveTextContent('Getting a new address…');
    await fireEvent.keyDown(dlg, { key: 'Escape', code: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    resolve('late@icloud.com');
    await tick();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(store.reserve).not.toHaveBeenCalled();
  });

  it('empty label shows an inline message and sends no request', async () => {
    const { dlg, label } = await openReady();
    await enter(label);
    expect(store.reserve).not.toHaveBeenCalled();
    expect(within(dlg).getByRole('alert')).toHaveTextContent('Add a label');
    expect(label).toHaveAttribute('aria-invalid', 'true');
  });

  it.each([
    [new ApiError(429, 'rate_limited', { retryAfter: 12 }), 'Too many new addresses. Try again in 12 seconds.'],
    [new ApiError(429, 'busy', { retryAfter: 1 }), 'Too many new addresses. Try again in 1 second.'],
    [new ApiError(502, 'apple_unreachable'), "Can't reach Apple right now."],
    [new ApiError(0, 'network_error'), "Can't reach Apple right now."],
    [new ApiError(502, 'apple_error', { serverMessage: 'Quota <b>reached</b>' }), 'Apple returned an error. Quota <b>reached</b>'],
    [new ApiError(500, 'internal_error'), "Couldn't get a new address. Try again."]
  ])('generate failure closes with a mapped toast (%s)', async (err, text) => {
    store.generate.mockRejectedValue(err);
    render(Palette);
    await tick();
    await altN();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(push).toHaveBeenCalledWith({ kind: 'error', text });
  });

  it('failed reserve keeps the dialog open with the input and an inline error', async () => {
    store.reserve.mockRejectedValue(new ApiError(502, 'apple_unreachable'));
    const { dlg, label } = await openReady();
    await type(label, 'Keep me');
    await enter(label);
    await waitFor(() => expect(within(dlg).getByRole('alert')).toHaveTextContent("Can't reach Apple right now."));
    expect(screen.getByRole('dialog', { name: 'New alias' })).toBe(dlg);
    expect(label.value).toBe('Keep me');
    expect(within(dlg).getByRole('button', { name: 'Create' })).not.toBeDisabled();
  });

  it('session expiry during reserve keeps the dialog and shows the reconnect banner', async () => {
    store.reserve.mockImplementation(async () => {
      session.__set({ state: 'reconnect', reason: 'session_expired' });
      throw new ApiError(401, 'session_expired');
    });
    const { dlg, label } = await openReady();
    await type(label, 'Keep me');
    await enter(label);
    await waitFor(() => expect(screen.getByText('Your Apple session expired. Reconnect to keep editing.')).toBeInTheDocument());
    expect(label.value).toBe('Keep me');
    expect(within(dlg).getByRole('button', { name: 'Reconnect' })).toBeInTheDocument();
  });

  it('the New alias button opens the same flow for mouse and touch users', async () => {
    render(Palette);
    await tick();
    await fireEvent.click(screen.getByRole('button', { name: 'New alias' }));
    expect(await screen.findByRole('dialog', { name: 'New alias' })).toBeInTheDocument();
    expect(store.generate).toHaveBeenCalledTimes(1);
  });

  it('created alias outside the current search clears the search so it can be selected', async () => {
    const { label } = await openReady();
    // search is inert behind the modal; simulate a pre-existing query by closing and reopening
    await fireEvent.keyDown(label, { key: 'Escape', code: 'Escape' });
    await type(search(), 'Label 1');
    await altN();
    const dlg = screen.getByRole('dialog', { name: 'New alias' });
    const l2 = (await within(dlg).findByLabelText('Label')) as HTMLInputElement;
    await type(l2, 'Brand new');
    await enter(l2);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(search().value).toBe('');
    const sel = screen.getAllByRole('option').find((o) => o.getAttribute('aria-selected') === 'true');
    expect(sel).toHaveAttribute('data-id', 'new-id');
  });
});
