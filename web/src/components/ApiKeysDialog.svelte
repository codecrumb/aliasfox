<script lang="ts">
  import { onMount } from 'svelte';
  import { api, ApiError } from '../lib/api';
  import { copyText } from '../lib/clipboard';
  import { describeError } from '../lib/errors';
  import { toasts } from '../lib/toasts';
  import type { ApiKey, CreatedApiKey } from '../lib/types';
  import ConfirmDialog from './ConfirmDialog.svelte';
  import Modal from './Modal.svelte';

  interface Props {
    onClose: () => void;
  }
  let { onClose }: Props = $props();

  const uid = $props.id();
  const SCOPES = ['read', 'create', 'edit', 'deactivate', 'delete'] as const;
  const PRESETS = [
    { id: 'bitwarden', label: 'Bitwarden', scopes: ['create'] },
    { id: 'readonly', label: 'Read only', scopes: ['read'] },
    { id: 'assistant', label: 'Assistant (MCP)', scopes: ['read', 'create', 'edit', 'deactivate'] },
    { id: 'full', label: 'Full access', scopes: [...SCOPES] },
    { id: 'custom', label: 'Custom', scopes: [] }
  ] as const;
  const presetLabel = (id: string | null): string => PRESETS.find((p) => p.id === id)?.label ?? 'Custom';

  let keys = $state<ApiKey[]>([]);
  let loaded = $state(false);
  let loadError = $state('');
  let name = $state('');
  let preset = $state<string>('bitwarden');
  let scopes = $state<string[]>(['create']);
  let days = $state('');
  let confirmDelete = $state(false);
  let busy = $state(false);
  let error = $state('');
  let revoking = $state<ApiKey | null>(null);
  // Only ever held here: the server shows the full key once and it is never stored anywhere.
  let created = $state<CreatedApiKey | null>(null);
  let copied = $state('');
  let nameEl: HTMLInputElement | undefined = $state();

  const hasDelete = $derived(scopes.includes('delete'));
  const origin = window.location.origin;
  const canCreate = $derived(!busy && name.trim() !== '' && scopes.length > 0 && (!hasDelete || confirmDelete));

  onMount(() => {
    api.listKeys().then(
      (l) => ((keys = l), (loaded = true)),
      (e: unknown) => ((loadError = describeError(e, "Couldn't load your API keys. Check your connection.")), (loaded = true))
    );
    nameEl?.focus({ preventScroll: true });
  });

  function pick(id: string): void {
    preset = id;
    const p = PRESETS.find((x) => x.id === id);
    if (p && id !== 'custom') scopes = [...p.scopes];
    confirmDelete = false;
  }
  function toggle(s: string, on: boolean): void {
    scopes = on ? [...scopes, s] : scopes.filter((x) => x !== s);
    preset = 'custom';
    confirmDelete = false;
  }

  async function submit(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    if (!canCreate) return;
    error = '';
    busy = true;
    const body: { name: string; preset?: string; scopes?: string[]; expires_days?: number } = { name: name.trim() };
    if (preset === 'custom') body.scopes = SCOPES.filter((s) => scopes.includes(s));
    else body.preset = preset;
    const n = Number.parseInt(days, 10);
    if (Number.isFinite(n)) body.expires_days = n;
    try {
      const k = await api.createKey(body);
      created = k;
      keys = [...keys, (({ id, name: n, preset: p, scopes: sc, prefix, created_at, last_used_at, expires_at }) => ({ id, name: n, preset: p, scopes: sc, prefix, created_at, last_used_at, expires_at }))(k)];
      name = '';
      days = '';
    } catch (err) {
      error =
        err instanceof ApiError && err.detail === 'too_many_keys'
          ? 'You can have at most 20 API keys'
          : describeError(err, "Couldn't create the key. Check the name and expiry, then try again.");
    } finally {
      busy = false;
    }
  }

  async function revoke(k: ApiKey): Promise<void> {
    await api.revokeKey(k.id);
    keys = keys.filter((x) => x.id !== k.id);
    revoking = null;
    toasts.push({ kind: 'ok', text: `Revoked ${k.name}` });
  }

  async function copy(): Promise<void> {
    if (!created) return;
    copied = (await copyText(created.key)) === 'copied' ? 'Copied' : 'Select the key and copy it yourself';
  }

  const when = (iso: string | null): string => (iso ? new Date(iso).toLocaleDateString() : 'never');
</script>

