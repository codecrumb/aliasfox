<script lang="ts">
  import { aliasStore } from '../lib/aliases';
  import { online, pending } from '../lib/queue';
  import { sessionStore } from '../lib/session';

  // Online per the browser but the list couldn't be refreshed (server down, cold start):
  // what's on screen is the last saved copy. The Apple-session banner covers expiry.
  const stale = $derived(
    $online && $aliasStore.loadError !== null && $aliasStore.list.length > 0 && $sessionStore.state !== 'reconnect'
  );
  const count = $derived(`${$pending} ${$pending === 1 ? 'change' : 'changes'}`);
  const syncing = $derived($online && !stale && $pending > 0 && $aliasStore.refreshing);
  const text = $derived(
    syncing
      ? `Syncing ${count}…`
      : [
          !$online ? 'Offline' : stale ? "Couldn't refresh · showing your last saved list" : '',
          $pending > 0 ? `${count} waiting to sync` : ''
        ]
          .filter(Boolean)
          .join(' · ')
  );
</script>

{#if !$online || stale || $pending > 0}
  <div class="offline" role="status">
    <p>{text}</p>
    {#if $online && !syncing}
      <button type="button" onclick={() => void aliasStore.refresh()}>{stale ? 'Retry' : 'Sync now'}</button>
    {/if}
  </div>
{/if}

<style>
  .offline {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-1) var(--space-2) var(--space-1) var(--space-4);
    border: 1px solid var(--border);
    border-left: 3px solid var(--danger);
    border-radius: var(--radius);
    background: var(--surface);
  }
  p {
    flex: 1;
    font-size: 0.9375rem;
  }
  button {
    min-height: 44px;
    border-color: transparent;
    background: transparent;
    color: var(--accent);
    font-weight: 600;
  }
</style>
