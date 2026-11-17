import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import StepTransition from '../src/components/StepTransition.svelte';

const kids = createRawSnippet(() => ({
  render: () => '<div><p id="a">a</p><p id="b">b</p><p id="c">c</p></div>'
}));
const flat = createRawSnippet(() => ({
  render: () => '<span id="a">a</span>'
}));

function mockMotion(reduced: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({ matches: reduced, addEventListener() {}, removeEventListener() {} }))
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('StepTransition', () => {
  it('test_transition_applies_stagger_classes_in_dom_order_and_none_when_reduced_motion', async () => {
    vi.useFakeTimers();
    mockMotion(false);
    const { container, rerender } = render(StepTransition, {
      props: { step: 0, direction: 'forward', children: kids }
    });
    const wrap = container.querySelector('.step') as HTMLElement;
    // --i follows DOM order of the screen's children (single wrapper child is descended into)
    const items = ['a', 'b', 'c'].map((id) => container.querySelector(`#${id}`) as HTMLElement);
    expect(items.map((e) => e.style.getPropertyValue('--i'))).toEqual(['0', '1', '2']);
    expect(wrap.classList.contains('enter-forward')).toBe(false); // initial mount: no motion

    await rerender({ step: 1, direction: 'forward', children: kids });
    expect(wrap.classList.contains('enter-forward')).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(wrap.classList.contains('enter-forward')).toBe(false);

    await rerender({ step: 0, direction: 'back', children: kids });
    expect(wrap.classList.contains('enter-back')).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(wrap.className).not.toMatch(/enter-/);

    // reduced motion: no classes at all
    mockMotion(true);
    await rerender({ step: 2, direction: 'forward', children: kids });
    expect(wrap.className).not.toMatch(/enter-/);
  });

  it('indexes direct children when the wrapper has several', () => {
    mockMotion(false);
    const { container } = render(StepTransition, {
      props: { step: 0, direction: 'forward', children: flat }
    });
    expect((container.querySelector('#a') as HTMLElement).style.getPropertyValue('--i')).toBe('0');
  });
});
