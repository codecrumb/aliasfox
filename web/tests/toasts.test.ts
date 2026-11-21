import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { get } from 'svelte/store';
import { toasts } from '../src/lib/toasts';
import Toasts from '../src/components/Toasts.svelte';

beforeEach(() => {
  vi.useFakeTimers();
  for (const t of get(toasts)) toasts.dismiss(t.id);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('toasts', () => {
  it('test_toasts_render_text_only', async () => {
    render(Toasts);
    toasts.push({ kind: 'ok', text: '<b>x</b>' });
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.getByText('<b>x</b>')).toBeInTheDocument();
    expect(document.querySelector('b')).toBeNull();
  });

  it('auto-dismisses after 4s (ok) and 7s (error)', () => {
    toasts.push({ kind: 'ok', text: 'a' });
    toasts.push({ kind: 'error', text: 'b' });
    expect(get(toasts)).toHaveLength(2);
    vi.advanceTimersByTime(3999);
    expect(get(toasts)).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(get(toasts).map((t) => t.text)).toEqual(['b']);
    vi.advanceTimersByTime(3000);
    expect(get(toasts)).toHaveLength(0);
  });

  it('caps at 4, dropping the oldest', () => {
    for (let i = 0; i < 6; i++) toasts.push({ kind: 'ok', text: `t${i}` });
    expect(get(toasts).map((t) => t.text)).toEqual(['t2', 't3', 't4', 't5']);
  });

  it('push returns an id that dismiss removes', () => {
    const id = toasts.push({ kind: 'ok', text: 'x' });
    toasts.dismiss(id);
    expect(get(toasts)).toHaveLength(0);
  });

  it('renders live region, alert role for errors, labelled dismiss', async () => {
    const { container } = render(Toasts);
    toasts.push({ kind: 'error', text: 'boom' });
    await vi.advanceTimersByTimeAsync(0);
    expect(container.querySelector('[aria-live="polite"]')).not.toBeNull();
    expect(screen.getByRole('alert')).toHaveTextContent('boom');
    await fireEvent.click(screen.getByRole('button', { name: 'Dismiss notification' }));
    expect(get(toasts)).toHaveLength(0);
  });
});

describe('toast action', () => {
  it('runs the action once, dismisses the toast, and lasts 8s', async () => {
    const run = vi.fn();
    render(Toasts);
    toasts.push({ kind: 'ok', text: 'Deactivated x', action: { label: 'Undo', run } });
    await vi.advanceTimersByTimeAsync(7999);
    expect(get(toasts)).toHaveLength(1);
    await fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(run).toHaveBeenCalledTimes(1);
    expect(get(toasts)).toHaveLength(0);
  });
});
