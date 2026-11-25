// The palette keyboard map. ShortcutHelp renders KEYMAP and handleKey dispatches from it, so the
// documentation and the behaviour come from the same table.

export type KeyId =
  | 'copy'
  | 'toggle'
  | 'new'
  | 'edit'
  | 'delete'
  | 'menu'
  | 'up'
  | 'down'
  | 'first'
  | 'last'
  | 'pageUp'
  | 'pageDown'
  | 'clear'
  | 'refresh'
  | 'filter'
  | 'sort'
  | 'blur'
  | 'mark'
  | 'markAll'
  | 'markDown'
  | 'markUp'
  | 'clearMarks'
  | 'undo'
  | 'help';

export interface Combo {
  /** Physical key (event.code); used for letter shortcuts so Alt/Option dead keys still match. */
  code?: string;
  /** Logical key (event.key); used for named keys and printable symbols like '?'. */
  key?: string;
  alt?: boolean;
  shift?: boolean;
  ctrl?: boolean;
  meta?: boolean;
  /** Shift state is irrelevant (e.g. '?' needs Shift on most layouts, not on others). */
  anyShift?: boolean;
  /** Only on a Mac / only elsewhere. */
  mac?: boolean;
  nonMac?: boolean;
}

export interface KeyBinding {
  id: KeyId;
  /** Display: alternatives, each a list of key caps. */
  keys: string[][];
  /** Display on a Mac when it differs from `keys`. */
  keysMac?: string[][];
  label: string;
  combos: Combo[];
  when?: 'searchEmpty' | 'searchNotEmpty' | 'caretAtStart' | 'marksOnly';
  note?: string;
}

/** Bindings that keep working while a key is held down (auto-repeat). Everything else fires once
 * per physical press, so a held Enter or Shift+Enter can never repeat an account action. */
const REPEATABLE: ReadonlySet<KeyId> = new Set<KeyId>(['up', 'down', 'pageUp', 'pageDown', 'first', 'last']);

/**
 * A confirmation dialog ignores Enter/Space (and keyboard-generated clicks) for this long after it
 * opens, so the key press that opened it (e.g. Enter in the actions menu) can't also confirm it.
 */
export const CONFIRM_GUARD_MS = 250;

