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
