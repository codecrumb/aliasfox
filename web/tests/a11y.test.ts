// Accessibility checks: axe (via vitest-axe) on every screen and dialog, plus keyboard
// reachability, focus return and focus trapping.
//
// axe rules DISABLED here (each is a jsdom limitation, not an app decision):
//   - color-contrast: jsdom has no layout or computed cascade for the scoped Svelte styles
//     (they are not even loaded in tests), so contrast cannot be computed. It is covered by the
//     token-contrast test in tests/css.test.ts, which checks every text/background token pair
//     against WCAG AA, and must be re-checked with Lighthouse in a real browser (README
//     release checklist).
//   - region: axe's "all content should be in a landmark" rule assumes a full page. Components
//     are rendered as fragments inside a bare <div>, without the page's <main> from index.html,
//     so the rule would flag every fragment. The real page landmark structure is checked in a
//     browser (Lighthouse) per the release checklist.
//   - scrollable-region-focusable: needs real layout (scrollHeight/clientHeight) to decide
//     whether a region scrolls; jsdom reports 0 for both, so it can neither pass nor fail
//     meaningfully. The virtual list is operated through the focused search combobox
//     (aria-activedescendant), by design.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { tick } from 'svelte';
import { get } from 'svelte/store';
import { axe } from 'vitest-axe';
import { CONFIRM_GUARD_MS } from '../src/lib/keys';
import { alias, many, type createAliasStore, type createSessionStore } from './fakes';

vi.mock('../src/lib/aliases', async () => ({ aliasStore: (await import('./fakes')).createAliasStore() }));
vi.mock('../src/lib/session', async () => ({ sessionStore: (await import('./fakes')).createSessionStore() }));
vi.mock('../src/lib/clipboard', () => ({ copyText: vi.fn(async () => 'copied') }));
vi.mock('../src/lib/api', async (orig) => {
  const real = await orig<typeof import('../src/lib/api')>();
  return {
    ...real,
    api: { disconnect: vi.fn(async () => ({})), connectLogin: vi.fn(), connectVerify: vi.fn(), connectResend: vi.fn(), connectCookie: vi.fn() }
  };
});

import { aliasStore as realAliasStore } from '../src/lib/aliases';
import { sessionStore as realSession } from '../src/lib/session';
import { api } from '../src/lib/api';
import { toasts } from '../src/lib/toasts';
import Login from '../src/components/Login.svelte';
import ConnectApple from '../src/components/ConnectApple.svelte';
import Palette from '../src/components/Palette.svelte';
import Toasts from '../src/components/Toasts.svelte';

const store = realAliasStore as unknown as ReturnType<typeof createAliasStore>;
const session = realSession as unknown as ReturnType<typeof createSessionStore>;

const AXE_OPTIONS = {
  rules: {
    'color-contrast': { enabled: false },
    region: { enabled: false },
    'scrollable-region-focusable': { enabled: false }
  }
};

async function expectNoViolations(root: Element = document.body): Promise<void> {
  const results = await axe(root, AXE_OPTIONS);
  const summary = results.violations.map((v) => ({
    rule: v.id,
    impact: v.impact,
    help: v.help,
    nodes: v.nodes.map((n) => n.html.slice(0, 160))
  }));
  expect(summary).toEqual([]);
  // The run must have actually checked something (guards against an empty page passing).
  expect(results.passes.length).toBeGreaterThan(0);
}

// --- keyboard helpers -------------------------------------------------------------------------

// Natively focusable controls plus anything given a tabindex: the browser's Tab stops (before
// filtering out disabled/negative-tabindex/inert ones).
const FOCUSABLE = 'a[href], button, input:not([type="hidden"]), select, textarea, summary, [tabindex]';
// Everything a user could try to operate: the focusable set plus elements that merely CLAIM an
// interactive role. A role-only element with no tabindex is not reachable by Tab, so it must be
// one of the documented exceptions in assertEveryInteractiveReachable.
const INTERACTIVE = `${FOCUSABLE}, [role="button"], [role="checkbox"], [role="menuitem"], [role="option"], [role="switch"], [role="tab"]`;

function isHidden(el: Element): boolean {
  return el.closest('[inert], [hidden], [aria-hidden="true"]') !== null;
}

/** Elements the Tab key visits, in order. With no positive tabindex this is DOM order. */
function tabbables(root: ParentNode = document.body): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => {
    if ((el as HTMLButtonElement).disabled) return false;
    const ti = el.getAttribute('tabindex');
    if (ti !== null && Number(ti) < 0) return false;
    if (el instanceof HTMLInputElement && el.readOnly && el.getAttribute('tabindex') === '-1') return false;
    return !isHidden(el);
  });
}

