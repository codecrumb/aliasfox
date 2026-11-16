import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { tick } from 'svelte';
import CodeInput from '../src/components/CodeInput.svelte';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function boxes(): HTMLInputElement[] {
  return screen.getAllByRole('textbox') as HTMLInputElement[];
}
async function type(i: number, v: string) {
  await fireEvent.input(boxes()[i] as HTMLInputElement, { target: { value: v } });
}
function pasteInto(el: HTMLElement, text: string) {
  const ev = new Event('paste', { bubbles: true, cancelable: true });
  Object.defineProperty(ev, 'clipboardData', { value: { getData: () => text } });
  el.dispatchEvent(ev);
}
function mockMotion(reduce: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: reduce && q.includes('reduce'),
    media: q,
    addEventListener() {},
    removeEventListener() {}
  }));
}

describe('CodeInput', () => {
  it('test_code_input_groups_3_3_and_autoadvances_and_backspace_moves_back', async () => {
    const onComplete = vi.fn();
    const { container } = render(CodeInput, { onComplete });
    const group = screen.getByRole('group', { name: 'Verification code' });
    const triples = group.querySelectorAll(':scope > div');
    expect(triples).toHaveLength(2);
    expect(triples[0]?.querySelectorAll('input')).toHaveLength(3);
    expect(triples[1]?.querySelectorAll('input')).toHaveLength(3);
    const b = boxes();
    expect(b).toHaveLength(6);
    b.forEach((el, i) => {
      expect(el.getAttribute('inputmode')).toBe('numeric');
      expect(el.getAttribute('maxlength')).toBe(i === 0 ? '6' : '1');
      expect(el.getAttribute('pattern')).toBe('[0-9]');
      expect(el.getAttribute('aria-label')).toBe(`Digit ${i + 1} of 6`);
      expect(el.getAttribute('autocomplete')).toBe(i === 0 ? 'one-time-code' : 'off');
    });
    expect(container.querySelector('[style]')).toBeNull();

    b[0]?.focus();
    await type(0, '1');
    expect(b[1]).toHaveFocus();
    await type(1, '2');
    expect(b[2]).toHaveFocus();
    // Backspace on an empty box moves back and clears the previous one
    await fireEvent.keyDown(b[2] as HTMLElement, { key: 'Backspace' });
    expect(b[1]).toHaveFocus();
    expect(b[1]?.value).toBe('');
    expect(b[0]?.value).toBe('1');
    await fireEvent.keyDown(b[1] as HTMLElement, { key: 'ArrowRight' });
    expect(b[2]).toHaveFocus();
    await fireEvent.keyDown(b[2] as HTMLElement, { key: 'ArrowLeft' });
    expect(b[1]).toHaveFocus();
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('test_code_input_paste_fills_all_boxes', async () => {
    for (const text of ['123 456', '123-456', '123456']) {
      const onComplete = vi.fn();
      const { unmount } = render(CodeInput, { onComplete });
      pasteInto(boxes()[3] as HTMLElement, text);
      await tick();
      expect(boxes().map((x) => x.value).join('')).toBe('123456');
      expect(onComplete).toHaveBeenCalledExactlyOnceWith('123456');
      expect(boxes()[5]).toHaveFocus();
      unmount();
    }
  });

  it('partial paste fills available boxes and focuses the next', async () => {
    const onComplete = vi.fn();
    render(CodeInput, { onComplete });
    pasteInto(boxes()[0] as HTMLElement, '12-3');
    await tick();
    expect(boxes().map((x) => x.value).join('')).toBe('123');
    expect(boxes()[3]).toHaveFocus();
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('test_6_digit_code_only_digits_accepted', async () => {
    const onComplete = vi.fn();
    render(CodeInput, { onComplete });
    await type(0, 'a');
    expect(boxes()[0]?.value).toBe('');
    await type(0, '-');
    expect(boxes()[0]?.value).toBe('');
    for (let i = 0; i < 6; i++) await type(i, String(i + 1));
    expect(onComplete).toHaveBeenCalledExactlyOnceWith('123456');
    expect(boxes().map((x) => x.value).join('')).toBe('123456');
  });

  it('onComplete fires once; again only if edited back to complete', async () => {
    const onComplete = vi.fn();
    render(CodeInput, { onComplete });
    for (let i = 0; i < 6; i++) await type(i, '1');
    expect(onComplete).toHaveBeenCalledTimes(1);
    await type(5, ''); // erase last
    expect(onComplete).toHaveBeenCalledTimes(1);
    await type(5, '2');
    expect(onComplete).toHaveBeenCalledTimes(2);
    expect(onComplete).toHaveBeenLastCalledWith('111112');
  });

  it('test_wrong_code_shakes_turns_red_and_recovers_on_typing', async () => {
    mockMotion(false);
    vi.useFakeTimers();
    const { rerender } = render(CodeInput, { onComplete: vi.fn(), error: 0 });
    for (let i = 0; i < 6; i++) await type(i, '7');
    const group = screen.getByRole('group', { name: 'Verification code' });
    expect(group.getAttribute('aria-invalid')).toBe('false');
    await rerender({ onComplete: vi.fn(), error: 1 });
    await vi.advanceTimersByTimeAsync(0);
    expect(group).toHaveClass('shake');
    expect(group).toHaveClass('invalid');
    expect(group.getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByRole('status')).toHaveTextContent("That code didn't work.");
    // boxes cleared and first focused
    expect(boxes().every((x) => x.value === '')).toBe(true);
    expect(boxes()[0]).toHaveFocus();
    await vi.advanceTimersByTimeAsync(430);
    expect(group).not.toHaveClass('shake');
    expect(group).toHaveClass('invalid'); // red persists until the next keystroke
    await type(0, '3');
    expect(group).not.toHaveClass('invalid');
    expect(group.getAttribute('aria-invalid')).toBe('false');
    expect(screen.getByRole('status')).toHaveTextContent('');
  });

  it('test_reduced_motion_no_shake_class_but_red_state_kept', async () => {
    mockMotion(true);
    const { rerender } = render(CodeInput, { onComplete: vi.fn(), error: 0 });
    await rerender({ onComplete: vi.fn(), error: 1 });
    await tick();
    const group = screen.getByRole('group', { name: 'Verification code' });
    expect(group).not.toHaveClass('shake');
    expect(group).toHaveClass('invalid');
  });

  it('disabled disables every box', () => {
    render(CodeInput, { onComplete: vi.fn(), disabled: true });
    expect(boxes().every((x) => x.disabled)).toBe(true);
  });

  it('mounting with error > 0 shows a clean state (no stale wrong-code)', async () => {
    mockMotion(false);
    render(CodeInput, { onComplete: vi.fn(), error: 1 });
    await tick();
    const group = screen.getByRole('group', { name: 'Verification code' });
    expect(group).not.toHaveClass('shake');
    expect(group).not.toHaveClass('invalid');
    expect(group.getAttribute('aria-invalid')).toBe('false');
    expect(screen.getByRole('status')).toHaveTextContent('');
  });

  it('a later error increment after a clean mount still triggers shake and red', async () => {
    mockMotion(false);
    const { rerender } = render(CodeInput, { onComplete: vi.fn(), error: 3 });
    await rerender({ onComplete: vi.fn(), error: 4 });
    await tick();
    const group = screen.getByRole('group', { name: 'Verification code' });
    expect(group).toHaveClass('shake');
    expect(group).toHaveClass('invalid');
  });

  it('whole-code autofill into box 0 fills all boxes and fires once', async () => {
    const onComplete = vi.fn();
    render(CodeInput, { onComplete });
    await type(0, '123456');
    expect(boxes().map((x) => x.value)).toEqual(['1', '2', '3', '4', '5', '6']);
    expect(onComplete).toHaveBeenCalledExactlyOnceWith('123456');
  });

  it('box 0 always shows a single digit after typing over a filled box', async () => {
    render(CodeInput, { onComplete: vi.fn() });
    await type(0, '1');
    expect(boxes()[0]?.value).toBe('1');
    await type(0, '12'); // old digit plus the new keystroke
    expect(boxes()[0]?.value).toBe('2');
    expect(boxes()[1]?.value).toBe('');
  });

  it('a non-digit typed into a filled box is ignored and the digit stays', async () => {
    render(CodeInput, { onComplete: vi.fn() });
    await type(2, '5');
    await type(2, 'x');
    expect(boxes()[2]?.value).toBe('5');
    await type(2, ''); // a real deletion still clears
    expect(boxes()[2]?.value).toBe('');
  });
});
