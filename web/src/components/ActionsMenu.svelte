<script lang="ts" module>
  export interface MenuAction {
    id: string;
    label: string;
    keys?: string[];
    disabled?: boolean;
    current?: boolean;
    danger?: boolean;
  }
</script>

<script lang="ts">
  import { onMount } from 'svelte';
  import { spoken } from '../lib/keys';
  import Modal from './Modal.svelte';

  interface Props {
    actions: MenuAction[];
    onRun: (id: string) => void;
    onClose: () => void;
  }
  let { actions, onRun, onClose }: Props = $props();

  const uid = $props.id();
  let text = $state('');
  let active = $state(0);
  let input: HTMLInputElement | undefined = $state();
  let list: HTMLElement | undefined = $state();

  const shown = $derived.by(() => {
    const terms = text.toLowerCase().trim().split(/\s+/).filter(Boolean);
    return actions.filter((a) => {
      const l = a.label.toLowerCase();
      return terms.every((t) => l.includes(t));
    });
  });

  function firstEnabled(from: number, step: 1 | -1): number {
    const n = shown.length;
    for (let k = 0; k < n; k++) {
      const i = (((from + step * k) % n) + n) % n;
      if (!shown[i]?.disabled) return i;
    }
    return -1;
  }

  // Reset the highlight to the first enabled item whenever the filter changes.
  $effect.pre(() => {
    void text;
    active = firstEnabled(0, 1);
  });

  const activeId = $derived.by(() => {
    const a = active >= 0 ? shown[active] : undefined;
    return a ? `${uid}-opt-${a.id}` : undefined;
  });

  function move(step: 1 | -1): void {
    if (shown.length === 0) return;
    const next = firstEnabled((active < 0 ? (step === 1 ? -1 : 0) : active) + step, step);
    if (next >= 0) active = next;
    queueMicrotask(() => {
      const el = activeId ? document.getElementById(activeId) : null;
      el?.scrollIntoView?.({ block: 'nearest' });
    });
  }

  function run(i: number): void {
    const a = shown[i];
    if (!a || a.disabled) return;
    onRun(a.id);
  }

  function onKey(e: KeyboardEvent): void {
    if (e.isComposing) return;
    if (e.key === 'ArrowDown' || (e.ctrlKey && e.code === 'KeyJ')) {
      e.preventDefault();
      move(1);
    } else if (e.key === 'ArrowUp' || (e.ctrlKey && e.code === 'KeyK' && !e.metaKey)) {
      e.preventDefault();
      move(-1);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (e.repeat) return; // a held Enter must not run the next thing it lands on
      run(active);
    } else if ((e.metaKey || e.ctrlKey) && e.code === 'KeyK') {
      // The shortcut that opened the menu closes it again.
      e.preventDefault();
      onClose();
    }
  }

  onMount(() => {
    input?.focus({ preventScroll: true });
  });
</script>

<Modal labelledby="{uid}-title" onClose={onClose} size="md">
  <h2 class="visually-hidden" id="{uid}-title">Actions</h2>
  <div class="search">
    <input
      bind:this={input}
      bind:value={text}
      role="combobox"
      aria-label="Filter actions"
      aria-expanded="true"
      aria-controls="{uid}-list"
      aria-autocomplete="list"
      aria-activedescendant={activeId}
      placeholder="Type an action…"
      autocomplete="off"
      spellcheck="false"
      onkeydown={onKey}
    />
  </div>
  <div class="list" id="{uid}-list" role="listbox" aria-label="Actions" bind:this={list}>
    {#each shown as a, i (a.id)}
      <div
        id="{uid}-opt-{a.id}"
        class="item"
        class:active={i === active}
        class:danger={a.danger}
        role="option"
        aria-selected={i === active}
        aria-disabled={a.disabled ? 'true' : undefined}
        tabindex="-1"
        onmousedown={(e) => e.preventDefault()}
        onclick={() => run(i)}
        onkeydown={onKey}
      >
        <span class="label">{a.label}{#if a.current}<span class="visually-hidden"> (current)</span>{/if}</span>
        {#if a.current}<span class="check" aria-hidden="true"></span>{/if}
        {#if a.keys}
          <span class="keys">{#each a.keys as k, j (j)}<kbd aria-label={spoken(k)}>{k}</kbd>{/each}</span>
        {/if}
      </div>
    {:else}
      <p class="empty">No matching actions</p>
    {/each}
  </div>
</Modal>

<style>
  .search {
    margin: calc(var(--space-2) * -1) calc(var(--space-2) * -1) var(--space-3);
  }
  .search input {
    min-height: 48px;
    border-color: transparent;
    background: var(--surface);
    font-size: 1rem;
  }
  .search input:focus-visible {
    outline: none;
    border-color: var(--field-border);
  }
  .list {
    max-height: min(26rem, 60vh);
    overflow-y: auto;
    margin: 0 calc(var(--space-2) * -1) calc(var(--space-2) * -1);
  }
  .item {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    min-height: 44px;
    padding: 0 var(--space-3);
    border-radius: 8px;
    cursor: pointer;
    color: var(--text);
  }
  .item:hover:not([aria-disabled='true']) {
    background: var(--surface);
  }
  .item.active {
    background: color-mix(in srgb, var(--accent) 14%, var(--surface));
    box-shadow: inset 2px 0 0 var(--accent);
  }
  .item.danger .label {
    color: var(--danger);
  }
  .item[aria-disabled='true'] {
    color: var(--muted);
    opacity: 0.55;
    cursor: not-allowed;
  }
  .label {
    flex: 1;
    min-width: 0;
  }
  .check {
    width: 0.5rem;
    height: 0.875rem;
    margin-right: var(--space-2);
    border-right: 2px solid var(--accent);
    border-bottom: 2px solid var(--accent);
    transform: rotate(45deg) translateY(-2px);
  }
  .keys {
    display: inline-flex;
    gap: 3px;
    flex: none;
  }
  .empty {
    padding: var(--space-4) var(--space-3);
    color: var(--muted);
  }
  @media (hover: none) {
    .keys {
      display: none;
    }
  }
</style>