function nameOf(el: HTMLElement): string {
  const aria = el.getAttribute('aria-label');
  if (aria) return aria;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
    const l = el.labels?.[0]?.textContent?.trim();
    if (l) return l;
  }
  const clone = el.cloneNode(true) as HTMLElement;
  clone.querySelectorAll('[aria-hidden="true"]').forEach((n) => n.remove());
  return (clone.textContent ?? '').replace(/\s+/g, ' ').trim();
}

const tabNames = (root?: ParentNode): string[] => tabbables(root).map(nameOf);

/**
 * Every interactive element is either tabbable or one of the documented exceptions:
 *  - listbox options: operated through the combobox that owns them (aria-activedescendant);
 *  - tabindex="-1" containers and the dialog panel: programmatic focus targets only;
 *  - elements that are disabled, inert or aria-hidden (not exposed to users at that moment).
 */
function assertEveryInteractiveReachable(root: ParentNode = document.body): void {
  const reachable = new Set(tabbables(root));
  const stray: string[] = [];
  for (const el of Array.from(root.querySelectorAll<HTMLElement>(INTERACTIVE))) {
    if (reachable.has(el)) continue;
    if ((el as HTMLButtonElement).disabled) continue;
    if (isHidden(el)) continue;
    if (el.getAttribute('role') === 'option') continue;
    const ti = el.getAttribute('tabindex');
    if (ti === '-1' && !/^(a|button|input|select|textarea|summary)$/i.test(el.tagName)) continue;
    stray.push(`${el.tagName.toLowerCase()} "${nameOf(el)}"`);
  }
  expect(stray).toEqual([]);
}

function assertNoPositiveTabindex(root: ParentNode = document.body): void {
  const bad = Array.from(root.querySelectorAll('[tabindex]')).filter((e) => Number(e.getAttribute('tabindex')) > 0);
  expect(bad).toEqual([]);
}

const press = (el: Element, init: KeyboardEventInit) => fireEvent.keyDown(el, init);
const search = () => screen.getByRole('combobox', { name: 'Search aliases' }) as HTMLInputElement;
const pastGuard = () => new Promise((r) => setTimeout(r, CONFIRM_GUARD_MS + 20));

function setList(list = many(5), extra = {}) {
  store.__set(list, { loading: false, refreshing: false, error: null, loadError: null, fromCache: false, ...extra });
}

const realistic = () => [
  alias(1, { label: 'Newsletter', note: 'weekly digest', domain: 'news.example.com', origin: 'SAFARI' }),
  alias(2, { label: 'Shopping', is_active: false, origin: 'IN_APP', origin_app_name: 'Shop', app_bundle_id: 'com.example.shop' }),
  alias(3, { label: null, forward_to: null }),
  alias(4, { label: 'עברית RTL label', note: 'x'.repeat(300) }),
  alias(5, { label: '<img src=x onerror=alert(1)>', origin: 'MAIL' })
];

beforeEach(() => {
  for (const m of [store.init, store.refresh, store.generate, store.reserve, store.update, store.setActive, store.remove, store.setForwardTo, store.clear]) {
    m.mockClear();
  }
  vi.mocked(api.connectLogin).mockReset();
  vi.mocked(api.connectVerify).mockReset();
  vi.mocked(api.connectCookie).mockReset();
  session.__set({ state: 'ready', reason: null });
  setList([]);
  for (const t of get(toasts)) toasts.dismiss(t.id);
});
afterEach(() => {
  cleanup();
  document.body.className = '';
});

// --- Login ------------------------------------------------------------------------------------

describe('a11y: Login', () => {
  it('has no axe violations', async () => {
    render(Login);
    await expectNoViolations();
  });

  it('is keyboard reachable in a sensible order with no positive tabindex', () => {
    render(Login);
    expect(tabNames()).toEqual(['Password', 'Log in']);
    assertEveryInteractiveReachable();
    assertNoPositiveTabindex();
    expect(screen.getByLabelText('Password')).toHaveFocus();
  });
});

// --- ConnectApple -----------------------------------------------------------------------------

const idField = () => screen.getByLabelText('Apple ID') as HTMLInputElement;
const pwField = () => screen.getByLabelText('Password') as HTMLInputElement;

async function toPassword() {
  await fireEvent.input(idField(), { target: { value: 'me@icloud.com' } });
  await fireEvent.submit(idField().closest('form') as HTMLFormElement);
  await screen.findByLabelText('Password');
}
async function toCode() {
  vi.mocked(api.connectLogin).mockResolvedValue({ status: 'needs_2fa', flow_id: 'flow-1' });
  await toPassword();
  await fireEvent.input(pwField(), { target: { value: 'hunter2' } });
  await fireEvent.submit(pwField().closest('form') as HTMLFormElement);
  await screen.findByRole('group', { name: 'Verification code' });
}