export const KEYMAP: readonly KeyBinding[] = [
  { id: 'copy', keys: [['Enter']], label: 'Copy address', combos: [{ key: 'Enter' }] },
  {
    id: 'toggle',
    keys: [['Shift', 'Enter']],
    label: 'Deactivate or reactivate the address',
    // Alt+D is an undocumented alias (no entry in `keys`): easier to remember than Shift+Enter.
    combos: [{ key: 'Enter', shift: true }, { code: 'KeyD', alt: true }]
  },
  { id: 'new', keys: [['Alt', 'N']], label: 'New alias', combos: [{ code: 'KeyN', alt: true }] },
  { id: 'edit', keys: [['Alt', 'E']], label: 'Edit label and note', combos: [{ code: 'KeyE', alt: true }] },
  {
    id: 'delete',
    keys: [['Alt', 'Backspace']],
    label: 'Delete alias — when the search box is empty or the caret is at the start',
    combos: [{ code: 'Backspace', alt: true }],
    // In a non-empty search Alt+Backspace is the platform's delete-word keystroke.
    when: 'caretAtStart'
  },
  {
    id: 'menu',
    keys: [['Ctrl', 'K']],
    keysMac: [['⌘', 'K']],
    label: 'All actions',
    combos: [
      { code: 'KeyK', ctrl: true, nonMac: true },
      { code: 'KeyK', meta: true }
    ]
  },
  {
    id: 'up',
    keys: [['↑']],
    keysMac: [['↑'], ['Ctrl', 'K']],
    label: 'Previous alias',
    combos: [{ key: 'ArrowUp' }, { code: 'KeyK', ctrl: true, mac: true }],
    note: 'Ctrl K moves up on a Mac, where ⌘ K opens the actions.'
  },
  { id: 'down', keys: [['↓'], ['Ctrl', 'J']], label: 'Next alias', combos: [{ key: 'ArrowDown' }, { code: 'KeyJ', ctrl: true }] },
  { id: 'pageUp', keys: [['Page Up']], label: 'Up one page', combos: [{ key: 'PageUp' }] },
  { id: 'pageDown', keys: [['Page Down']], label: 'Down one page', combos: [{ key: 'PageDown' }] },
  { id: 'first', keys: [['Home']], label: 'First alias (empty search)', combos: [{ key: 'Home' }], when: 'searchEmpty' },
  { id: 'last', keys: [['End']], label: 'Last alias (empty search)', combos: [{ key: 'End' }], when: 'searchEmpty' },
  { id: 'clear', keys: [['Esc']], label: 'Clear the search', combos: [{ key: 'Escape' }], when: 'searchNotEmpty' },
  { id: 'refresh', keys: [['Alt', 'R']], label: 'Refresh from Apple', combos: [{ code: 'KeyR', alt: true }] },
  { id: 'filter', keys: [['Alt', 'F']], label: 'Cycle All, Active and Inactive', combos: [{ code: 'KeyF', alt: true }] },
  { id: 'sort', keys: [['Alt', 'S']], label: 'Cycle the sort order', combos: [{ code: 'KeyS', alt: true }] },
  { id: 'blur', keys: [['Alt', 'B']], label: 'Blur or show the addresses (hover or select a row to reveal it)', combos: [{ code: 'KeyB', alt: true }] },
  { id: 'mark', keys: [['Alt', 'X']], label: 'Select or unselect this alias for a bulk action', combos: [{ code: 'KeyX', alt: true }] },
  { id: 'markAll', keys: [['Alt', 'A']], label: 'Select every alias in the list (again to unselect)', combos: [{ code: 'KeyA', alt: true }] },
  { id: 'markDown', keys: [['Shift', '↓']], label: 'Select this alias and the next one', combos: [{ key: 'ArrowDown', shift: true }] },
  { id: 'markUp', keys: [['Shift', '↑']], label: 'Select this alias and the previous one', combos: [{ key: 'ArrowUp', shift: true }] },
  {
    id: 'clearMarks',
    keys: [['Esc']],
    label: 'Clear the selection (empty search)',
    combos: [{ key: 'Escape' }],
    when: 'marksOnly'
  },
  {
    id: 'undo',
    keys: [['Ctrl', 'Z']],
    keysMac: [['⌘', 'Z']],
    label: 'Undo the last deactivate, reactivate or import (empty search, while its Undo button shows)',
    combos: [
      { code: 'KeyZ', ctrl: true, nonMac: true },
      { code: 'KeyZ', meta: true, mac: true }
    ],
    // With text in the search box Cmd/Ctrl+Z belongs to the text field.
    when: 'searchEmpty'
  },
  { id: 'help', keys: [['?']], label: 'Keyboard shortcuts (empty search)', combos: [{ key: '?', anyShift: true }], when: 'searchEmpty' }
];

export type KeyCtx = Record<KeyId, () => void> & {
  searchEmpty: boolean;
  /** The search caret is at position 0 with no selection. */
  caretAtStart?: boolean;
  /** At least one alias is selected for a bulk action. */
  hasMarks?: boolean;
  /** Alt/Option+1..9,0: select the n-th row (0-based) of the visible list. */
  jump?: (n: number) => void;
  /** A dialog or menu owns the keyboard. */
  blocked: boolean;
  mac: boolean;
};

function matches(e: KeyboardEvent, c: Combo, mac: boolean): boolean {
  if (c.mac && !mac) return false;
  if (c.nonMac && mac) return false;
  if (c.code !== undefined && e.code !== c.code) return false;
  if (c.key !== undefined && e.key !== c.key) return false;
  if (e.altKey !== !!c.alt) return false;
  if (e.ctrlKey !== !!c.ctrl) return false;
  if (e.metaKey !== !!c.meta) return false;
  if (!c.anyShift && e.shiftKey !== !!c.shift) return false;
  return true;
}

export function isMac(): boolean {
  const nav = typeof navigator === 'undefined' ? undefined : navigator;
  const p = nav?.platform ?? '';
  return /mac|iphone|ipad|ipod/i.test(p);
}

/** The key caps to show for a binding on this platform. */
export function keysFor(b: KeyBinding, mac: boolean): string[][] {
  return mac && b.keysMac ? b.keysMac : b.keys;
}

