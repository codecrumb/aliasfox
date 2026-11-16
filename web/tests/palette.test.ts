import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tick } from 'svelte';
import { ApiError } from '../src/lib/api';
import { alias, many, type createAliasStore, type createSessionStore } from './fakes';

vi.mock('../src/lib/aliases', async () => ({ aliasStore: (await import('./fakes')).createAliasStore() }));
vi.mock('../src/lib/session', async () => ({ sessionStore: (await import('./fakes')).createSessionStore() }));
vi.mock('../src/lib/clipboard', () => ({ copyText: vi.fn(async () => 'copied') }));
vi.mock('../src/lib/toasts', () => ({ toasts: { push: vi.fn(), dismiss: vi.fn(), subscribe: vi.fn() } }));
vi.mock('../src/lib/api', async (orig) => {
  const real = await orig<typeof import('../src/lib/api')>();
  return {
    ...real,
    api: { disconnect: vi.fn(async () => ({})), connectLogin: vi.fn(), connectVerify: vi.fn(), connectCookie: vi.fn() }
  };
});

import { aliasStore as realAliasStore } from '../src/lib/aliases';
import { sessionStore as realSession } from '../src/lib/session';
import { copyText } from '../src/lib/clipboard';
import { toasts } from '../src/lib/toasts';
import { api } from '../src/lib/api';
import { ROW_HEIGHT, MAX_ROWS } from '../src/lib/virtual';
import Palette from '../src/components/Palette.svelte';

const store = realAliasStore as unknown as ReturnType<typeof createAliasStore>;
const session = realSession as unknown as ReturnType<typeof createSessionStore>;
const copy = vi.mocked(copyText);
const push = vi.mocked(toasts.push);

const search = () => screen.getByRole('combobox', { name: 'Search aliases' }) as HTMLInputElement;
const listbox = () => screen.getByRole('listbox', { name: 'Aliases' });
const options = () => screen.queryAllByRole('option');
const selected = () => options().find((o) => o.getAttribute('aria-selected') === 'true');
const key = (el: Element, init: KeyboardEventInit) => fireEvent.keyDown(el, init);
const type = (el: HTMLInputElement | HTMLTextAreaElement, value: string) => fireEvent.input(el, { target: { value } });
const dialog = (name: string | RegExp) => screen.getByRole('dialog', { name });

function setList(list = many(5), extra = {}) {
  store.__set(list, { loading: false, refreshing: false, error: null, fromCache: false, ...extra });
}

const mocks = () =>
  [store.init, store.refresh, store.generate, store.reserve, store.update, store.setActive, store.remove, store.setForwardTo, store.clear, session.boot, session.logout, session.markReady, session.onApiError, copy, push, vi.mocked(api.disconnect)] as Array<ReturnType<typeof vi.fn>>;

let defaults: Map<ReturnType<typeof vi.fn>, unknown>;
beforeEach(() => {
  if (!defaults) defaults = new Map(mocks().map((m) => [m, m.getMockImplementation()]));
  for (const m of mocks()) {
    m.mockReset();
    const impl = defaults.get(m);
    if (impl) m.mockImplementation(impl as (...a: unknown[]) => unknown);
  }
  copy.mockResolvedValue('copied');
  session.__set({ state: 'ready', reason: null });
  setList([]);
});
afterEach(() => {
  cleanup();
  Object.defineProperty(navigator, 'platform', { value: '', configurable: true });
  document.body.className = '';
});

