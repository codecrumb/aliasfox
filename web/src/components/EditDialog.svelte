<script lang="ts">
  import { onMount, tick, untrack } from 'svelte';
  import { aliasStore } from '../lib/aliases';
  import { describeError, isSessionError } from '../lib/errors';
  import { createDeadKeyGuard, isCompositionInput, isMac, isSubmitChord } from '../lib/keys';
  import { sessionStore } from '../lib/session';
  import type { Alias } from '../lib/types';
  import Modal from './Modal.svelte';

  interface Props {
    alias: Alias;
    onClose: () => void;
    onReconnect: () => void;
  }
  let { alias, onClose, onReconnect }: Props = $props();

  const uid = $props.id();
  // Snapshot of the alias at open time: later list refreshes must not overwrite what is typed.
  const initial = untrack(() => alias);
  const id = initial.anonymous_id;
  const hme = initial.hme;
  let label = $state(initial.label ?? '');
  let note = $state(initial.note ?? '');
  let busy = $state(false);
  let error = $state('');
  let invalid = $state(false);
  let sessionLost = $state(false);
  let labelInput: HTMLInputElement | undefined = $state();
  let enabling = $state(false);
  // Apple doesn't take label/note changes for a turned-off address, so editing waits until it is on.
  // Read live: turning it on here (or Shift+Enter in the palette) flips this without reopening.
  const isOn = $derived($aliasStore.list.find((a) => a.anonymous_id === id)?.is_active ?? initial.is_active);
  const mac = isMac();
  // Option+E (the shortcut that opens this dialog) is a dead key on a Mac and would otherwise
  // replace the selected label with an accent.
  const deadKey = createDeadKeyGuard();
  function undoDeadKey(): void {
    const v = deadKey.restore();
    if (v === null || label === v) return;
    label = v;
    if (labelInput) {
      labelInput.value = v;
      labelInput.select();
    }
  }

  // After a reconnect the typed values are still here; say so instead of the stale error.
  $effect(() => {
    if ($sessionStore.state === 'ready' && sessionLost) {
      sessionLost = false;
      error = 'Reconnected. Save again to finish.';
    }
  });

  onMount(() => {
    labelInput?.focus({ preventScroll: true });
    labelInput?.select();
    deadKey.arm(label);
  });

  async function turnOn(): Promise<void> {
    if (enabling) return;
    enabling = true;
    error = '';
    sessionLost = false;
    try {
      await aliasStore.setActive(id, true);
      await tick();
      labelInput?.focus({ preventScroll: true });
      labelInput?.select();
    } catch (e) {
      sessionLost = isSessionError(e);
      error = describeError(e, "Couldn't turn it on. Try again.");
    } finally {
      enabling = false;
    }
  }

  async function save(): Promise<void> {
    if (busy || !isOn) return;
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
    try {
      await aliasStore.update(id, { label: l, note });
    } catch (e) {
      busy = false;
      sessionLost = isSessionError(e);
      error = describeError(e, "Couldn't save. Try again.");
      return;
    }
    busy = false;
    onClose();
  }

  function onLabelKey(e: KeyboardEvent): void {
    deadKey.key(e);
    if (e.key === 'Enter' && !e.isComposing && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      if (e.repeat) return; // a held Enter submits once
      void save();
    }
  }
  // Cmd/Ctrl+Enter saves from anywhere in the dialog: the fields, the buttons, the dialog itself.
  function onChord(e: KeyboardEvent): void {
    if (!isSubmitChord(e)) return;
    e.preventDefault();
    if (e.repeat) return; // a held chord submits once
    void save();
  }
  function onSubmit(e: SubmitEvent): void {
    e.preventDefault();
    void save();
  }
</script>

<svelte:window onkeydown={onChord} />

<Modal labelledby="{uid}-title" onClose={onClose} persistent>
  <form class="dialog-form" onsubmit={onSubmit} novalidate>
    <div>
      <h2 class="dialog-title" id="{uid}-title">Edit alias</h2>
      <p class="dialog-text mono addr" dir="auto">{hme}</p>
    </div>
    {#if !isOn}
      <div class="off-note">
        <p>This address is inactive, so its label and note can't be changed. Reactivate it to edit, then deactivate it again with Shift Enter.</p>
        <button type="button" onclick={() => void turnOn()} disabled={enabling}>{enabling ? 'Reactivating…' : 'Reactivate to edit'}</button>
      </div>
    {/if}
    <div>
      <label for="{uid}-label">Label</label>
      <input
        id="{uid}-label"
        bind:this={labelInput}
        bind:value={label}
        maxlength="200"
        disabled={!isOn}
        autocomplete="off"
        spellcheck="false"
        aria-invalid={invalid}
        aria-describedby="{uid}-error"
        dir="auto"
        oninput={(e) => {
          invalid = false;
          if (isCompositionInput(e)) undoDeadKey();
        }}
        oncompositionend={undoDeadKey}
        onkeydown={onLabelKey}
      />
    </div>
    <div>
      <label for="{uid}-note">Note</label>
      <textarea id="{uid}-note" bind:value={note} rows="3" maxlength="2000" dir="auto" disabled={!isOn}></textarea>
      <p class="field-hint">{mac ? '⌘' : 'Ctrl'} Enter saves from anywhere in this dialog.</p>
    </div>
    <div class="error-row">
      <p class="field-error" id="{uid}-error" role="alert">{error}</p>
      {#if sessionLost && $sessionStore.state !== 'ready'}
        <button type="button" class="reconnect" onclick={onReconnect}>Reconnect</button>
      {/if}
    </div>
    <div class="dialog-actions">
      <button type="button" class="ghost" onclick={onClose}>Cancel</button>
      <button type="submit" class="primary" disabled={busy || !isOn}>{busy ? 'Saving…' : 'Save'}</button>
    </div>
  </form>
</Modal>

<style>
  .dialog-actions {
    margin-top: 0;
  }
  .addr {
    font-size: 0.875rem;
  }
  .off-note {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-2);
    padding: var(--space-3);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
  }
  .off-note p {
    margin: 0;
    color: var(--muted);
    font-size: 0.875rem;
  }
</style>