{#if revoking}
  {@const k = revoking}
  <ConfirmDialog
    title="Revoke this key?"
    text="Anything using it stops working right away. This can't be undone."
    subject={k.name}
    confirmLabel="Revoke"
    busyLabel="Revoking…"
    danger
    focus="cancel"
    onConfirm={() => revoke(k)}
    onCancel={() => (revoking = null)}
  />
{:else}
  <Modal labelledby="{uid}-title" onClose={onClose} persistent={created !== null} size="md">
    <h2 class="dialog-title" id="{uid}-title">API keys</h2>
    {#if created}
      {@const c = created}
      <div class="dialog-form">
        <p class="dialog-text">Key "{c.name}" created. You won't see this again, so copy it now.</p>
        <div>
          <label for="{uid}-key">Your new API key</label>
          <input id="{uid}-key" class="mono" readonly value={c.key} onfocus={(e) => e.currentTarget.select()} />
          <p class="field-hint" role="status">{copied}</p>
        </div>
        {#if c.preset === 'bitwarden'}
          <p class="dialog-text">
            In Bitwarden, go to Generator &gt; Username &gt; Forwarded email alias &gt; SimpleLogin. Paste the key as the API key and
            set Server URL to <span class="mono">{origin}</span> (no trailing slash).
          </p>
        {:else}
          <div>
            <p class="dialog-text">To use it with Claude Code, run:</p>
            <pre class="mono cmd">claude mcp add --transport http aliasfox {origin}/mcp --header "Authorization: Bearer {c.key}"</pre>
          </div>
        {/if}
        <div class="dialog-actions">
          <button type="button" onclick={() => void copy()}>Copy</button>
          <button type="button" class="primary" onclick={() => ((created = null), onClose())}>Done</button>
        </div>
      </div>
    {:else}
      <p class="dialog-text">Keys let other apps use your aliases. Each key only gets the access you pick.</p>
      {#if loadError}<p class="field-error" role="alert">{loadError}</p>{/if}
      {#if loaded && !loadError && keys.length === 0}<p class="dialog-text empty">No keys yet.</p>{/if}
      <ul class="keys">
        {#each keys as k (k.id)}
          <li>
            <div class="info">
              <strong>{k.name}</strong>
              <span class="meta">{presetLabel(k.preset)} · <span class="mono">afk_{k.prefix}…</span></span>
              <span class="chips">{#each k.scopes as s (s)}<span class="chip">{s}</span>{/each}</span>
              <span class="meta">Created {when(k.created_at)} · Last used {when(k.last_used_at)}</span>
            </div>
            <button type="button" onclick={() => (revoking = k)} aria-label="Revoke {k.name}">Revoke</button>
          </li>
        {/each}
      </ul>
      <form class="dialog-form new" onsubmit={submit} novalidate>
        <h3 class="sub">New key</h3>
        <div>
          <label for="{uid}-name">Name</label>
          <input id="{uid}-name" bind:this={nameEl} bind:value={name} maxlength="80" autocomplete="off" />
        </div>
        <fieldset>
          <legend>Access</legend>
          {#each PRESETS as p (p.id)}
            <label class="choice"><input type="radio" name="{uid}-preset" checked={preset === p.id} onchange={() => pick(p.id)} />{p.label}</label>
          {/each}
        </fieldset>
        <fieldset>
          <legend>Allowed actions</legend>
          {#each SCOPES as s (s)}
            <label class="choice"><input type="checkbox" checked={scopes.includes(s)} onchange={(e) => toggle(s, e.currentTarget.checked)} />{s}</label>
          {/each}
        </fieldset>
        {#if hasDelete}
          <div class="warn">
            <p>This key can permanently delete aliases.</p>
            <label class="choice"><input type="checkbox" bind:checked={confirmDelete} />I understand this key can delete aliases</label>
          </div>
        {/if}
        <div>
          <label for="{uid}-days">Expires after (days, optional)</label>
          <input id="{uid}-days" bind:value={days} type="number" inputmode="numeric" min="1" max="365" />
        </div>
        <p class="field-error" role="alert">{error}</p>
        <div class="dialog-actions">
          <button type="button" class="ghost" onclick={onClose}>Close</button>
          <button type="submit" class="primary" disabled={!canCreate}>{busy ? 'Creating…' : 'Create key'}</button>
        </div>
      </form>
    {/if}
  </Modal>
{/if}

<style>
  .keys {
    list-style: none;
    margin: var(--space-4) 0;
    padding: 0;
  }
  .keys li {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-3) 0;
    border-bottom: 1px solid var(--border);
  }
  .info {
    display: grid;
    gap: var(--space-1);
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .meta {
    color: var(--muted);
    font-size: 0.875rem;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-1);
  }
  .chip {
    padding: 0 var(--space-2);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font-size: 0.8125rem;
  }
  .sub {
    font-size: 1rem;
  }
  fieldset {
    margin: 0;
    padding: 0;
    border: 0;
    display: grid;
    gap: var(--space-1);
  }
  legend {
    padding: 0;
    margin-bottom: var(--space-2);
    color: var(--muted);
    font-size: 0.9375rem;
  }
  .choice {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    min-height: 44px;
    margin: 0;
    color: inherit;
  }
  .choice input {
    width: 1.25rem;
    min-height: 0;
    height: 1.25rem;
    flex: none;
  }
  .warn {
    padding: var(--space-3);
    border: 1px solid var(--danger);
    border-radius: var(--radius);
    color: var(--danger);
  }
  .cmd {
    margin: var(--space-2) 0 0;
    padding: var(--space-3);
    border-radius: var(--radius);
    background: var(--surface);
    font-size: 0.8125rem;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
</style>
