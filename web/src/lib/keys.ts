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
  label: string;
  combos: Combo[];
  when?: 'searchEmpty' | 'searchNotEmpty';
  note?: string;
}

export const KEYMAP: readonly KeyBinding[] = [
  { id: 'copy', keys: [['Enter']], label: 'Copy address', combos: [{ key: 'Enter' }] },
  { id: 'toggle', keys: [['Shift', 'Enter']], label: 'Turn the address on or off', combos: [{ key: 'Enter', shift: true }] },
  { id: 'new', keys: [['Alt', 'N']], label: 'New alias', combos: [{ code: 'KeyN', alt: true }] },
  { id: 'edit', keys: [['Alt', 'E']], label: 'Edit label and note', combos: [{ code: 'KeyE', alt: true }] },
  { id: 'delete', keys: [['Alt', 'Backspace']], label: 'Delete', combos: [{ code: 'Backspace', alt: true }] },
  {
    id: 'menu',
    keys: [['Ctrl', 'K'], ['⌘', 'K']],
    label: 'All actions',
    combos: [
      { code: 'KeyK', ctrl: true, nonMac: true },
      { code: 'KeyK', meta: true }
    ]
  },
  {
    id: 'up',
    keys: [['↑'], ['Ctrl', 'K']],
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
  { id: 'help', keys: [['?']], label: 'Keyboard shortcuts (empty search)', combos: [{ key: '?', anyShift: true }], when: 'searchEmpty' }
];

export type KeyCtx = Record<KeyId, () => void> & {
  searchEmpty: boolean;
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

/** Runs the matching action. Returns true (and prevents the default) when it handled the event. */
export function handleKey(e: KeyboardEvent, ctx: KeyCtx): boolean {
  if (ctx.blocked || e.isComposing || e.keyCode === 229) return false;
  for (const b of KEYMAP) {
    if (b.when === 'searchEmpty' && !ctx.searchEmpty) continue;
    if (b.when === 'searchNotEmpty' && ctx.searchEmpty) continue;
    if (b.combos.some((c) => matches(e, c, ctx.mac))) {
      e.preventDefault();
      ctx[b.id]();
      return true;
    }
  }
  return false;
}
