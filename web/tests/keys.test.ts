import { describe, expect, it, vi } from 'vitest';
import { KEYMAP, handleKey, type KeyCtx, type KeyId } from '../src/lib/keys';

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
        const c = ctx({ searchEmpty: b.when !== 'searchNotEmpty', mac: combo.mac === true });
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
      ['clear', 'copy', 'delete', 'down', 'edit', 'first', 'help', 'last', 'menu', 'new', 'pageDown', 'pageUp', 'toggle', 'up'].sort()
    );
  });

  it('Enter copies, Shift+Enter toggles', () => {
    const c = ctx();
    handleKey(ev({ key: 'Enter', code: 'Enter' }), c);
    handleKey(ev({ key: 'Enter', code: 'Enter', shiftKey: true }), c);
    expect(c.copy).toHaveBeenCalledTimes(1);
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
});
