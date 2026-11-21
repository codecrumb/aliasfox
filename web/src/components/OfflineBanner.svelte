<script lang="ts">
  import { aliasStore } from '../lib/aliases';
  import { online, pending } from '../lib/queue';
</script>

{#if !$online || $pending > 0}
  <div class="offline" role="status">
    <span>
      {#if !$online}Offline{:else}Back online{/if}{#if $pending > 0}
        · {$pending} {$pending === 1 ? 'change' : 'changes'} waiting to sync{/if}
    </span>
    {#if $online && $pending > 0}
      <button type="button" onclick={() => void aliasStore.refresh()}>Sync now</button>
    {/if}
  </div>
{/if}

<style>
  .offline {
    position: fixed;
    z-index: var(--z-toast);
    top: max(var(--gutter), env(safe-area-inset-top));
    right: max(var(--gutter), env(safe-area-inset-right));
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-1) var(--space-3);
    background: var(--elevated);
    border-radius: var(--radius);
    border-left: 3px solid var(--danger);
    box-shadow: var(--shadow-2);
    font-size: 0.875rem;
  }
  button {
    min-height: 44px;
    border-color: transparent;
    background: transparent;
    color: var(--accent, inherit);
    font-weight: 600;
  }
</style>
