<script lang="ts">
  import { onMount } from 'svelte';
  import { isMac } from '../lib/keys';
  import Modal from './Modal.svelte';

  interface Props {
    text: string;
    onClose: () => void;
  }
  let { text, onClose }: Props = $props();

  const uid = $props.id();
  const mac = isMac();
  let field: HTMLInputElement | undefined = $state();

  function select(): void {
    field?.setSelectionRange(0, field.value.length);
  }

  onMount(() => {
    field?.focus({ preventScroll: true });
    select();
  });
</script>

<Modal labelledby="{uid}-title" describedby="{uid}-msg" onClose={onClose} size="sm">
  <h2 class="dialog-title" id="{uid}-title">Copy address</h2>
  <p class="dialog-text" id="{uid}-msg">Your browser blocked copying. Press {mac ? '⌘' : 'Ctrl'}+C to copy.</p>
  <input
    class="mono field"
    bind:this={field}
    value={text}
    readonly
    aria-labelledby="{uid}-title"
    spellcheck="false"
    onfocus={select}
    onclick={select}
  />
  <div class="dialog-actions">
    <button type="button" onclick={onClose}>Close</button>
  </div>
</Modal>

<style>
  .field {
    margin-top: var(--space-4);
    font-size: 0.9375rem;
  }
</style>
