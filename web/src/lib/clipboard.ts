// Clipboard access via the async Clipboard API only. Any failure (denied permission, insecure
// context, missing API) reports 'fallback' so the UI can show a selectable field instead.
//
// `until` lets a caller start the copy inside a key press and finish it later: browsers (Safari in
// particular) only allow a clipboard write during the gesture, so the write is started at once with
// content that resolves when `until` does. If `until` rejects, nothing is copied.
export async function copyText(text: string, until?: Promise<unknown>): Promise<'copied' | 'fallback'> {
  try {
    const clip = navigator.clipboard;
    if (until && typeof ClipboardItem !== 'undefined' && typeof clip.write === 'function') {
      const blob = until.then(() => new Blob([text], { type: 'text/plain' }));
      await clip.write([new ClipboardItem({ 'text/plain': blob })]);
      return 'copied';
    }
    if (until) await until;
    const writeText = clip.writeText.bind(clip);
    await writeText(text);
    return 'copied';
  } catch {
    return 'fallback';
  }
}
