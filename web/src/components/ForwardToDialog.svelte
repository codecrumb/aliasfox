<script lang="ts">
  import { onMount, tick, untrack } from 'svelte';
  import { api } from '../lib/api';
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
  // Apple's own addresses; null until loaded, and empty/failed means plain text box.
  let options: string[] = $state([]);
  let other = $state(true);
  const OTHER = '\u0000other';

  onMount(() => {
    input?.focus({ preventScroll: true });
    input?.select();
    void loadOptions();
  });

  async function loadOptions(): Promise<void> {
    try {
      const res = await api.forwardOptions();
      if (!res.options.length) return;
      options = res.options;
      other = !res.options.includes(address);
    } catch {
      // Keep the text box: the list is a convenience, saving still works without it.
    }
  }
  function onPick(e: Event): void {
    const v = (e.currentTarget as HTMLSelectElement).value;
    other = v === OTHER;
    if (!other) address = v;
    else void tick().then(() => input?.focus());
  }

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
      if (e.repeat) return; // a held Enter submits once
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
      <label for={options.length ? `${uid}-pick` : `${uid}-addr`}>Forward to</label>
      {#if options.length}
        <select id="{uid}-pick" value={other ? OTHER : address} onchange={onPick}>
          {#each options as o (o)}<option value={o}>{o}</option>{/each}
          <option value={OTHER}>Other…</option>
        </select>
      {/if}
      <input
        hidden={!other}
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
  select {
    width: 100%;
    min-height: 44px;
    padding: 0 var(--space-3);
    border: 1px solid var(--field-border);
    border-radius: var(--radius);
    background: var(--surface);
    font: inherit;
    color: inherit;
  }
  select + input:not([hidden]) {
    margin-top: var(--space-2);
  }
</style>