describe('palette list', () => {
  it('test_list_renders_bounded_rows_for_10000', async () => {
    setList(many(10_000));
    render(Palette);
    await tick();
    const n = options().length;
    expect(n).toBeGreaterThan(5);
    expect(n).toBeLessThanOrEqual(MAX_ROWS);
    expect(MAX_ROWS).toBeLessThanOrEqual(60);
    expect(options()[0]).toHaveAttribute('aria-setsize', '10000');
    const spacers = listbox().querySelectorAll<HTMLElement>('.spacer');
    const pad = Array.from(spacers).reduce((sum, s) => sum + parseFloat(s.style.height || '0'), 0);
    expect(pad + n * ROW_HEIGHT).toBe(10_000 * ROW_HEIGHT);
    // scrolling far keeps the window bounded and moves it
    listbox().scrollTop = 5000 * ROW_HEIGHT;
    await fireEvent.scroll(listbox());
    expect(options().length).toBeLessThanOrEqual(MAX_ROWS);
    expect(options().some((o) => o.textContent?.includes('Label ') && o.getAttribute('aria-posinset') === '5001')).toBe(true);
  });

  it('test_typing_filters_and_selection_resets_to_first', async () => {
    setList([alias(1, { label: 'GitHub' }), alias(2, { label: 'Netflix' }), alias(3, { label: 'Amazon' }), alias(4, { label: 'Netflix Kids' })]);
    render(Palette);
    await tick();
    expect(search()).toHaveFocus();
    await key(search(), { key: 'ArrowDown', code: 'ArrowDown' });
    await key(search(), { key: 'ArrowDown', code: 'ArrowDown' });
    expect(selected()).toHaveAttribute('aria-posinset', '3');
    await type(search(), 'kids');
    expect(options()).toHaveLength(1);
    expect(options()[0]).toHaveTextContent('Netflix Kids');
    expect(selected()).toBe(options()[0]);
    await type(search(), 'netflix');
    expect(options().length).toBe(2);
    expect(selected()).toHaveAttribute('aria-posinset', '1');
  });

  it('test_arrows_and_ctrl_jk_move_selection_and_scroll_into_view', async () => {
    Object.defineProperty(navigator, 'platform', { value: 'MacIntel', configurable: true });
    setList(many(200));
    render(Palette);
    await tick();
    const s = search();
    expect(selected()).toHaveAttribute('aria-posinset', '1');
    await key(s, { key: 'ArrowDown', code: 'ArrowDown' });
    await key(s, { key: 'j', code: 'KeyJ', ctrlKey: true });
    expect(selected()).toHaveAttribute('aria-posinset', '3');
    await key(s, { key: 'k', code: 'KeyK', ctrlKey: true });
    await key(s, { key: 'ArrowUp', code: 'ArrowUp' });
    expect(selected()).toHaveAttribute('aria-posinset', '1');
    await key(s, { key: 'ArrowUp', code: 'ArrowUp' }); // clamps at the top
    expect(selected()).toHaveAttribute('aria-posinset', '1');
    for (let i = 0; i < 40; i++) await key(s, { key: 'ArrowDown', code: 'ArrowDown' });
    expect(selected()).toHaveAttribute('aria-posinset', '41');
    expect(listbox().scrollTop).toBeGreaterThan(0);
    expect(listbox().scrollTop).toBeLessThanOrEqual(40 * ROW_HEIGHT);
    expect(listbox().scrollTop + 1000).toBeGreaterThanOrEqual(41 * ROW_HEIGHT);
    expect(s.getAttribute('aria-activedescendant')).toBe(selected()?.id);
    await key(s, { key: 'End', code: 'End' });
    expect(selected()).toHaveAttribute('aria-posinset', '200');
    await key(s, { key: 'Home', code: 'Home' });
    expect(selected()).toHaveAttribute('aria-posinset', '1');
    expect(listbox().scrollTop).toBe(0);
    await key(s, { key: 'PageDown', code: 'PageDown' });
    expect(Number(selected()?.getAttribute('aria-posinset'))).toBeGreaterThan(2);
  });

  it('combobox ARIA wiring; activedescendant follows the selection', async () => {
    setList(many(10));
    render(Palette);
    await tick();
    const s = search();
    expect(s).toHaveAttribute('aria-expanded', 'true');
    expect(s.getAttribute('aria-controls')).toBe(listbox().id);
    expect(s).toHaveAttribute('aria-autocomplete', 'list');
    const first = s.getAttribute('aria-activedescendant');
    expect(first).toBe(selected()?.id);
    await key(s, { key: 'ArrowDown', code: 'ArrowDown' });
    expect(s.getAttribute('aria-activedescendant')).not.toBe(first);
    expect(document.getElementById(s.getAttribute('aria-activedescendant') ?? '')).toBe(selected());
  });

  it('announces result counts in a polite live region', async () => {
    setList(many(12));
    render(Palette);
    await tick();
    const live = screen.getByTestId('result-count');
    expect(live).toHaveAttribute('aria-live', 'polite');
    expect(live).toHaveTextContent('12 results');
    await type(search(), 'zzzzqqq');
    expect(live).toHaveTextContent('No results');
    expect(screen.getByText(/No aliases match/)).toBeInTheDocument();
    await type(search(), 'Label 11');
    expect(live).toHaveTextContent(/^1 result$/);
  });

  it('status filter: segmented control and actions menu', async () => {
    setList([alias(1), alias(2, { is_active: false, label: 'Dormant' }), alias(3)]);
    render(Palette);
    await tick();
    const group = screen.getByRole('group', { name: 'Status' });
    await fireEvent.click(within(group).getByRole('button', { name: 'Inactive' }));
    expect(options()).toHaveLength(1);
    expect(options()[0]).toHaveTextContent('Dormant');
    expect(within(group).getByRole('button', { name: 'Inactive' })).toHaveAttribute('aria-pressed', 'true');
    expect(search()).toHaveFocus();
    await key(search(), { key: 'k', code: 'KeyK', ctrlKey: true });
    const menu = dialog('Actions');
    await type(within(menu).getByRole('combobox', { name: 'Filter actions' }) as HTMLInputElement, 'active');
    await fireEvent.click(within(menu).getByRole('option', { name: /Filter: Active/ }));
    await tick();
    expect(options()).toHaveLength(2);
    expect(within(group).getByRole('button', { name: 'Active' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('selection is preserved across a refresh by anonymous_id', async () => {
    setList(many(10));
    render(Palette);
    await tick();
    await key(search(), { key: 'ArrowDown', code: 'ArrowDown' });
    await key(search(), { key: 'ArrowDown', code: 'ArrowDown' });
    const id = selected()?.getAttribute('data-id');
    expect(id).toBe('id-7');
    setList([alias(50), alias(51), ...many(10)]);
    await tick();
    expect(selected()?.getAttribute('data-id')).toBe('id-7');
    expect(selected()).toHaveAttribute('aria-posinset', '5');
  });

  it('clicking a row selects it without leaving the search field', async () => {
    setList(many(5));
    render(Palette);
    await tick();
    const row = options()[3] as HTMLElement;
    const md = await fireEvent.mouseDown(row);
    expect(md).toBe(false); // default prevented: focus stays in search
    await fireEvent.click(row);
    expect(row).toHaveAttribute('aria-selected', 'true');
    expect(search()).toHaveFocus();
  });
});

describe('palette keyboard actions', () => {
  it('test_enter_copies_selected_address_and_toasts', async () => {
    setList(many(5));
    render(Palette);
    await tick();
    await key(search(), { key: 'ArrowDown', code: 'ArrowDown' });
    await key(search(), { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(copy).toHaveBeenCalledWith('addr3@icloud.com'));
    await waitFor(() => expect(push).toHaveBeenCalledWith({ kind: 'ok', text: 'Copied addr3@icloud.com' }));
  });

  it('manual-copy fallback appears and is selectable when the clipboard is denied', async () => {
    copy.mockResolvedValue('fallback');
    setList(many(3));
    render(Palette);
    await tick();
    await key(search(), { key: 'Enter', code: 'Enter' });
    const dlg = await screen.findByRole('dialog', { name: 'Copy address' });
    const field = within(dlg).getByRole('textbox') as HTMLInputElement;
    expect(field.value).toBe('addr2@icloud.com');
    expect(field).toHaveAttribute('readonly');
    await waitFor(() => expect(field).toHaveFocus());
    expect(field.selectionStart).toBe(0);
    expect(field.selectionEnd).toBe(field.value.length);
    expect(dlg).toHaveTextContent('Your browser blocked copying. Press Ctrl/Cmd+C to copy.');
    expect(push).not.toHaveBeenCalledWith(expect.objectContaining({ text: expect.stringContaining('Copied') }));
    await key(field, { key: 'Escape', code: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(search()).toHaveFocus());
  });

  it('test_shift_enter_toggles_active_optimistically', async () => {
    setList(many(3));
    let finish: () => void = () => {};
    store.setActive.mockImplementation((id: string, active: boolean) => {
      store.__set(store.__get().list.map((a) => (a.anonymous_id === id ? { ...a, is_active: active } : a)));
      return new Promise<void>((r) => (finish = r));
    });
    render(Palette);
    await tick();
    await key(search(), { key: 'Enter', code: 'Enter', shiftKey: true });
    expect(store.setActive).toHaveBeenCalledWith('id-2', false);
    await tick();
    expect(within(options()[0] as HTMLElement).getByText('Off')).toBeInTheDocument();
    finish();
    await waitFor(() => expect(push).toHaveBeenCalledWith({ kind: 'ok', text: 'Turned off addr2@icloud.com' }));
  });

  it('test_alt_backspace_confirms_before_delete_enter_confirms_esc_cancels', async () => {
    setList(many(3));
    render(Palette);
    await tick();
    await key(search(), { key: 'Backspace', code: 'Backspace', altKey: true });
    let dlg = dialog('Delete this address?');
    expect(dlg).toHaveTextContent('addr2@icloud.com');
    expect(within(dlg).getByRole('button', { name: 'Delete' })).toHaveFocus();
    expect(store.remove).not.toHaveBeenCalled();
    await key(within(dlg).getByRole('button', { name: 'Delete' }), { key: 'Escape', code: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(store.remove).not.toHaveBeenCalled();
    await waitFor(() => expect(search()).toHaveFocus());
    await key(search(), { key: 'Backspace', code: 'Backspace', altKey: true });
    dlg = dialog('Delete this address?');
    await key(within(dlg).getByRole('button', { name: 'Delete' }), { key: 'Enter', code: 'Enter' });
    expect(store.remove).toHaveBeenCalledTimes(1);
    expect(store.remove).toHaveBeenCalledWith('id-2');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(options()).toHaveLength(2);
  });

  it('delete removes optimistically and rolls back on error with a toast', async () => {
    setList(many(3));
    let fail: (e: unknown) => void = () => {};
    const before = store.__get().list;
    store.remove.mockImplementation((id: string) => {
      store.__set(before.filter((a) => a.anonymous_id !== id));
      return new Promise<void>((_, rej) => (fail = (e) => {
        store.__set(before);
        rej(e);
      }));
    });
    render(Palette);
    await tick();
    await key(search(), { key: 'Backspace', code: 'Backspace', altKey: true });
    await key(within(dialog('Delete this address?')).getByRole('button', { name: 'Delete' }), { key: 'Enter', code: 'Enter' });
    await tick();
    expect(options()).toHaveLength(2);
    expect(screen.queryByText('addr2@icloud.com', { selector: '.addr' })).toBeNull();
    fail(new ApiError(502, 'apple_unreachable'));
    await waitFor(() => expect(options()).toHaveLength(3));
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith({ kind: 'error', text: "Couldn't delete addr2@icloud.com. Can't reach Apple right now." })
    );
  });

  it('test_alt_e_edits_label_and_note', async () => {
    setList([alias(1, { label: 'Old', note: 'old note' })]);
    render(Palette);
    await tick();
    await key(search(), { key: 'e', code: 'KeyE', altKey: true });
    let dlg = dialog('Edit alias');
    const label = within(dlg).getByLabelText('Label') as HTMLInputElement;
    const note = within(dlg).getByLabelText('Note') as HTMLTextAreaElement;
    expect(label.value).toBe('Old');
    expect(note.value).toBe('old note');
    await waitFor(() => expect(label).toHaveFocus());
    await type(label, 'New label');
    await type(note, 'new note');
    await key(label, { key: 'Enter', code: 'Enter' });
    expect(store.update).toHaveBeenCalledWith('id-1', { label: 'New label', note: 'new note' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(options()[0]).toHaveTextContent('New label');
    // Ctrl/Cmd+Enter saves from the note; plain Enter there is a newline
    await key(search(), { key: 'e', code: 'KeyE', altKey: true });
    dlg = dialog('Edit alias');
    const note2 = within(dlg).getByLabelText('Note') as HTMLTextAreaElement;
    await type(note2, 'line');
    await key(note2, { key: 'Enter', code: 'Enter' });
    expect(store.update).toHaveBeenCalledTimes(1);
    await key(note2, { key: 'Enter', code: 'Enter', metaKey: true });
    expect(store.update).toHaveBeenCalledTimes(2);
    expect(store.update).toHaveBeenLastCalledWith('id-1', { label: 'New label', note: 'line' });
  });

  it('edit: empty label is refused, failure keeps the dialog open with an inline error, Esc cancels', async () => {
    setList([alias(1, { label: 'Old' })]);
    store.update.mockRejectedValueOnce(new ApiError(502, 'apple_error', { serverMessage: 'nope <b>' }));
    render(Palette);
    await tick();
    await key(search(), { key: 'e', code: 'KeyE', altKey: true });
    const dlg = dialog('Edit alias');
    const label = within(dlg).getByLabelText('Label') as HTMLInputElement;
    await type(label, '   ');
    await key(label, { key: 'Enter', code: 'Enter' });
    expect(store.update).not.toHaveBeenCalled();
    expect(within(dlg).getByRole('alert')).toHaveTextContent('Add a label');
    await type(label, 'Fine');
    await key(label, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(within(dlg).getByRole('alert')).toHaveTextContent('Apple returned an error. nope <b>'));
    expect(dialog('Edit alias')).toBeInTheDocument();
    expect(label.value).toBe('Fine');
    await key(label, { key: 'Escape', code: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('test_shortcut_help_on_question_mark_only_when_search_empty', async () => {
    setList(many(3));
    render(Palette);
    await tick();
    await type(search(), 'abc');
    await key(search(), { key: '?', code: 'Slash', shiftKey: true });
    expect(screen.queryByRole('dialog')).toBeNull();
    await type(search(), '');
    await key(search(), { key: '?', code: 'Slash', shiftKey: true });
    const help = dialog('Keyboard shortcuts');
    expect(help).toHaveTextContent('Copy address');
    expect(help).toHaveTextContent('New alias');
    expect(help.querySelectorAll('kbd').length).toBeGreaterThan(10);
    await key(help, { key: 'Escape', code: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('Esc clears a non-empty search', async () => {
    setList(many(3));
    render(Palette);
    await tick();
    await type(search(), 'Label 1');
    await key(search(), { key: 'Escape', code: 'Escape' });
    expect(search().value).toBe('');
    expect(options()).toHaveLength(3);
  });

  it('Home/End only move the selection when the search is empty', async () => {
    setList(many(30));
    render(Palette);
    await tick();
    await type(search(), 'Label');
    const before = selected()?.getAttribute('aria-posinset');
    await key(search(), { key: 'End', code: 'End' });
    expect(selected()?.getAttribute('aria-posinset')).toBe(before);
  });

  it('keys are ignored during IME composition and while a dialog is open', async () => {
    setList(many(3));
    render(Palette);
    await tick();
    await key(search(), { key: 'Enter', code: 'Enter', isComposing: true });
    await tick();
    expect(copy).not.toHaveBeenCalled();
    await key(search(), { key: '?', code: 'Slash', shiftKey: true });
    expect(dialog('Keyboard shortcuts')).toBeInTheDocument();
    await key(search(), { key: 'n', code: 'KeyN', altKey: true });
    await key(search(), { key: 'Enter', code: 'Enter' });
    await tick();
    expect(store.generate).not.toHaveBeenCalled();
    expect(copy).not.toHaveBeenCalled();
    expect(screen.getByTestId('palette-root')).toHaveAttribute('inert');
  });

  it('Alt shortcuts are ignored when Ctrl/Meta is also held', async () => {
    setList(many(3));
    render(Palette);
    await tick();
    await key(search(), { key: 'n', code: 'KeyN', altKey: true, ctrlKey: true });
    await key(search(), { key: 'e', code: 'KeyE', altKey: true, metaKey: true });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(store.generate).not.toHaveBeenCalled();
  });
});

describe('palette rendering of untrusted text', () => {
  it('test_hostile_label_and_note_render_as_text', async () => {
    const evil = '<img src=x onerror=alert(1)>';
    const evil2 = '"><script>alert(1)</script>';
    setList([alias(1, { label: evil, note: evil2, domain: '<b>d</b>', origin_app_name: '<svg onload=x>', forward_to: '<i>f</i>' })]);
    const { container } = render(Palette);
    await tick();
    expect(document.querySelector('img')).toBeNull();
    expect(document.querySelector('script')).toBeNull();
    expect(document.querySelector('svg[onload]')).toBeNull();
    expect(container.querySelector('b, i')).toBeNull();
    expect(container.textContent).toContain(evil);
    expect(container.textContent).toContain(evil2);
    expect(container.innerHTML).toContain('&lt;img src=x onerror=alert(1)&gt;');
    expect(container.innerHTML).toContain('&lt;script&gt;');
  });

  it('test_long_and_rtl_labels_do_not_break_row_height', async () => {
    const long = 'x'.repeat(10_000);
    const rtl = 'שלום עולם مرحبا';
    setList([alias(1, { label: long }), alias(2, { label: rtl }), alias(3, { label: 'a​b 🦊' })]);
    render(Palette);
    await tick();
    const root = screen.getByTestId('palette-root');
    expect(root.style.getPropertyValue('--row-h')).toBe(`${ROW_HEIGHT}px`);
    for (const o of options()) {
      expect(o).toHaveClass('row');
      const lab = o.querySelector('.label') as HTMLElement;
      expect(lab).toHaveClass('ellipsis');
      expect(lab).toHaveAttribute('dir', 'auto');
    }
    const css = readFileSync(resolve(import.meta.dirname, '../src/components/AliasRow.svelte'), 'utf8');
    expect(css).toMatch(/\.row\s*\{[^}]*height:\s*var\(--row-h\)/);
    expect(css).toMatch(/\.ellipsis\s*\{[^}]*text-overflow:\s*ellipsis/);
    expect(css).toMatch(/\.ellipsis\s*\{[^}]*white-space:\s*nowrap/);
    // the row text for a 10,000 char label is capped; the detail pane still shows it as text
    expect((options()[2]?.querySelector('.label')?.textContent ?? '').length).toBeLessThan(1000);
  });
});

describe('palette states', () => {
  it('test_empty_state_and_error_state_have_retry', async () => {
    render(Palette);
    await tick();
    expect(screen.getByText('No aliases yet. Press Alt+N to create your first one.')).toBeInTheDocument();
    store.__patch({ error: 'apple_unreachable' });
    await tick();
    expect(screen.getByText(/Can't reach Apple right now/)).toBeInTheDocument();
    await fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(store.refresh).toHaveBeenCalledTimes(1);
  });

  it('loading shows skeleton rows; refreshing shows Updating and the offline-copy hint', async () => {
    store.__patch({ loading: true });
    render(Palette);
    await tick();
    expect(listbox()).toHaveAttribute('aria-busy', 'true');
    expect(document.querySelectorAll('.skeleton').length).toBeGreaterThan(2);
    setList(many(3), { refreshing: true, fromCache: true });
    await tick();
    expect(screen.getByText('Updating…')).toBeInTheDocument();
    expect(screen.getByText('Offline copy')).toBeInTheDocument();
    store.__patch({ refreshing: false, fromCache: false });
    await tick();
    expect(screen.queryByText('Updating…')).toBeNull();
  });

  it('calls aliasStore.init once on mount when ready', async () => {
    render(Palette);
    await tick();
    expect(store.init).toHaveBeenCalledTimes(1);
  });

  it('detail pane shows the selected alias fields and mouse buttons', async () => {
    setList([alias(1, { label: 'Shop', note: 'a note', domain: 'shop.example', origin: 'SAFARI', origin_app_name: 'Safari', created_ms: Date.UTC(2024, 0, 2) })]);
    render(Palette);
    await tick();
    const pane = screen.getByRole('region', { name: 'Alias details' });
    for (const t of ['addr1@icloud.com', 'Shop', 'a note', 'me@example.com', 'shop.example', 'Active']) expect(pane).toHaveTextContent(t);
    expect(pane).toHaveTextContent(new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(Date.UTC(2024, 0, 2))));
    await fireEvent.click(within(pane).getByRole('button', { name: 'Copy address' }));
    await waitFor(() => expect(copy).toHaveBeenCalledWith('addr1@icloud.com'));
    await fireEvent.click(within(pane).getByRole('button', { name: 'Turn off' }));
    expect(store.setActive).toHaveBeenCalledWith('id-1', false);
    await fireEvent.click(within(pane).getByRole('button', { name: 'Edit' }));
    expect(dialog('Edit alias')).toBeInTheDocument();
  });
});

describe('actions menu and dialogs', () => {
  it('filters, disables selection actions with no selection, and runs by keyboard', async () => {
    render(Palette);
    await tick();
    await key(search(), { key: 'k', code: 'KeyK', ctrlKey: true });
    const menu = dialog('Actions');
    const filter = within(menu).getByRole('combobox', { name: 'Filter actions' });
    await waitFor(() => expect(filter).toHaveFocus());
    expect(within(menu).getByRole('option', { name: /Copy address/ })).toHaveAttribute('aria-disabled', 'true');
    expect(within(menu).getByRole('option', { name: /Delete/ })).toHaveAttribute('aria-disabled', 'true');
    expect(within(menu).getByRole('option', { name: /Refresh/ })).not.toHaveAttribute('aria-disabled', 'true');
    expect(within(menu).getByRole('option', { name: /New alias/ })).toHaveTextContent('Alt');
    await type(filter as HTMLInputElement, 'refr');
    expect(within(menu).getAllByRole('option')).toHaveLength(1);
    await key(filter, { key: 'Enter', code: 'Enter' });
    expect(store.refresh).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(search()).toHaveFocus());
  });

  it('arrow keys skip disabled actions; Esc closes and returns focus to search', async () => {
    setList(many(2));
    render(Palette);
    await tick();
    await key(search(), { key: 'k', code: 'KeyK', metaKey: true });
    const menu = dialog('Actions');
    const filter = within(menu).getByRole('combobox', { name: 'Filter actions' });
    const active = () => document.getElementById(filter.getAttribute('aria-activedescendant') ?? '');
    expect(active()).toHaveTextContent('Copy address');
    await key(filter, { key: 'ArrowDown', code: 'ArrowDown' });
    expect(active()).toHaveTextContent(/Turn off/);
    await key(filter, { key: 'Escape', code: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(search()).toHaveFocus());
  });

  it('change forward-to dialog calls setForwardTo', async () => {
    setList(many(2));
    render(Palette);
    await tick();
    await key(search(), { key: 'k', code: 'KeyK', ctrlKey: true });
    await fireEvent.click(within(dialog('Actions')).getByRole('option', { name: /Change forward-to/ }));
    const dlg = await screen.findByRole('dialog', { name: 'Change forward-to address' });
    const input = within(dlg).getByLabelText('Forward to') as HTMLInputElement;
    expect(input.value).toBe('me@example.com');
    await type(input, 'other@example.com');
    await key(input, { key: 'Enter', code: 'Enter' });
    expect(store.setForwardTo).toHaveBeenCalledWith('other@example.com');
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(push).toHaveBeenCalledWith({ kind: 'ok', text: 'Forwarding address updated' });
  });

  it('disconnect flow calls api.disconnect then clear then boot', async () => {
    setList(many(2));
    const order: string[] = [];
    vi.mocked(api.disconnect).mockImplementation(async () => {
      order.push('disconnect');
      return {} as never;
    });
    store.clear.mockImplementation(async () => {
      order.push('clear');
    });
    session.boot.mockImplementation(async () => {
      order.push('boot');
    });
    render(Palette);
    await tick();
    await key(search(), { key: 'k', code: 'KeyK', ctrlKey: true });
    await fireEvent.click(within(dialog('Actions')).getByRole('option', { name: /Disconnect Apple account/ }));
    const dlg = await screen.findByRole('dialog', { name: 'Disconnect Apple account?' });
    expect(api.disconnect).not.toHaveBeenCalled();
    await fireEvent.click(within(dlg).getByRole('button', { name: 'Disconnect' }));
    await waitFor(() => expect(order).toEqual(['disconnect', 'clear', 'boot']));
  });

  it('log out from the actions menu', async () => {
    setList(many(2));
    render(Palette);
    await tick();
    await key(search(), { key: 'k', code: 'KeyK', ctrlKey: true });
    await fireEvent.click(within(dialog('Actions')).getByRole('option', { name: /Log out/ }));
    await waitFor(() => expect(session.logout).toHaveBeenCalledTimes(1));
  });

  it('modal traps focus, sets inert and a body scroll lock, and restores focus to search', async () => {
    setList([alias(1)]);
    render(Palette);
    await tick();
    await key(search(), { key: 'e', code: 'KeyE', altKey: true });
    const dlg = dialog('Edit alias');
    expect(dlg).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByTestId('palette-root')).toHaveAttribute('inert');
    expect(document.body).toHaveClass('modal-open');
    const focusables = Array.from(dlg.querySelectorAll<HTMLElement>('input, textarea, button:not([disabled])'));
    const first = focusables[0] as HTMLElement;
    const last = focusables[focusables.length - 1] as HTMLElement;
    last.focus();
    await key(last, { key: 'Tab', code: 'Tab' });
    expect(first).toHaveFocus();
    await key(first, { key: 'Tab', code: 'Tab', shiftKey: true });
    expect(last).toHaveFocus();
    await key(last, { key: 'Escape', code: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByTestId('palette-root')).not.toHaveAttribute('inert');
    expect(document.body).not.toHaveClass('modal-open');
    await waitFor(() => expect(search()).toHaveFocus());
  });
});

describe('session expiry', () => {
  it('test_session_expired_during_action_shows_reconnect_and_keeps_dialog_input', async () => {
    setList([alias(1, { label: 'Old' })]);
    store.update.mockImplementation(async () => {
      session.__set({ state: 'reconnect', reason: 'session_expired' });
      throw new ApiError(401, 'session_expired');
    });
    render(Palette);
    await tick();
    const root = screen.getByTestId('palette-root');
    await key(search(), { key: 'e', code: 'KeyE', altKey: true });
    const dlg = dialog('Edit alias');
    const label = within(dlg).getByLabelText('Label') as HTMLInputElement;
    await type(label, 'Typed label');
    await key(label, { key: 'Enter', code: 'Enter' });
    await waitFor(() => expect(screen.getByText('Your Apple session expired. Reconnect to keep editing.')).toBeInTheDocument());
    expect(dialog('Edit alias')).toBeInTheDocument();
    expect(label.value).toBe('Typed label');
    expect(within(dlg).getByRole('alert')).toHaveTextContent(/Reconnect/);
    // reconnect from inside the dialog, then save again
    await fireEvent.click(within(dlg).getByRole('button', { name: 'Reconnect' }));
    const overlay = await screen.findByRole('dialog', { name: 'Reconnect your Apple account' });
    expect(within(overlay).getByLabelText('Apple ID')).toBeInTheDocument();
    await session.markReady();
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Reconnect your Apple account' })).toBeNull());
    expect(dialog('Edit alias')).toBeInTheDocument();
    expect((within(dialog('Edit alias')).getByLabelText('Label') as HTMLInputElement).value).toBe('Typed label');
    expect(screen.getByTestId('palette-root')).toBe(root);
    expect(screen.queryByText('Your Apple session expired. Reconnect to keep editing.')).toBeNull();
  });

  it('reconnect banner and overlay; palette stays mounted on markReady', async () => {
    setList(many(3));
    session.__set({ state: 'reconnect', reason: 'session_unreadable' });
    render(Palette);
    await tick();
    const root = screen.getByTestId('palette-root');
    expect(screen.getByText(/can't read the stored Apple session/)).toBeInTheDocument();
    // data stays browsable and copyable
    expect(options()).toHaveLength(3);
    await fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }));
    const overlay = await screen.findByRole('dialog', { name: 'Reconnect your Apple account' });
    expect(within(overlay).getByLabelText('Apple ID')).toBeInTheDocument();
    await session.markReady();
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByTestId('palette-root')).toBe(root);
    expect(store.init).toHaveBeenCalled();
  });

  it('mutating shortcuts in reconnect open the reconnect overlay instead of calling Apple', async () => {
    setList(many(3));
    session.__set({ state: 'reconnect', reason: 'session_expired' });
    render(Palette);
    await tick();
    await key(search(), { key: 'Enter', code: 'Enter', shiftKey: true });
    expect(store.setActive).not.toHaveBeenCalled();
    expect(await screen.findByRole('dialog', { name: 'Reconnect your Apple account' })).toBeInTheDocument();
  });
});

describe('source hygiene and accessibility smoke', () => {
  const dir = resolve(import.meta.dirname, '../src');
  const files = [
    ...readdirSync(join(dir, 'components')).map((f) => join(dir, 'components', f)),
    ...readdirSync(join(dir, 'lib')).map((f) => join(dir, 'lib', f))
  ];

  it('new components exist; no style="" literal and no console calls in sources', () => {
    for (const n of ['Palette', 'AliasRow', 'DetailPane', 'NewAlias', 'EditDialog', 'ConfirmDialog', 'ActionsMenu', 'ShortcutHelp', 'Modal', 'CopyFallback']) {
      expect(files.some((f) => f.endsWith(`/${n}.svelte`)), n).toBe(true);
    }
    for (const f of files) {
      const src = readFileSync(f, 'utf8');
      expect(src, f).not.toMatch(/style="/);
      expect(src, f).not.toMatch(/console\.\w+\(/);
    }
  });

  it('every button has an accessible name and every dialog a label', async () => {
    setList(many(3));
    render(Palette);
    await tick();
    const check = () => {
      for (const b of screen.queryAllByRole('button')) expect(b, b.outerHTML).toHaveAccessibleName();
      for (const d of screen.queryAllByRole('dialog')) expect(d).toHaveAccessibleName();
      for (const i of document.querySelectorAll('input, textarea')) expect(i, i.outerHTML).toHaveAccessibleName();
    };
    check();
    await key(search(), { key: 'k', code: 'KeyK', ctrlKey: true });
    check();
    await key(document.activeElement as Element, { key: 'Escape', code: 'Escape' });
    await key(search(), { key: 'e', code: 'KeyE', altKey: true });
    check();
  });
});