const MAC_CAPS: Record<string, string> = { Alt: '⌥', Shift: '⇧', Ctrl: '⌃', Enter: '↵', Backspace: '⌫' };
const SPOKEN: Record<string, string> = { '⌥': 'Option', '⇧': 'Shift', '⌃': 'Control', '⌘': 'Command', '↵': 'Enter', '⌫': 'Delete' };

/** One key cap as this platform shows it: the Mac symbols (⌥ ⇧ ⌃ ↵ ⌫), or the plain name elsewhere. */
export function cap(name: string, mac: boolean): string {
  return mac ? (MAC_CAPS[name] ?? name) : name;
}

/** The name a screen reader should say for a key cap (symbols are read badly or not at all). */
export function spoken(capText: string): string | undefined {
  return SPOKEN[capText];
}

/**
 * Option+letter on macOS starts a dead key (Option+E is the acute accent). preventDefault doesn't
 * stop it: the accent is inserted into whatever has focus once the key is resolved, which after a
 * shortcut that opens a dialog is the dialog's field. A field arms this guard when it takes focus
 * and calls `restore` from its input/compositionend handlers: within DEAD_KEY_MS it gives back the
 * value the field had when armed. Any real keystroke disarms it.
 */
export const DEAD_KEY_MS = 600;

/** Cmd/Ctrl+Enter (not mid-composition): the "submit this dialog" chord. */
export function isSubmitChord(e: KeyboardEvent): boolean {
  return e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.isComposing;
}

/** An input event produced by composition (an IME or a dead key), not by typing or pasting. */
export function isCompositionInput(e: Event): boolean {
  const i = e as Partial<InputEvent>;
  return i.isComposing === true || i.inputType === 'insertCompositionText';
}

export function createDeadKeyGuard() {
  let armed: { value: string; until: number } | null = null;
  return {
    arm(value: string): void {
      armed = { value, until: performance.now() + DEAD_KEY_MS };
    },
    /** Call from keydown. */
    key(e: KeyboardEvent): void {
      if (e.key !== 'Dead' && e.key !== 'Process') armed = null;
    },
    /** The value to put back, or null when the input was real. */
    restore(): string | null {
      if (armed && performance.now() <= armed.until) return armed.value;
      armed = null;
      return null;
    }
  };
}

/** Option+letter on macOS: a dead key that WebKit/Chromium may report with keyCode 229. */
export function isAltLetter(e: KeyboardEvent): boolean {
  return e.altKey && !e.ctrlKey && !e.metaKey && /^Key[A-Z]$/.test(e.code);
}

/** Alt/Option+1..9 is 0..8 and Alt/Option+0 is 9: the row a number shortcut points at, else null. */
export function altDigit(e: KeyboardEvent): number | null {
  if (!e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return null;
  const m = /^Digit([0-9])$/.exec(e.code);
  return m === null ? null : (Number(m[1]) + 9) % 10;
}

/** Runs the matching action. Returns true (and prevents the default) when it handled the event. */
export function handleKey(e: KeyboardEvent, ctx: KeyCtx): boolean {
  if (ctx.blocked || e.isComposing === true) return false;
  // keyCode 229 without isComposing: an IME is processing the key (e.g. Safari's Enter that
  // commits a composition). Ignore it, except Option+letter dead keys, matched by event.code.
  if (e.keyCode === 229 && !isAltLetter(e)) return false;
  const digit = altDigit(e);
  if (digit !== null && ctx.jump) {
    e.preventDefault(); // Option+digit types a symbol on a Mac
    if (!e.repeat) ctx.jump(digit);
    return true;
  }
  for (const b of KEYMAP) {
    if (b.when === 'searchEmpty' && !ctx.searchEmpty) continue;
    if (b.when === 'searchNotEmpty' && ctx.searchEmpty) continue;
    if (b.when === 'marksOnly' && !(ctx.hasMarks && ctx.searchEmpty)) continue;
    if (b.when === 'caretAtStart' && !ctx.searchEmpty && !ctx.caretAtStart) continue;
    if (b.combos.some((c) => matches(e, c, ctx.mac))) {
      e.preventDefault();
      if (e.repeat && !REPEATABLE.has(b.id)) return true; // swallowed: held keys act once
      ctx[b.id]();
      return true;
    }
  }
  return false;
}
