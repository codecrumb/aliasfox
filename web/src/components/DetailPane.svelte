<script lang="ts">
  import type { Alias } from '../lib/types';
  import { originLabel } from './AliasRow.svelte';

  interface Props {
    alias: Alias | null;
    onCopy: () => void;
    onToggle: () => void;
    onEdit: () => void;
    onDelete: () => void;
  }
  let { alias, onCopy, onToggle, onEdit, onDelete }: Props = $props();

  const fmt = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

  function created(ms: number | null): string {
    if (typeof ms !== 'number' || !Number.isFinite(ms)) return '';
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? '' : fmt.format(d);
  }

  const origin = $derived(alias ? originLabel(alias.origin) : null);
  const date = $derived(alias ? created(alias.created_ms) : '');
</script>

<section class="pane" aria-label="Alias details">
  {#if alias}
    <div class="addr-row">
      <p class="addr">{alias.hme}</p>
      <button type="button" class="copy" aria-label="Copy address" onclick={onCopy}>
        <span class="copy-icon" aria-hidden="true"></span><span class="copy-text" aria-hidden="true">Copy</span>
      </button>
    </div>

    <h2 class="label" class:empty={!alias.label} dir="auto">{alias.label || 'No label'}</h2>

    <dl class="facts">
      {#if alias.note}
        <div class="fact wide">
          <dt>Note</dt>
          <dd class="note" dir="auto">{alias.note}</dd>
        </div>
      {/if}
      <div class="fact">
        <dt>Status</dt>
        <dd><span class="dot" class:on={alias.is_active} aria-hidden="true"></span>{alias.is_active ? 'Active' : 'Off'}</dd>
      </div>
      {#if alias.forward_to}
        <div class="fact">
          <dt>Forwards to</dt>
          <dd class="break">{alias.forward_to}</dd>
        </div>
      {/if}
      {#if origin}
        <div class="fact">
          <dt>Created from</dt>
          <dd>{origin}</dd>
        </div>
      {/if}
      {#if alias.origin_app_name}
        <div class="fact">
          <dt>App</dt>
          <dd class="break" dir="auto">{alias.origin_app_name}</dd>
        </div>
      {/if}
      {#if alias.domain}
        <div class="fact">
          <dt>Site</dt>
          <dd class="break" dir="auto">{alias.domain}</dd>
        </div>
      {/if}
      {#if date}
        <div class="fact">
          <dt>Created</dt>
          <dd>{date}</dd>
        </div>
      {/if}
    </dl>

    <div class="actions">
      <button type="button" onclick={onToggle}>{alias.is_active ? 'Turn off' : 'Turn on'}</button>
      <button type="button" onclick={onEdit}>Edit</button>
      <button type="button" class="ghost delete" onclick={onDelete}>Delete</button>
    </div>
  {:else}
    <p class="none">Select an alias to see its details.</p>
  {/if}
</section>

<style>
  .pane {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    min-width: 0;
    padding: var(--space-4);
    border: 1px solid var(--border);
    border-radius: 14px;
    background: var(--surface);
    overflow-y: auto;
  }
  .addr-row {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    margin: calc(var(--space-2) * -1) calc(var(--space-2) * -1) 0 0;
  }
  .addr {
    flex: 1;
    min-width: 0;
    font-family: var(--font-mono);
    font-size: 0.9375rem;
    color: var(--accent);
    overflow-wrap: anywhere;
  }
  .copy {
    flex: none;
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: 0 var(--space-3);
    border-color: transparent;
    background: transparent;
    color: var(--muted);
  }
  .copy:hover:not(:disabled) {
    border-color: transparent;
    background: var(--elevated);
    color: var(--text);
  }
  .copy-icon {
    position: relative;
    width: 12px;
    height: 14px;
    border: 1.5px solid currentColor;
    border-radius: 3px;
  }
  .copy-icon::after {
    content: '';
    position: absolute;
    left: 2px;
    top: -5px;
    width: 12px;
    height: 14px;
    border-top: 1.5px solid currentColor;
    border-right: 1.5px solid currentColor;
    border-radius: 3px;
  }
  .copy-text {
    font-size: 0.875rem;
  }
  .label {
    font-size: 1.125rem;
    font-weight: 600;
    overflow-wrap: anywhere;
    max-height: 7.5em;
    overflow-y: auto;
  }
  .label.empty {
    color: var(--muted);
    font-weight: 400;
  }
  .facts {
    margin: 0;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(9rem, 1fr));
    gap: var(--space-3) var(--space-4);
  }
  .fact {
    min-width: 0;
  }
  .fact.wide {
    grid-column: 1 / -1;
  }
  dt {
    color: var(--muted);
    font-size: 0.8125rem;
  }
  dd {
    margin: 0;
    font-size: 0.9375rem;
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }
  .break {
    display: block;
    overflow-wrap: anywhere;
  }
  .note {
    display: block;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 10rem;
    overflow-y: auto;
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--danger);
  }
  .dot.on {
    background: var(--ok);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
    margin-top: auto;
  }
  .actions button {
    padding: 0 var(--space-4);
  }
  .delete {
    color: var(--danger);
    margin-left: auto;
  }
  .delete:hover:not(:disabled) {
    color: var(--danger);
  }
  .none {
    color: var(--muted);
    margin: auto 0;
    text-align: center;
  }
</style>
