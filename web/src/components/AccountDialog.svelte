<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { sessionStore } from '../lib/session';
  import Modal from './Modal.svelte';

  interface Props {
    onClose: () => void;
    /** Opens the existing "Disconnect Apple account?" confirm. */
    onDisconnect: () => void;
  }
  let { onClose, onDisconnect }: Props = $props();

  const uid = $props.id();
  const WORD = 'delete';
  let view = $state<'main' | 'delete'>('main');
  let typed = $state('');
  let busy = $state(false);
  let error = $state('');
  let firstBtn: HTMLButtonElement | undefined = $state();
  let wordInput: HTMLInputElement | undefined = $state();

  const armed = $derived(typed.trim().toLowerCase() === WORD);

  onMount(() => firstBtn?.focus({ preventScroll: true }));

  async function showDelete(): Promise<void> {
    view = 'delete';
    typed = '';
    error = '';
    await tick();
    wordInput?.focus();
  }

  async function showMain(): Promise<void> {
    if (busy) return;
    view = 'main';
    await tick();
    firstBtn?.focus();
  }

  async function confirmDelete(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    if (!armed || busy) return;
    busy = true;
    error = '';
    try {
      // On success the session store returns to the signed-out view, which unmounts this dialog.
      await sessionStore.deleteAccount();
    } catch {
      error = "Couldn't delete your account. Try again.";
      busy = false;
    }
  }
</script>

<Modal labelledby="{uid}-title" onClose={() => !busy && onClose()} size="sm">
  {#if view === 'main'}
    <h2 class="dialog-title" id="{uid}-title">Account</h2>
    <div class="rows">
      <div class="row">
        <p class="dialog-text">Forget your stored Apple session. Your AliasFox account stays.</p>
        <button type="button" bind:this={firstBtn} onclick={onDisconnect}>Disconnect Apple</button>
      </div>
      <div class="row">
        <p class="dialog-text">Delete your Apple session, data key and AliasFox account, and log out.</p>
        <button type="button" class="danger" onclick={() => void showDelete()}>Delete account</button>
      </div>
    </div>
    <div class="dialog-actions">
      <button type="button" class="ghost" onclick={onClose}>Close</button>
    </div>
  {:else}
    <h2 class="dialog-title" id="{uid}-title">Delete your account?</h2>
    <form class="dialog-form" onsubmit={confirmDelete}>
      <p class="dialog-text">
        AliasFox deletes your stored Apple session, your data key and your account, then logs you out.
        Your Hide My Email addresses stay in your Apple account. This can't be undone.
      </p>
      <div>
        <label for="{uid}-word">Type {WORD} to confirm</label>
        <input
          id="{uid}-word"
          bind:this={wordInput}
          bind:value={typed}
          name="confirm"
          autocomplete="off"
          autocapitalize="off"
          spellcheck="false"
          aria-describedby="{uid}-error"
          readonly={busy}
        />
      </div>
      <p id="{uid}-error" class="field-error" role="alert">{error}</p>
      <div class="dialog-actions">
        <button type="button" class="ghost" disabled={busy} onclick={() => void showMain()}>Cancel</button>
        <button type="submit" class="danger" disabled={!armed || busy}>
          {busy ? 'Deleting…' : 'Delete account'}
        </button>
      </div>
    </form>
  {/if}
</Modal>

<style>
  .rows {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    margin-top: var(--space-4);
  }
  .row {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-3);
    padding-top: var(--space-4);
    border-top: 1px solid var(--border);
  }
</style>
