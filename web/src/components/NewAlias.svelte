<script lang="ts">
  import { onDestroy, onMount, tick } from 'svelte';
  import { aliasStore } from '../lib/aliases';
  import { describeError, describeGenerateError, isSessionError } from '../lib/errors';
  import { sessionStore } from '../lib/session';
  import { toasts } from '../lib/toasts';
  import type { Alias } from '../lib/types';
  import Modal from './Modal.svelte';

  interface Props {
    onClose: () => void;
    onCreated: (a: Alias) => void;
    onReconnect: () => void;
  }
  let { onClose, onCreated, onReconnect }: Props = $props();

  const uid = $props.id();
  let hme = $state('');
  let label = $state('');
  let note = $state('');
  let busy = $state(false);
  let error = $state('');
  let invalid = $state(false);
  let sessionLost = $state(false);
  let labelInput: HTMLInputElement | undefined = $state();
  let alive = true;

  $effect(() => {
    if ($sessionStore.state === 'ready' && sessionLost) {
      sessionLost = false;
      error = 'Reconnected. Press Enter to create it.';
    }
  });

  onMount(() => {
    aliasStore.generate().then(
      async (h) => {
        if (!alive) return;
        hme = h;
        await tick();
        labelInput?.focus({ preventScroll: true });
      },
      (e: unknown) => {
        if (!alive) return;
        toasts.push({ kind: 'error', text: describeGenerateError(e) });
        onClose();
      }
    );
  });
  onDestroy(() => {
    alive = false;
  });

  async function create(): Promise<void> {
    if (busy || hme === '') return;
    const l = label.trim();
    if (l === '') {
      invalid = true;
      error = 'Add a label so you can find this address later.';
      labelInput?.focus();
      return;
    }
    invalid = false;
    error = '';
    sessionLost = false;
    busy = true;
    let created: Alias;
    try {
      created = await aliasStore.reserve({ hme, label: l, note });
    } catch (e) {
      busy = false;
      if (!alive) return;
      sessionLost = isSessionError(e);
      error = describeError(e, "Couldn't create the address. Try again.");
      return;
    }
    busy = false;
    if (alive) onCreated(created);
  }

  function onLabelKey(e: KeyboardEvent): void {
    if (e.key === 'Enter' && !e.isComposing && !e.shiftKey) {
      e.preventDefault();
      void create();
    }
  }
  function onNoteKey(e: KeyboardEvent): void {
    if (e.key === 'Enter' && !e.isComposing && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void create();
    }
  }
  function onSubmit(e: SubmitEvent): void {
    e.preventDefault();
    void create();
  }
</script>

<Modal labelledby="{uid}-title" onClose={onClose} persistent>
  <form class="dialog-form" onsubmit={onSubmit} novalidate>
    <h2 class="dialog-title" id="{uid}-title">New alias</h2>

    {#if hme === ''}
      <div class="ticket loading" role="status">
        <span class="shimmer" aria-hidden="true"></span>
        <span class="muted">Getting a new address…</span>
      </div>
    {:else}
      <div class="ticket">
        <span class="visually-hidden">New address:</span>
        <span class="addr mono">{hme}</span>
      </div>

      <div>
        <label for="{uid}-label">Label</label>
        <input
          id="{uid}-label"
          bind:this={labelInput}
          bind:value={label}
          maxlength="200"
          autocomplete="off"
          spellcheck="false"
          dir="auto"
          aria-invalid={invalid}
          aria-describedby="{uid}-hint {uid}-error"
          oninput={() => (invalid = false)}
          onkeydown={onLabelKey}
        />
        <p class="field-hint" id="{uid}-hint">Where will you use this? e.g. a site name</p>
      </div>
      <div>
        <label for="{uid}-note">Note <span class="optional">(optional)</span></label>
        <textarea id="{uid}-note" bind:value={note} rows="2" maxlength="2000" dir="auto" onkeydown={onNoteKey}></textarea>
      </div>
      <div class="error-row">
        <p class="field-error" id="{uid}-error" role="alert">{error}</p>
        {#if sessionLost && $sessionStore.state !== 'ready'}
          <button type="button" class="reconnect" onclick={onReconnect}>Reconnect</button>
        {/if}
      </div>
    {/if}

    <div class="dialog-actions">
      <button type="button" class="ghost" onclick={onClose}>Cancel</button>
      <button type="submit" class="primary" disabled={busy || hme === ''}>{busy ? 'Creating…' : 'Create'}</button>
    </div>
  </form>
</Modal>

<style>
  .dialog-actions {
    margin-top: 0;
  }
  .ticket {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    min-height: 3.5rem;
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius);
    background: var(--surface);
    box-shadow: inset 3px 0 0 var(--accent);
  }
  .addr {
    font-size: 1.0625rem;
    color: var(--text);
    overflow-wrap: anywhere;
    animation: ticket-in var(--dur) var(--ease) both;
  }
  .shimmer {
    width: 1rem;
    height: 1rem;
    border-radius: 50%;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    animation: spin 0.8s linear infinite;
  }
  .optional {
    color: var(--muted);
    opacity: 0.8;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  @keyframes ticket-in {
    from {
      opacity: 0;
      letter-spacing: 0.04em;
    }
    to {
      opacity: 1;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .addr,
    .shimmer {
      animation: none;
    }
  }
</style>