describe('a11y: ConnectApple', () => {
  it('step 1 (Apple ID): no violations; tab order', async () => {
    render(ConnectApple);
    await expectNoViolations();
    expect(tabNames()).toEqual(['Apple ID', 'Continue', 'Use a cookie instead', 'Log out']);
    assertEveryInteractiveReachable();
    assertNoPositiveTabindex();
    expect(idField()).toHaveFocus();
  });

  it('step 2 (password): no violations; tab order', async () => {
    render(ConnectApple);
    await toPassword();
    await expectNoViolations();
    expect(tabNames()).toEqual(['Password', 'My Apple ID is for mainland China', 'Back', 'Continue', 'Use a cookie instead', 'Log out']);
    assertEveryInteractiveReachable();
    assertNoPositiveTabindex();
    expect(pwField()).toHaveFocus();
  });

  it('step 3 (code): no violations; six digit boxes, then Back and Resend code, in order', async () => {
    render(ConnectApple);
    await toCode();
    await expectNoViolations();
    const names = tabNames();
    expect(names.slice(-3)).toEqual(['Back', 'Resend code', 'Log out']);
    expect(names.slice(0, -3)).toHaveLength(6);
    expect(names.slice(0, -3)).toEqual([1, 2, 3, 4, 5, 6].map((n) => `Digit ${n} of 6`));
    assertEveryInteractiveReachable();
    assertNoPositiveTabindex();
  });

  it('cookie panel open: no violations; the panel follows its toggle in tab order', async () => {
    render(ConnectApple);
    await fireEvent.click(screen.getByRole('button', { name: 'Use a cookie instead' }));
    await expectNoViolations();
    expect(tabNames()).toEqual(['Apple ID', 'Continue', 'Use a cookie instead', 'Cookie header', 'Log out']);
    // 'Connect with cookie' is disabled until something is pasted
    await fireEvent.input(screen.getByLabelText('Cookie header'), { target: { value: 'a=b' } });
    expect(tabNames()).toEqual(['Apple ID', 'Continue', 'Use a cookie instead', 'Cookie header', 'Connect with cookie', 'Log out']);
    assertEveryInteractiveReachable();
    assertNoPositiveTabindex();
  });

  it('error and reconnect states have no violations', async () => {
    session.__set({ state: 'reconnect', reason: 'session_expired' });
    render(ConnectApple);
    await expectNoViolations();
  });
});

// --- Palette ----------------------------------------------------------------------------------

describe('a11y: Palette', () => {
  it('empty state: no violations; tab order', async () => {
    render(Palette);
    await tick();
    await expectNoViolations();
    expect(tabNames()).toEqual(['New alias', 'Actions', 'Search aliases', 'All', 'Active', 'Inactive']);
    assertEveryInteractiveReachable();
    assertNoPositiveTabindex();
  });

  it('error state with Retry: no violations; Retry is reachable', async () => {
    store.__patch({ loadError: 'apple_unreachable', error: 'apple_unreachable' });
    render(Palette);
    await tick();
    await expectNoViolations();
    expect(tabNames()).toContain('Retry');
    assertEveryInteractiveReachable();
  });

  it('loading state: no violations', async () => {
    store.__patch({ loading: true });
    render(Palette);
    await tick();
    await expectNoViolations();
  });

  it('populated list with hostile/RTL/long values: no violations', async () => {
    setList(realistic());
    render(Palette);
    await tick();
    expect(screen.getAllByRole('option').length).toBe(5);
    await expectNoViolations();
  });

  it('a selected alias with its detail pane: no violations; tab order', async () => {
    setList(realistic());
    render(Palette);
    await tick();
    await press(search(), { key: 'ArrowDown', code: 'ArrowDown' });
    await expectNoViolations();
    expect(tabNames()).toEqual([
      'New alias',
      'Actions',
      'Search aliases',
      'All',
      'Active',
      'Inactive',
      'Copy address',
      'Turn off',
      'Edit',
      'Delete'
    ]);
    assertEveryInteractiveReachable();
    assertNoPositiveTabindex();
  });

  it('filtered to nothing: no violations', async () => {
    setList(realistic());
    render(Palette);
    await tick();
    await fireEvent.input(search(), { target: { value: 'zzzzqqqq' } });
    await tick();
    await expectNoViolations();
  });

  it('reconnect banner: no violations; Reconnect is reachable', async () => {
    setList(realistic());
    session.__set({ state: 'reconnect', reason: 'session_expired' });
    render(Palette);
    await tick();
    await expectNoViolations();
    expect(tabNames()).toContain('Reconnect');
  });
});

// --- Dialogs ----------------------------------------------------------------------------------

interface DialogCase {
  name: string;
  title: string | RegExp;
  open: () => Promise<void>;
  /** Labels in tab order inside the dialog. */
  order?: string[];
  /** Esc is swallowed for a moment by confirm dialogs; wait out the guard before closing. */
  guarded?: boolean;
}

