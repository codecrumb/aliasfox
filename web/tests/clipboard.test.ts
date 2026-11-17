import { afterEach, describe, expect, it, vi } from 'vitest';
import { copyText } from '../src/lib/clipboard';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('copyText', () => {
  it('returns copied when writeText resolves', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    expect(await copyText('a@icloud.com')).toBe('copied');
    expect(writeText).toHaveBeenCalledWith('a@icloud.com');
  });

  it('test_copy_falls_back_when_clipboard_denied', async () => {
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockRejectedValue(new DOMException('no', 'NotAllowedError')) }
    });
    expect(await copyText('x')).toBe('fallback');
  });

  it('falls back when the clipboard API is absent or throws synchronously', async () => {
    vi.stubGlobal('navigator', {});
    expect(await copyText('x')).toBe('fallback');
    vi.stubGlobal('navigator', undefined);
    expect(await copyText('x')).toBe('fallback');
    vi.stubGlobal('navigator', {
      clipboard: {
        writeText: () => {
          throw new Error('sync');
        }
      }
    });
    expect(await copyText('x')).toBe('fallback');
  });
});

describe('copyText with a pending result', () => {
  class FakeItem {
    constructor(public items: Record<string, Promise<Blob>>) {}
  }

  it('starts the write right away and resolves its content once the promise does', async () => {
    let finish: () => void = () => {};
    const until = new Promise<void>((r) => (finish = r));
    const write = vi.fn(async (items: FakeItem[]) => {
      const blob = await items[0]!.items['text/plain']!;
      expect(await blob.text()).toBe('a@icloud.com');
    });
    vi.stubGlobal('navigator', { clipboard: { write, writeText: vi.fn() } });
    vi.stubGlobal('ClipboardItem', FakeItem);
    const done = copyText('a@icloud.com', until);
    expect(write).toHaveBeenCalledTimes(1); // inside the same tick: still within the key press
    finish();
    expect(await done).toBe('copied');
  });

  it('falls back, and leaves the clipboard alone, when the promise rejects', async () => {
    const write = vi.fn(async (items: FakeItem[]) => {
      await items[0]!.items['text/plain'];
    });
    const writeText = vi.fn();
    vi.stubGlobal('navigator', { clipboard: { write, writeText } });
    vi.stubGlobal('ClipboardItem', FakeItem);
    expect(await copyText('a@icloud.com', Promise.reject(new Error('reserve failed')))).toBe('fallback');
    expect(writeText).not.toHaveBeenCalled();
  });

  it('without ClipboardItem it waits for the promise, then uses writeText', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.stubGlobal('ClipboardItem', undefined);
    let finish: () => void = () => {};
    const done = copyText('a@icloud.com', new Promise<void>((r) => (finish = r)));
    await Promise.resolve();
    expect(writeText).not.toHaveBeenCalled();
    finish();
    expect(await done).toBe('copied');
    expect(writeText).toHaveBeenCalledWith('a@icloud.com');
  });

  it('does not copy at all when the promise rejects and only writeText exists', async () => {
    const writeText = vi.fn();
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.stubGlobal('ClipboardItem', undefined);
    expect(await copyText('x', Promise.reject(new Error('no')))).toBe('fallback');
    expect(writeText).not.toHaveBeenCalled();
  });
});
