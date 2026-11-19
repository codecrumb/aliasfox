<script lang="ts" module>
  const ORIGINS: Record<string, string> = {
    SAFARI: 'Safari',
    MAIL: 'Mail',
    IN_APP: 'App',
    ON_DEMAND: 'On demand'
  };
  // Row text is capped: the ellipsis hides the rest anyway, and huge text nodes slow layout.
  const ROW_TEXT_MAX = 300;

  export function originLabel(origin: string | null): string | null {
    return origin !== null && Object.hasOwn(ORIGINS, origin) ? (ORIGINS[origin] ?? null) : null;
  }

  export function cap(s: string | null | undefined): string {
    if (typeof s !== 'string') return '';
    return s.length > ROW_TEXT_MAX ? s.slice(0, ROW_TEXT_MAX) : s;
  }
</script>

<script lang="ts">
  import type { Alias } from '../lib/types';

  interface Props {
    alias: Alias;
    id: string;
    index: number;
    total: number;
    selected: boolean;
    /** Number shown while Alt/Option is held (the row's Alt+digit shortcut). */
    hint?: string;
    /** Briefly highlighted after its address was copied. */
    flash?: boolean;
    /** Selected for a bulk action. */
    marked?: boolean;
    /** Some alias is selected, so every row shows its check slot. */
    markMode?: boolean;
  }
  let { alias, id, index, total, selected, hint, flash = false, marked = false, markMode = false }: Props = $props();

  const label = $derived(cap(alias.label).trim());
  const origin = $derived(originLabel(alias.origin));
  const meta = $derived(cap(alias.origin_app_name) || cap(alias.domain));
</script>

<div
  class="row"
  class:selected
  class:off={!alias.is_active}
  class:flash
  class:marked
  role="option"
  {id}
  aria-selected={selected}
  aria-setsize={total}
  aria-posinset={index + 1}
  data-index={index}
  data-id={alias.anonymous_id}
>
  {#if markMode}<span class="check" class:on={marked} aria-hidden="true"></span>{/if}
  {#if hint !== undefined}<kbd class="num" aria-hidden="true">{hint}</kbd>{/if}
  <div class="text">
    <span class="label ellipsis" class:empty={label === ''} dir="auto">{label === '' ? 'No label' : label}</span>
    <span class="addr ellipsis">{cap(alias.hme)}</span>{#if marked}<span class="visually-hidden">, selected for bulk action</span>{/if}
  </div>
  {#if meta}
    <span class="meta ellipsis" dir="auto">{meta}</span>
  {/if}
  <span class="badges">
    {#if !alias.is_active}<span class="badge off-badge">Inactive</span>{/if}
    {#if origin}<span class="badge">{origin}</span>{/if}
  </span>
  {#if selected}<kbd class="enter" aria-hidden="true">↵ copy</kbd>{/if}
</div>

<style>
  .row {
    position: relative;
    display: flex;
    align-items: center;
    gap: var(--space-3);
    height: var(--row-h);
    padding: 0 var(--space-3) 0 var(--space-4);
    border-radius: 8px;
    cursor: default;
    overflow: hidden;
    contain: layout paint;
  }
  .row:hover {
    background: color-mix(in srgb, var(--surface) 70%, transparent);
  }
  .row.selected {
    background: var(--surface);
  }
  .row.selected::before {
    content: '';
    position: absolute;
    left: 4px;
    top: 12px;
    bottom: 12px;
    width: 3px;
    border-radius: 2px;
    background: var(--accent);
  }
  .row.flash {
    background: color-mix(in srgb, var(--ok) 18%, var(--surface));
  }
  .row {
    transition: background-color var(--dur) var(--ease);
  }
  @media (pointer: coarse) {
    .row {
      -webkit-touch-callout: none;
      user-select: none; /* long-press selects the row, not its text */
    }
  }
  .row.marked {
    background: color-mix(in srgb, var(--accent) 10%, var(--surface));
  }
  .check {
    flex: none;
    width: 16px;
    height: 16px;
    border: 1.5px solid var(--field-border);
    border-radius: 4px;
  }
  .check.on {
    border-color: var(--accent);
    background:
      linear-gradient(135deg, transparent 45%, var(--accent-contrast) 45% 55%, transparent 55%) 3px 6px / 5px 5px no-repeat,
      linear-gradient(45deg, transparent 45%, var(--accent-contrast) 45% 55%, transparent 55%) 6px 3px / 8px 8px no-repeat,
      var(--accent);
  }
  .enter {
    display: none;
    flex: none;
    color: var(--muted);
  }
  @media (hover: hover) and (min-width: 640px) {
    .enter {
      display: inline-flex;
    }
  }
  .num {
    flex: none;
    min-width: 1.25rem;
    padding: 0 var(--space-1);
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--surface);
    color: var(--muted);
    font-family: var(--font-mono);
    font-size: 0.75rem;
    text-align: center;
  }
  .text {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    justify-content: center;
    line-height: 1.3;
  }
  .ellipsis {
    display: block;
    min-width: 0;
    max-width: 100%;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .label {
    color: var(--text);
    font-size: 0.9375rem;
    font-weight: 500;
    unicode-bidi: plaintext;
  }
  .label.empty {
    color: var(--muted);
    font-weight: 400;
  }
  .addr {
    color: var(--muted);
    font-family: var(--font-mono);
    font-size: 0.8125rem;
  }
  .off .label,
  .off .addr {
    opacity: 0.6;
  }
  .meta {
    display: none;
    flex: 0 1 11rem;
    color: var(--muted);
    font-size: 0.8125rem;
    text-align: right;
  }
  .badges {
    display: inline-flex;
    gap: var(--space-1);
    flex: none;
  }
  .badge {
    padding: 1px var(--space-2);
    border: 1px solid var(--border);
    border-radius: 999px;
    color: var(--muted);
    font-size: 0.75rem;
    line-height: 1.4;
    white-space: nowrap;
  }
  .off-badge {
    border-color: color-mix(in srgb, var(--danger) 45%, var(--border));
    color: var(--danger);
  }
  @media (min-width: 1100px) {
    .meta {
      display: block;
    }
  }
</style>
