<script lang="ts">
  import { statsOf } from '../lib/transfer';
  import type { Alias } from '../lib/types';
  import Modal from './Modal.svelte';

  interface Props {
    list: Alias[];
    onClose: () => void;
  }
  let { list, onClose }: Props = $props();
  const uid = $props.id();
  const s = $derived(statsOf(list));
</script>

<Modal labelledby="{uid}-title" onClose={onClose} size="sm">
  <h2 class="dialog-title" id="{uid}-title">Your aliases</h2>
  <dl class="stats">
    <div><dt>Total</dt><dd>{s.total.toLocaleString()}</dd></div>
    <div><dt>Active</dt><dd>{s.active.toLocaleString()}</dd></div>
    <div><dt>Inactive</dt><dd>{s.inactive.toLocaleString()}</dd></div>
    <div><dt>Created in the last 30 days</dt><dd>{s.lastMonth.toLocaleString()}</dd></div>
  </dl>
  {#if s.domains.length > 0}
    <h3 class="sites-title">Most used sites</h3>
    <ol class="sites">
      {#each s.domains as [domain, n] (domain)}
        <li><span>{domain}</span><span>{n.toLocaleString()}</span></li>
      {/each}
    </ol>
  {/if}
  <div class="dialog-actions">
    <button type="button" onclick={onClose}>Close</button>
  </div>
</Modal>

<style>
  .stats {
    margin: var(--space-4) 0 0;
    display: grid;
  }
  .stats div,
  .sites li {
    display: flex;
    justify-content: space-between;
    gap: var(--space-4);
    min-height: 2.25rem;
    align-items: center;
    border-bottom: 1px solid var(--border);
  }
  dd {
    margin: 0;
    font-variant-numeric: tabular-nums;
  }
  .sites-title {
    margin-top: var(--space-5);
    font-size: 0.9375rem;
  }
  .sites {
    margin: var(--space-2) 0 0;
    padding: 0;
    list-style: none;
    font-size: 0.9375rem;
    font-variant-numeric: tabular-nums;
  }
  .sites span:first-child {
    overflow-wrap: anywhere;
  }
</style>