const CASES: DialogCase[] = [
  {
    name: 'NewAlias',
    title: 'New alias',
    open: async () => {
      await press(search(), { key: 'n', code: 'KeyN', altKey: true });
    }
  },
  {
    name: 'EditDialog',
    title: /Edit/,
    open: async () => {
      await press(search(), { key: 'e', code: 'KeyE', altKey: true });
    }
  },
  {
    name: 'ConfirmDialog',
    title: 'Delete this address?',
    open: async () => {
      await press(search(), { key: 'Backspace', code: 'Backspace', altKey: true });
    },
    order: ['Cancel', 'Delete'],
    guarded: true
  },
  {
    name: 'ActionsMenu',
    title: /actions/i,
    open: async () => {
      await press(search(), { key: 'k', code: 'KeyK', ctrlKey: true });
    }
  },
  {
    name: 'ShortcutHelp',
    title: 'Keyboard shortcuts',
    open: async () => {
      await press(search(), { key: '?', code: 'Slash', shiftKey: true });
    },
    order: ['Close']
  },
  {
    name: 'ForwardToDialog',
    title: /forward/i,
    open: async () => {
      await press(search(), { key: 'k', code: 'KeyK', ctrlKey: true });
      await fireEvent.click(await screen.findByRole('option', { name: /Change forward-to address/ }));
      await screen.findByRole('dialog', { name: /forward/i });
    }
  }
];

describe.each(CASES)('a11y: $name', (c) => {
  async function openIt() {
    setList(realistic());
    render(Palette);
    await tick();
    await c.open();
    return screen.findByRole('dialog', { name: c.title });
  }

  it('has no axe violations', async () => {
    await openIt();
    await expectNoViolations();
  });

  it('focus is inside, only the dialog is tabbable, and there is no positive tabindex', async () => {
    const dlg = await openIt();
    expect(dlg.contains(document.activeElement)).toBe(true);
    const outside = tabbables().filter((el) => !dlg.contains(el));
    expect(outside.map(nameOf)).toEqual([]);
    if (c.order) expect(tabNames(dlg)).toEqual(c.order);
    assertNoPositiveTabindex();
    assertEveryInteractiveReachable(dlg);
  });

  it('traps focus: Tab from the last control wraps to the first, Shift+Tab the other way', async () => {
    const dlg = await openIt();
    const items = tabbables(dlg);
    expect(items.length).toBeGreaterThan(0);
    const first = items[0] as HTMLElement;
    const last = items[items.length - 1] as HTMLElement;
    last.focus();
    await press(last, { key: 'Tab', code: 'Tab' });
    expect(first).toHaveFocus();
    await press(first, { key: 'Tab', code: 'Tab', shiftKey: true });
    expect(last).toHaveFocus();
    // focus that has strayed outside the dialog is pulled back in
    (search() as HTMLElement).focus();
    await press(dlg, { key: 'Tab', code: 'Tab' });
    expect(dlg.contains(document.activeElement)).toBe(true);
  });

  it('returns focus to the search field when it closes', async () => {
    const dlg = await openIt();
    if (c.guarded) await pastGuard();
    await press(dlg, { key: 'Escape', code: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    await waitFor(() => expect(search()).toHaveFocus());
  });
});

describe('a11y: dialogs on top of dialogs', () => {
  it('closing the actions menu after choosing help leaves the help dialog trapping focus, then returns to search', async () => {
    setList(realistic());
    render(Palette);
    await tick();
    await press(search(), { key: 'k', code: 'KeyK', ctrlKey: true });
    await fireEvent.click(await screen.findByRole('option', { name: /Keyboard shortcuts/ }));
    const help = await screen.findByRole('dialog', { name: 'Keyboard shortcuts' });
    expect(help.contains(document.activeElement)).toBe(true);
    await press(help, { key: 'Escape', code: 'Escape' });
    await waitFor(() => expect(search()).toHaveFocus());
  });
});

// --- Toasts -----------------------------------------------------------------------------------

describe('a11y: Toasts', () => {
  it('ok and error toasts have no violations and their dismiss buttons are reachable', async () => {
    render(Toasts);
    toasts.push({ kind: 'ok', text: 'Copied fox@icloud.com' });
    toasts.push({ kind: 'error', text: "Couldn't delete fox@icloud.com. Try again." });
    await tick();
    await expectNoViolations();
    expect(tabNames()).toEqual(['Dismiss notification', 'Dismiss notification']);
    assertEveryInteractiveReachable();
    assertNoPositiveTabindex();
    expect(screen.getByRole('alert')).toHaveTextContent("Couldn't delete");
  });

  it('an empty toast region has no violations', async () => {
    render(Toasts);
    await expectNoViolations();
  });
});
