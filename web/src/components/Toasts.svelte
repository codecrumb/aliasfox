<script lang="ts">
  import { toasts } from '../lib/toasts';
</script>

<div class="toasts" aria-live="polite">
  {#each $toasts as t (t.id)}
    <div class="toast {t.kind}" role={t.kind === 'error' ? 'alert' : undefined}>
      <span class="text">{t.text}</span>
      {#if t.action}
        {@const act = t.action}
        <button type="button" class="act" onclick={() => { toasts.dismiss(t.id); act.run(); }}>{act.label}</button>
      {/if}
      <button type="button" class="close" aria-label="Dismiss notification" onclick={() => toasts.dismiss(t.id)}>
        <span aria-hidden="true">×</span>
      </button>
    </div>
  {/each}
</div>

<style>
  .toasts {
    position: fixed;
    z-index: var(--z-toast);
    left: max(var(--gutter), env(safe-area-inset-left));
    right: max(var(--gutter), env(safe-area-inset-right));
    bottom: max(var(--gutter), env(safe-area-inset-bottom));
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-2);
    pointer-events: none;
  }
  .toast {
    pointer-events: auto;
    display: flex;
    align-items: center;
    gap: var(--space-3);
    width: 100%;
    max-width: 24rem;
    padding: var(--space-2) var(--space-2) var(--space-2) var(--space-4);
    background: var(--elevated);
    border-radius: var(--radius);
    border-left: 3px solid var(--ok);
    box-shadow: var(--shadow-2);
    animation: toast-in var(--dur) var(--ease) both;
  }
  .toast.error {
    border-left-color: var(--danger);
  }
  .text {
    flex: 1;
    overflow-wrap: anywhere;
  }
  .act {
    flex: none;
    min-height: 44px;
    padding: 0 var(--space-3);
    border-color: transparent;
    background: transparent;
    color: var(--accent, inherit);
    font-weight: 600;
  }
  .close {
    flex: none;
    width: 44px;
    padding: 0;
    border-color: transparent;
    background: transparent;
    color: var(--muted);
    font-size: 1.25rem;
  }
  @keyframes toast-in {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }
  @media (min-width: 640px) {
    .toasts {
      left: auto;
      align-items: flex-end;
      width: 24rem;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .toast {
      animation: none;
    }
  }
</style>
