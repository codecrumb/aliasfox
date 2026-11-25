import { describe, expect, it, vi } from 'vitest';
import { KEYMAP, altDigit, cap, createDeadKeyGuard, handleKey, keysFor, spoken, type KeyCtx, type KeyId } from '../src/lib/keys';

function ctx(over: Partial<KeyCtx> = {}): KeyCtx {
  const ids: KeyId[] = KEYMAP.map((b) => b.id);
  const fns = Object.fromEntries(ids.map((id) => [id, vi.fn()])) as unknown as Record<KeyId, () => void>;
  return { searchEmpty: true, blocked: false, mac: false, ...fns, ...over };
}

function ev(init: KeyboardEventInit): KeyboardEvent {
  return new KeyboardEvent('keydown', { bubbles: true, cancelable: true, ...init });
}

describe('KEYMAP / handleKey', () => {
  it('every KEYMAP id has a working handler (docs and behaviour cannot drift)', () => {
    expect(KEYMAP.length).toBeGreaterThanOrEqual(14);
    const seen = new Set<string>();
    for (const b of KEYMAP) {
      expect(seen.has(b.id), `duplicate id ${b.id}`).toBe(false);
      seen.add(b.id);
      expect(b.label.length).toBeGreaterThan(0);
      expect(b.keys.length).toBeGreaterThan(0);
      for (const combo of b.combos) {
        const c = ctx({ searchEmpty: b.when !== 'searchNotEmpty', hasMarks: b.when === 'marksOnly', mac: combo.mac === true });
        const e = ev({
          key: combo.key ?? '',
          code: combo.code ?? '',
          altKey: !!combo.alt,
          shiftKey: !!combo.shift,
          ctrlKey: !!combo.ctrl,
          metaKey: !!combo.meta
        });
        expect(handleKey(e, c), `${b.id} ${JSON.stringify(combo)}`).toBe(true);
        expect(c[b.id], b.id).toHaveBeenCalledTimes(1);
        expect(e.defaultPrevented).toBe(true);
        for (const other of KEYMAP) if (other.id !== b.id) expect(c[other.id], `${b.id} also fired ${other.id}`).not.toHaveBeenCalled();
      }
    }
  });

  it('exposes the documented ids', () => {
    expect(KEYMAP.map((b) => b.id).sort()).toEqual(
      ['clear', 'copy', 'delete', 'down', 'edit', 'filter', 'first', 'help', 'last', 'menu', 'new', 'pageDown', 'pageUp', 'refresh', 'sort', 'blur', 'toggle', 'up', 'mark', 'markAll', 'markDown', 'markUp', 'clearMarks', 'undo'].sort()
    );
  });

  it('Enter copies, Shift+Enter toggles', () => {
    const c = ctx();
    handleKey(ev({ key: 'Enter', code: 'Enter' }), c);
    handleKey(ev({ key: 'Enter', code: 'Enter', shiftKey: true }), c);
    expect(c.copy).toHaveBeenCalledTimes(1);
    expect(c.toggle).toHaveBeenCalledTimes(1);
  });

  it('Alt+D also toggles (hidden alias of Shift+Enter)', () => {
    const c = ctx();
    expect(handleKey(ev({ key: '∂', code: 'KeyD', altKey: true }), c)).toBe(true);
    expect(c.toggle).toHaveBeenCalledTimes(1);
  });

  it('Alt shortcuts use event.code (Option+N on a Mac types a dead key)', () => {
    const c = ctx();
    expect(handleKey(ev({ key: '˜', code: 'KeyN', altKey: true }), c)).toBe(true);
    expect(c.new).toHaveBeenCalledTimes(1);
  });

  it('Alt shortcuts are ignored when Ctrl or Meta is also held', () => {
    const c = ctx();
    expect(handleKey(ev({ key: 'n', code: 'KeyN', altKey: true, ctrlKey: true }), c)).toBe(false);
    expect(handleKey(ev({ key: 'e', code: 'KeyE', altKey: true, metaKey: true }), c)).toBe(false);
    expect(handleKey(ev({ key: 'Backspace', code: 'Backspace', altKey: true, ctrlKey: true }), c)).toBe(false);
    expect(c.new).not.toHaveBeenCalled();
    expect(c.edit).not.toHaveBeenCalled();
    expect(c.delete).not.toHaveBeenCalled();
  });

  it('ignores events during IME composition', () => {
    const c = ctx();
    const e = ev({ key: 'Enter', code: 'Enter', isComposing: true });
    expect(handleKey(e, c)).toBe(false);
    const e229 = ev({ key: 'Process', code: 'Enter' });
    Object.defineProperty(e229, 'keyCode', { value: 229 });
    expect(handleKey(e229, c)).toBe(false);
    expect(c.copy).not.toHaveBeenCalled();
    expect(e.defaultPrevented).toBe(false);
  });

  it('does nothing while blocked (a dialog or menu owns the keyboard)', () => {
    const c = ctx({ blocked: true });
    expect(handleKey(ev({ key: 'Enter', code: 'Enter' }), c)).toBe(false);
    expect(handleKey(ev({ key: 'n', code: 'KeyN', altKey: true }), c)).toBe(false);
    expect(c.copy).not.toHaveBeenCalled();
    expect(c.new).not.toHaveBeenCalled();
  });

  it('Home/End only when the search is empty; otherwise the caret moves', () => {
    const c = ctx({ searchEmpty: false });
    const home = ev({ key: 'Home', code: 'Home' });
    expect(handleKey(home, c)).toBe(false);
    expect(home.defaultPrevented).toBe(false);
    expect(handleKey(ev({ key: 'End', code: 'End' }), c)).toBe(false);
    expect(c.first).not.toHaveBeenCalled();
    const c2 = ctx({ searchEmpty: true });
    expect(handleKey(ev({ key: 'End', code: 'End' }), c2)).toBe(true);
    expect(c2.last).toHaveBeenCalled();
  });

  it('? opens help only when the search is empty; otherwise it types', () => {
    const c = ctx({ searchEmpty: false });
    expect(handleKey(ev({ key: '?', code: 'Slash', shiftKey: true }), c)).toBe(false);
    expect(c.help).not.toHaveBeenCalled();
    const c2 = ctx();
    expect(handleKey(ev({ key: '?', code: 'Slash', shiftKey: true }), c2)).toBe(true);
    expect(c2.help).toHaveBeenCalled();
  });

  it('Esc clears only a non-empty search', () => {
    const c = ctx({ searchEmpty: true });
    expect(handleKey(ev({ key: 'Escape', code: 'Escape' }), c)).toBe(false);
    const c2 = ctx({ searchEmpty: false });
    expect(handleKey(ev({ key: 'Escape', code: 'Escape' }), c2)).toBe(true);
    expect(c2.clear).toHaveBeenCalled();
  });

  it('Ctrl+K opens the menu off-Mac; on a Mac Cmd+K opens it and Ctrl+K moves up', () => {
    const pc = ctx({ mac: false });
    handleKey(ev({ key: 'k', code: 'KeyK', ctrlKey: true }), pc);
    expect(pc.menu).toHaveBeenCalledTimes(1);
    expect(pc.up).not.toHaveBeenCalled();
    const mac = ctx({ mac: true });
    handleKey(ev({ key: 'k', code: 'KeyK', metaKey: true }), mac);
    handleKey(ev({ key: 'k', code: 'KeyK', ctrlKey: true }), mac);
    expect(mac.menu).toHaveBeenCalledTimes(1);
    expect(mac.up).toHaveBeenCalledTimes(1);
  });

  it('Ctrl+J moves down; plain letters are left to the search field', () => {
    const c = ctx();
    handleKey(ev({ key: 'j', code: 'KeyJ', ctrlKey: true }), c);
    expect(c.down).toHaveBeenCalledTimes(1);
    const plain = ev({ key: 'n', code: 'KeyN' });
    expect(handleKey(plain, c)).toBe(false);
    expect(plain.defaultPrevented).toBe(false);
  });

  it('auto-repeat is ignored for every action except navigation', () => {
    const c = ctx();
    const first = ev({ key: 'Enter', code: 'Enter', shiftKey: true });
    expect(handleKey(first, c)).toBe(true);
    for (let i = 0; i < 5; i++) handleKey(ev({ key: 'Enter', code: 'Enter', shiftKey: true, repeat: true }), c);
    expect(c.toggle).toHaveBeenCalledTimes(1);
    for (const init of [
      { key: 'Enter', code: 'Enter' },
      { key: 'n', code: 'KeyN', altKey: true },
      { key: 'e', code: 'KeyE', altKey: true },
      { key: 'Backspace', code: 'Backspace', altKey: true },
      { key: 'k', code: 'KeyK', ctrlKey: true },
      { key: '?', code: 'Slash', shiftKey: true }
    ]) {
      handleKey(ev({ ...init, repeat: true }), c);
    }
    for (const id of ['copy', 'new', 'edit', 'delete', 'menu', 'help'] as const) expect(c[id], id).not.toHaveBeenCalled();
    const nav = ctx({ mac: true });
    for (const init of [
      { key: 'ArrowDown', code: 'ArrowDown' },
      { key: 'ArrowUp', code: 'ArrowUp' },
      { key: 'j', code: 'KeyJ', ctrlKey: true },
      { key: 'k', code: 'KeyK', ctrlKey: true },
      { key: 'PageDown', code: 'PageDown' },
      { key: 'PageUp', code: 'PageUp' },
      { key: 'Home', code: 'Home' },
      { key: 'End', code: 'End' }
    ]) {
      expect(handleKey(ev({ ...init, repeat: true }), nav)).toBe(true);
    }
    expect(nav.down).toHaveBeenCalledTimes(2);
    expect(nav.up).toHaveBeenCalledTimes(2);
    expect(nav.pageDown).toHaveBeenCalledTimes(1);
    expect(nav.pageUp).toHaveBeenCalledTimes(1);
    expect(nav.first).toHaveBeenCalledTimes(1);
    expect(nav.last).toHaveBeenCalledTimes(1);
  });

  it('Alt+Backspace deletes only with an empty search or the caret at the start; otherwise the browser deletes a word', () => {
    const word = ev({ key: 'Backspace', code: 'Backspace', altKey: true });
    const c = ctx({ searchEmpty: false, caretAtStart: false });
    expect(handleKey(word, c)).toBe(false);
    expect(word.defaultPrevented).toBe(false);
    expect(c.delete).not.toHaveBeenCalled();
    const c2 = ctx({ searchEmpty: false, caretAtStart: true });
    expect(handleKey(ev({ key: 'Backspace', code: 'Backspace', altKey: true }), c2)).toBe(true);
    expect(c2.delete).toHaveBeenCalledTimes(1);
    const c3 = ctx({ searchEmpty: true, caretAtStart: false });
    expect(handleKey(ev({ key: 'Backspace', code: 'Backspace', altKey: true }), c3)).toBe(true);
    expect(c3.delete).toHaveBeenCalledTimes(1);
    expect(KEYMAP.find((b) => b.id === 'delete')?.label).toBe('Delete alias — when the search box is empty or the caret is at the start');
  });

  it('macOS dead keys: Option+N reported with keyCode 229 still runs the combo by code and prevents the default', () => {
    const c = ctx();
    const e = ev({ key: 'Dead', code: 'KeyN', altKey: true });
    Object.defineProperty(e, 'keyCode', { value: 229 });
    expect(handleKey(e, c)).toBe(true);
    expect(c.new).toHaveBeenCalledTimes(1);
    expect(e.defaultPrevented).toBe(true);
    const ed = ev({ key: 'Dead', code: 'KeyE', altKey: true });
    Object.defineProperty(ed, 'keyCode', { value: 229 });
    expect(handleKey(ed, c)).toBe(true);
    expect(c.edit).toHaveBeenCalledTimes(1);
  });

  it('real IME composition (isComposing) still blocks every shortcut, and a bare 229 Enter (Safari IME commit) is ignored', () => {
    const c = ctx();
    expect(handleKey(ev({ key: 'n', code: 'KeyN', altKey: true, isComposing: true }), c)).toBe(false);
    const enter = ev({ key: 'Enter', code: 'Enter' });
    Object.defineProperty(enter, 'keyCode', { value: 229 });
    expect(handleKey(enter, c)).toBe(false);
    expect(c.new).not.toHaveBeenCalled();
    expect(c.copy).not.toHaveBeenCalled();
  });
});

