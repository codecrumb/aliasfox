<script lang="ts">
  import { onMount } from 'svelte';
  import { describeError } from '../lib/errors';
  import { CONFIRM_GUARD_MS } from '../lib/keys';
  import Modal from './Modal.svelte';

  interface Props {
    title: string;
    text: string;
    /** Shown on its own line in monospace (e.g. the address being deleted). Plain text. */
    subject?: string;
    confirmLabel: string;
    busyLabel?: string;
    danger?: boolean;
    focus?: 'confirm' | 'cancel';
    onConfirm: () => void | Promise<void>;
    onCancel: () => void;
  }
  let {
    title,
    text,
    subject,
    confirmLabel,
    busyLabel,
    danger = false,
    focus = 'confirm',
    onConfirm,
    onCancel
  }: Props = $props();

  const uid = $props.id();
  let busy = $state(false);
  let error = $state('');
  let body: HTMLElement | undefined = $state();
  let confirmBtn: HTMLButtonElement | undefined = $state();
  let cancelBtn: HTMLButtonElement | undefined = $state();

  // See CONFIRM_GUARD_MS: the key press that opened this dialog must not also confirm it.
  const openedAt = performance.now();
  const guarded = (): boolean => performance.now() - openedAt < CONFIRM_GUARD_MS;

  async function confirm(): Promise<void> {
    if (busy) return;
    error = '';
    const r = onConfirm();
    if (!(r instanceof Promise)) return;
    busy = true;
    try {
      await r;
    } catch (e) {
      error = describeError(e);
    } finally {
      busy = false;
    }
  }

  onMount(() => {
    (focus === 'cancel' ? cancelBtn : confirmBtn)?.focus({ preventScroll: true });
    const el = body;
    if (!el) return;
    const key = (e: KeyboardEvent): void => {
      const enter = e.key === 'Enter';
      const space = e.key === ' ' || e.code === 'Space';
      if ((!enter && !space) || e.isComposing) return;
      if (e.repeat || guarded()) {
        // Swallow held keys and the opening key press, including the native button activation.
        e.preventDefault();
        return;
      }
      if (!enter || e.shiftKey || e.altKey) return; // Space activates natively (click, detail 0)
      e.preventDefault();
      if (e.target === cancelBtn) onCancel();
      else void confirm();
    };
    el.addEventListener('keydown', key);
    return () => el.removeEventListener('keydown', key);
  });
</script>

<Modal labelledby="{uid}-title" describedby="{uid}-text" onClose={() => !busy && onCancel()} size="sm">
  <div bind:this={body}>
    <h2 class="dialog-title" id="{uid}-title">{title}</h2>
    <p class="dialog-text" id="{uid}-text">{text}</p>
    {#if subject}
      <p class="subject mono" dir="auto">{subject}</p>
    {/if}
    <p class="field-error" role="alert">{error}</p>
    <div class="dialog-actions">
      <button type="button" class="ghost" bind:this={cancelBtn} disabled={busy} onclick={onCancel}>Cancel</button>
      <button
        type="button"
        class={danger ? 'danger' : 'primary'}
        bind:this={confirmBtn}
        disabled={busy}
        onclick={(e) => {
          // A keyboard-generated click (detail 0) inside the guard window is the opening key press.
          if (e.detail === 0 && guarded()) return;
          void confirm();
        }}
      >
        {busy && busyLabel ? busyLabel : confirmLabel}
      </button>
    </div>
  </div>
</Modal>

<style>
  .subject {
    margin-top: var(--space-3);
    padding: var(--space-3);
    border-radius: var(--radius);
    background: var(--surface);
    font-size: 0.875rem;
    overflow-wrap: anywhere;
  }
</style>
