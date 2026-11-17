// Clipboard access via the async Clipboard API only. Any failure (denied permission, insecure
// context, missing API) reports 'fallback' so the UI can show a selectable field instead.
export async function copyText(text: string): Promise<'copied' | 'fallback'> {
  try {
    const writeText = navigator.clipboard.writeText.bind(navigator.clipboard);
    await writeText(text);
    return 'copied';
  } catch {
    return 'fallback';
  }
}
