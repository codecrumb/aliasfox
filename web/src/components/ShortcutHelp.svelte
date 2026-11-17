<script lang="ts">
  import { KEYMAP, cap, isMac, keysFor } from '../lib/keys';
  import Modal from './Modal.svelte';

  interface Props {
    onClose: () => void;
  }
  let { onClose }: Props = $props();
  const uid = $props.id();
  const mac = isMac();
  const notes = mac ? KEYMAP.flatMap((b) => (b.note ? [b.note] : [])) : [];
</script>

<Modal labelledby="{uid}-title" onClose={onClose} size="lg">
  <h2 class="dialog-title" id="{uid}-title">Keyboard shortcuts</h2>
  <p class="dialog-text">The search field always has focus, so you can type to filter at any time.</p>
  <dl class="map">
    {#each KEYMAP as b (b.id)}
      <div class="item">
        <dt>{b.label}</dt>
        <dd>
          {#each keysFor(b, mac) as alt, i (i)}
            {#if i > 0}<span class="or">or</span>{/if}
            <span class="combo">
              {#each alt as name, j (j)}<kbd>{cap(name, mac)}</kbd>{/each}
            </span>
          {/each}
        </dd>
      </div>
    {/each}
    <div class="item">
      <dt>Jump to a visible alias (hold {cap('Alt', mac)} to see the numbers)</dt>
      <dd><span class="combo"><kbd>{cap('Alt', mac)}</kbd><kbd>1</kbd></span><span class="or">…</span><span class="combo"><kbd>{cap('Alt', mac)}</kbd><kbd>0</kbd></span></dd>
    </div>
  </dl>
  {#each notes as n (n)}
    <p class="field-hint">{n}</p>
  {/each}
  <div class="dialog-actions">
    <button type="button" onclick={onClose}>Close</button>
  </div>
</Modal>

<style>
  .map {
    margin: var(--space-4) 0 0;
    display: grid;
    gap: 0;
  }
  .item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-4);
    min-height: 2.5rem;
    border-bottom: 1px solid var(--border);
  }
  .item:last-child {
    border-bottom: 0;
  }
  dt {
    color: var(--text);
    font-size: 0.9375rem;
  }
  dd {
    margin: 0;
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex: none;
  }
  .combo {
    display: inline-flex;
    gap: 3px;
  }
  .or {
    color: var(--muted);
    font-size: 0.8125rem;
  }
</style>