describe('platform labels', () => {
  const binding = (id: KeyId) => KEYMAP.find((b) => b.id === id)!;

  it('shows Mac symbols only on a Mac, and gives screen readers the spoken name', () => {
    expect(cap('Alt', true)).toBe('⌥');
    expect(cap('Shift', true)).toBe('⇧');
    expect(cap('Ctrl', true)).toBe('⌃');
    expect(cap('Enter', true)).toBe('↵');
    expect(cap('K', true)).toBe('K');
    expect(cap('Alt', false)).toBe('Alt');
    expect(cap('Ctrl', false)).toBe('Ctrl');
    expect(spoken('⌥')).toBe('Option');
    expect(spoken('⌘')).toBe('Command');
    expect(spoken('K')).toBeUndefined();
  });

  it('shows one actions shortcut per platform, and Ctrl K as "up" only on a Mac', () => {
    expect(keysFor(binding('menu'), true)).toEqual([['⌘', 'K']]);
    expect(keysFor(binding('menu'), false)).toEqual([['Ctrl', 'K']]);
    expect(keysFor(binding('up'), true)).toEqual([['↑'], ['Ctrl', 'K']]);
    expect(keysFor(binding('up'), false)).toEqual([['↑']]);
  });
});

describe('createDeadKeyGuard', () => {
  it('gives back the armed value for a while, and not after a real keystroke', () => {
    const g = createDeadKeyGuard();
    expect(g.restore()).toBeNull();
    g.arm('Old');
    g.key(ev({ key: 'Dead' }));
    expect(g.restore()).toBe('Old');
    g.key(ev({ key: 'a' }));
    expect(g.restore()).toBeNull();
  });

  it('expires', () => {
    vi.useFakeTimers();
    try {
      const g = createDeadKeyGuard();
      g.arm('Old');
      vi.advanceTimersByTime(700);
      expect(g.restore()).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('Alt+digit jump', () => {
  it('maps Alt+1..9 to 0..8 and Alt+0 to 9, and ignores other modifiers', () => {
    expect(altDigit(ev({ key: '¡', code: 'Digit1', altKey: true }))).toBe(0);
    expect(altDigit(ev({ key: '(', code: 'Digit9', altKey: true }))).toBe(8);
    expect(altDigit(ev({ key: '°', code: 'Digit0', altKey: true }))).toBe(9);
    expect(altDigit(ev({ key: '1', code: 'Digit1' }))).toBeNull();
    expect(altDigit(ev({ key: '1', code: 'Digit1', altKey: true, metaKey: true }))).toBeNull();
  });

  it('calls jump once per press and swallows a held key', () => {
    const jump = vi.fn();
    const c = ctx({ jump });
    expect(handleKey(ev({ key: '™', code: 'Digit2', altKey: true }), c)).toBe(true);
    expect(handleKey(ev({ key: '™', code: 'Digit2', altKey: true, repeat: true }), c)).toBe(true);
    expect(jump).toHaveBeenCalledTimes(1);
    expect(jump).toHaveBeenCalledWith(1);
  });
});
