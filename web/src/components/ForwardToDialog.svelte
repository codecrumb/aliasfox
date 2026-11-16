<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { aliasStore } from '../lib/aliases';
  import { describeError, isSessionError } from '../lib/errors';
  import { sessionStore } from '../lib/session';
  import { toasts } from '../lib/toasts';
  import Modal from './Modal.svelte';

  interface Props {
    current: string;
    onClose: () => void;
    onReconnect: () => void;
  }
  let { current, onClose, onReconnect }: Props = $props();

  const uid = $props.id();
  // Captured once: later list refreshes must not overwrite what is typed.
  let address = $state(untrack(() => current));
  let busy = $state(false);
  let error = $state('');
  let invalid = $state(false);
  let sessionLost = $state(false);
  let input: HTMLInputElement | undefined = $state();

  onMount(() => {
    input?.focus({ preventScroll: true });
    input?.select();
  });

  async function save(): Promise<void> {
    if (busy) return;
    const a = address.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(a)) {
      invalid = true;
      error = 'Enter an email address, like you@example.com.';
      input?.focus();
      return;
    }
    invalid = false;
    error = '';
    sessionLost = false;
    busy = true;
    try {
      await aliasStore.setForwardTo(a);
    } catch (e) {
      busy = false;
      sessionLost = isSessionError(e);
      error = describeError(e, "Couldn't change the forwarding address. Try again.");
      return;
    }
    busy = false;
    toasts.push({ kind: 'ok', text: 'Forwarding address updated' });
    onClose();
  }

  function onKey(e: KeyboardEvent): void {
    if (e.key === 'Enter' && !e.isComposing) {
      e.preventDefault();
      void save();
    }
  }
  function onSubmit(e: SubmitEvent): void {
    e.preventDefault();
    void save();
  }
</script>

<Modal labelledby="{uid}-title" onClose={onClose} persistent size="sm">
  <form class="dialog-form" onsubmit={onSubmit} novalidate>
    <div>
      <h2 class="dialog-title" id="{uid}-title">Change forward-to address</h2>
      <p class="dialog-text">Mail sent to every alias is forwarded here. Apple only accepts addresses on your account.</p>
    </div>
    <div>
      <label for="{uid}-addr">Forward to</label>
      <input
        id="{uid}-addr"
        bind:this={input}
        bind:value={address}
        type="email"
        inputmode="email"
        autocomplete="email"
        autocapitalize="off"
        spellcheck="false"
        aria-invalid={invalid}
        aria-describedby="{uid}-error"
        onkeydown={onKey}
      />
    </div>
    <div class="error-row">
      <p class="field-error" id="{uid}-error" role="alert">{error}</p>
      {#if sessionLost && $sessionStore.state !== 'ready'}
        <button type="button" class="reconnect" onclick={onReconnect}>Reconnect</button>
      {/if}
    </div>
    <div class="dialog-actions">
      <button type="button" class="ghost" onclick={onClose}>Cancel</button>
      <button type="submit" class="primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
    </div>
  </form>
</Modal>

<style>
  .dialog-actions {
    margin-top: 0;
  }
</style>
