<script lang="ts">
  import { KEYMAP, cap, isMac, keysFor, spoken } from '../lib/keys';
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
              {#each alt as name, j (j)}<kbd aria-label={spoken(cap(name, mac))}>{cap(name, mac)}</kbd>{/each}
            </span>
          {/each}
        </dd>
      </div>
    {/each}
    <div class="item">
      <dt>Jump to a visible alias (hold {mac ? 'Option' : 'Alt'} to see the numbers)</dt>
      <dd><span class="combo"><kbd aria-label={spoken(cap('Alt', mac))}>{cap('Alt', mac)}</kbd><kbd>1</kbd></span><span class="or">…</span><span class="combo"><kbd aria-label={spoken(cap('Alt', mac))}>{cap('Alt', mac)}</kbd><kbd>0</kbd></span></dd>
    </div>
  </dl>
  <h3 class="tips-title">Search tips</h3>
  <ul class="tips">
    <li><code>is:inactive</code> or <code>is:active</code> — by status; <code>-is:inactive</code> excludes (<code>is:off</code> and <code>is:on</code> work too)</li>
    <li><code>label:</code> <code>note:</code> <code>site:</code> <code>app:</code> <code>to:</code> <code>from:safari</code> — one field only; quote a phrase: <code>label:"gift card"</code></li>
    <li><code>sort:oldest</code> <code>sort:label</code> <code>sort:address</code> — order the results</li>
  </ul>
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
  .tips-title {
    margin-top: var(--space-5);
    font-size: 0.9375rem;
  }
  .tips {
    margin: var(--space-2) 0 0;
    padding-left: var(--space-5);
    color: var(--muted);
    font-size: 0.875rem;
    display: grid;
    gap: var(--space-1);
  }
  code {
    color: var(--text);
    font-family: var(--font-mono);
    font-size: 0.8125rem;
  }
  .or {
    color: var(--muted);
    font-size: 0.8125rem;
  }
</style>
