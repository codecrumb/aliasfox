<script lang="ts">
  import { onDestroy, onMount, tick, untrack } from 'svelte';
  import { SvelteSet } from 'svelte/reactivity';
  import { get } from 'svelte/store';
  import { ApiError, api } from '../lib/api';
  import { aliasStore } from '../lib/aliases';
  import { copyText } from '../lib/clipboard';
  import { describeError } from '../lib/errors';
  import { KEYMAP, cap, handleKey, isAltLetter, isMac, type KeyCtx, type KeyId } from '../lib/keys';
  import { parseQuery, query, sortAliases, type SortMode } from '../lib/search';
  import { sessionStore } from '../lib/session';
  import * as queue from '../lib/queue';
  import { pending as pendingChanges } from '../lib/queue';
  import { IMPORT_MAX_BYTES, download, planImport, toCsv, type ImportPlan } from '../lib/transfer';
  import { toasts } from '../lib/toasts';
  import type { Alias } from '../lib/types';
  import { LIST_PADDING, ROW_HEIGHT, firstVisibleRow, pageSize, scrollToShow, windowFor } from '../lib/virtual';
  import AccountDialog from './AccountDialog.svelte';
  import ActionsMenu, { type MenuAction } from './ActionsMenu.svelte';
  import OfflineBanner from './OfflineBanner.svelte';
  import AliasRow from './AliasRow.svelte';
  import ConfirmDialog from './ConfirmDialog.svelte';
  import ConnectApple from './ConnectApple.svelte';
  import CopyFallback from './CopyFallback.svelte';
  import DetailPane from './DetailPane.svelte';
  import EditDialog from './EditDialog.svelte';
  import ForwardToDialog from './ForwardToDialog.svelte';
  import Modal, { setModalReturnFocus } from './Modal.svelte';
  import NewAlias from './NewAlias.svelte';
  import ShortcutHelp from './ShortcutHelp.svelte';
  import StatsDialog from './StatsDialog.svelte';

  type Filter = 'all' | 'active' | 'inactive';
  type Dialog =
    | { kind: 'new' }
    | { kind: 'edit'; alias: Alias }
    | { kind: 'delete'; alias: Alias }
    | { kind: 'bulkDelete' }
    | { kind: 'menu' }
    | { kind: 'help' }
    | { kind: 'stats' }
    | { kind: 'import'; plan: ImportPlan }
    | { kind: 'detail' }
    | { kind: 'forward'; current: string }
    | { kind: 'disconnect' }
    | { kind: 'account' }
    | { kind: 'logout' }
    | { kind: 'copy'; text: string };

  const FILTERS: { id: Filter; label: string }[] = [
    { id: 'all', label: 'All' },
    { id: 'active', label: 'Active' },
    { id: 'inactive', label: 'Inactive' }
  ];
  const SORTS: { id: SortMode; label: string }[] = [
    { id: 'newest', label: 'Newest' },
    { id: 'oldest', label: 'Oldest' },
    { id: 'label', label: 'Label A–Z' },
    { id: 'address', label: 'Address A–Z' }
  ];
  const SKELETON = [0, 1, 2, 3, 4, 5, 6, 7];
  const UNREADABLE =
    "AliasFox can't read the stored Apple session (the server key may have changed). Reconnect to continue.";
  const EXPIRED = 'Your Apple session expired. Reconnect to keep editing.';

  const uid = $props.id();
  const listId = `${uid}-list`;
  const mac = isMac();

  let q = $state('');
  let filter = $state<Filter>('all');
  let selIndex = $state(0);
  let selectedId: string | null = null;
  let pendingId: string | null = null;
  let lastKey = '';
  let scrollTop = $state(0);
  let viewH = $state(0);
  let dialog = $state<Dialog | null>(null);
  let reconnectOpen = $state(false);

  let rootEl: HTMLElement | undefined = $state();
  let searchEl: HTMLInputElement | undefined = $state();
  let listEl: HTMLElement | undefined = $state();

  const reconnecting = $derived($sessionStore.state === 'reconnect');
  const bannerText = $derived($sessionStore.reason === 'session_unreadable' ? UNREADABLE : EXPIRED);
  const anyOpen = $derived(dialog !== null || reconnectOpen);

  const list = $derived($aliasStore.list);
  const searched = $derived(query($aliasStore.index, q));
  // `sort:oldest` in the search box beats the sort picked with the button or Alt+S.
  let sortMode = $state<SortMode>('newest');
  const sort = $derived(parseQuery(q).sort ?? sortMode);
  const sortLabel = $derived(SORTS.find((s) => s.id === sort)?.label ?? 'Newest');
  const results = $derived(
    sortAliases(
      filter === 'all' ? searched : searched.filter((a) => (filter === 'active' ? a.is_active : !a.is_active)),
      sort
    )
  );
  // Counts follow the search text, so the tabs show how many of each kind match right now.
  const counts = $derived({
    all: searched.length,
    active: searched.filter((a) => a.is_active).length,
    inactive: searched.filter((a) => !a.is_active).length
  });
  // Bulk selection: ids picked with Alt+X, Alt+A or Shift+arrows. It survives searching and
  // filtering, so a bulk action can include aliases the current view hides; the bar says how many.
  const marked = new SvelteSet<string>();
  let bulkBusy = $state(false);
  const markedAliases = $derived(marked.size === 0 ? [] : $aliasStore.index.newestFirst.filter((a) => marked.has(a.anonymous_id)));
  const hiddenMarked = $derived(
    marked.size === 0 ? 0 : markedAliases.length - results.filter((a) => marked.has(a.anonymous_id)).length
  );
  const win = $derived(windowFor(scrollTop, viewH, results.length));
  const visible = $derived(results.slice(win.start, win.end));
  // Holding Alt/Option labels the first ten visible rows 1..9,0; Alt+<digit> selects that row.
  // The hints appear only after Alt has been held for ALT_HINT_MS, so a quick Alt+<digit> jumps
  // without the numbers flashing up first.
  const ALT_HINT_MS = 400;
  let altHeld = $state(false);
  let altTimer: ReturnType<typeof setTimeout> | undefined;
  function altDown(e: KeyboardEvent): void {
    if (e.key !== 'Alt') {
      cancelAlt(false);
    } else if (!e.repeat && altTimer === undefined && !altHeld) {
      altTimer = setTimeout(() => {
        altTimer = undefined;
        altHeld = true;
      }, ALT_HINT_MS);
    }
  }
  function cancelAlt(hide: boolean): void {
    if (altTimer !== undefined) clearTimeout(altTimer);
    altTimer = undefined;
    if (hide) altHeld = false;
  }
  const numberBase = $derived(firstVisibleRow(scrollTop));
  const selected = $derived<Alias | null>(results[selIndex] ?? null);
  const activeDescendant = $derived(
    selected && selIndex >= win.start && selIndex < win.end ? optId(selIndex) : undefined
  );

  const loadingEmpty = $derived($aliasStore.loading && list.length === 0);
  // Only list-load failures show the error state; failed actions are reported where they happen.
  const errorEmpty = $derived(!loadingEmpty && list.length === 0 && $aliasStore.loadError !== null);
  const empty = $derived(!loadingEmpty && list.length === 0 && $aliasStore.loadError === null);
  const noResults = $derived(list.length > 0 && results.length === 0);
  const countText = $derived(
    loadingEmpty
      ? ''
      : results.length === 0
        ? 'No results'
        : `${results.length.toLocaleString()} ${results.length === 1 ? 'result' : 'results'}`
  );
  const errorText = $derived(
    $aliasStore.loadError === null ? '' : describeError(new ApiError(0, $aliasStore.loadError), 'Something went wrong.')
  );

  // The live region trails the visible count by ANNOUNCE_MS so fast typing produces one
  // announcement instead of one per keystroke.
  const ANNOUNCE_MS = 300;
  let announced = $state(untrack(() => countText));
  let announceTimer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    const t = countText;
    if (announceTimer !== undefined) clearTimeout(announceTimer);
    announceTimer = setTimeout(() => {
      announced = t;
    }, ANNOUNCE_MS);
  });

  function numberHint(n: number): string | undefined {
    return n >= 0 && n < 10 ? String((n + 1) % 10) : undefined;
  }

  function optId(i: number): string {
    return `${uid}-opt-${i}`;
  }

  // Keep the selection meaningful when the results change: a new query or filter starts at the
  // top; a refresh of the same view keeps the same alias selected (by id) when it still exists.
  $effect.pre(() => {
    const r = results;
    const key = `${q}\u0000${filter}\u0000${sort}`;
    untrack(() => {
      const changed = key !== lastKey;
      lastKey = key;
      let idx = -1;
      if (pendingId !== null) {
        idx = r.findIndex((a) => a.anonymous_id === pendingId);
        if (idx >= 0) pendingId = null;
      }
      if (idx < 0 && !changed && selectedId !== null) idx = r.findIndex((a) => a.anonymous_id === selectedId);
      if (idx < 0) idx = changed ? 0 : Math.min(selIndex, r.length - 1);
      idx = Math.max(0, idx);
      selIndex = idx;
      selectedId = r[idx]?.anonymous_id ?? null;
      if (changed && idx === 0) setScroll(0);
      else ensureVisible(idx);
    });
  });

  // Forget selected aliases that no longer exist (deleted here, elsewhere, or after a refresh).
  $effect(() => {
    const l = list;
    untrack(() => {
      if (marked.size === 0) return;
      const ids = new Set(l.map((a) => a.anonymous_id));
      for (const id of [...marked]) if (!ids.has(id)) marked.delete(id);
    });
  });

  // Entering `ready` (first mount, or after a reconnect) loads/refreshes the list once and closes
  // the reconnect overlay. The palette itself stays mounted across ready <-> reconnect.
  let lastState: string | null = null;
  $effect(() => {
    const st = $sessionStore.state;
    if (st === lastState) return;
    lastState = st;
    if (st === 'ready') {
      untrack(() => {
        reconnectOpen = false;
        aliasStore.init().catch(() => {});
      });
    }
  });

  setModalReturnFocus(() => focusSearch());

  // `inert` as an attribute (not only the DOM property) so every engine, and tests, see it.
  $effect(() => {
    if (!rootEl) return;
    if (anyOpen) rootEl.setAttribute('inert', '');
    else rootEl.removeAttribute('inert');
  });

  // Touch screens: focusing the search field raises the keyboard, so only a tap on it does that.
  // Below 900px the details pane is hidden and a tapped row opens it as a dialog instead.
  const touch = typeof matchMedia === 'function' && matchMedia('(hover: none)').matches;
  const stacked = (): boolean => typeof matchMedia === 'function' && matchMedia('(max-width: 899px)').matches;

  function focusSearch(): void {
    if (!searchEl || anyOpen || touch) return;
    searchEl.focus({ preventScroll: true });
  }

  function setScroll(v: number): void {
    scrollTop = v;
    if (listEl) listEl.scrollTop = v;
  }

  function ensureVisible(i: number): void {
    const next = scrollToShow(i, scrollTop, viewH);
    if (next !== null) setScroll(next);
  }

  function jump(n: number): void {
    select(numberBase + n);
  }

  function select(i: number): void {
    if (results.length === 0) return;
    const idx = Math.max(0, Math.min(results.length - 1, i));
    selIndex = idx;
    selectedId = results[idx]?.anonymous_id ?? null;
    ensureVisible(idx);
  }

  function setFilter(f: Filter): void {
    filter = f;
    focusSearch();
  }

  function setSort(m: SortMode): void {
    sortMode = m;
    focusSearch();
  }

  function toggleMark(id: string): void {
    if (!marked.delete(id)) marked.add(id);
  }
  function markSelected(): void {
    if (selected) toggleMark(selected.anonymous_id);
  }
  function markAll(): void {
    const ids = results.map((a) => a.anonymous_id);
    const all = ids.length > 0 && ids.every((id) => marked.has(id));
    for (const id of ids) {
      if (all) marked.delete(id);
      else marked.add(id);
    }
  }
  function markMove(step: 1 | -1): void {
    if (!selected) return;
    marked.add(selected.anonymous_id);
    const to = results[selIndex + step];
    if (to) marked.add(to.anonymous_id);
    select(selIndex + step);
  }
  function clearMarks(): void {
    marked.clear();
  }

  const plural = (n: number): string => `${n.toLocaleString()} ${n === 1 ? 'address' : 'addresses'}`;

  // Runs `fn` on each target in turn (gently on Apple). The selection stays so a deactivate can be
  // undone with a reactivate; deleted aliases drop out of it via the effect above. Stops at the
  // first failure so an outage or expired session doesn't produce a wall of errors.
  async function runBulk(
    targets: Alias[],
    past: string,
    verb: string,
    fn: (a: Alias) => Promise<void>,
    undo?: (a: Alias) => Promise<void>
  ): Promise<void> {
    if (bulkBusy || targets.length === 0) return;
    bulkBusy = true;
    let done = 0;
    let failure: unknown = null;
    try {
      for (const a of targets) {
        try {
          await fn(a);
          done++;
        } catch (e) {
          failure = e;
          break;
        }
      }
    } finally {
      bulkBusy = false;
    }
    if (failure === null) {
      const text = `${past} ${plural(done)}${queuedAmong(targets) ? ' · will sync when online' : ''}`;
      if (undo) offerUndo(text, async () => {
          for (const a of targets) await undo(a); // one at a time, like the original run
        });
      else toasts.push({ kind: 'ok', text });
    } else
      toasts.push({
        kind: 'error',
        text: `${past} ${done} of ${targets.length}. Couldn't ${verb} the rest. ${describeError(failure)}`
      });
  }

  // True when one of these aliases has a change waiting in the offline queue.
  const queuedAmong = (as: Alias[]): boolean => {
    const ids = new Set(queue.snapshot().map((o) => o.id));
    return as.some((a) => ids.has(a.anonymous_id));
  };

  function bulkSetActive(active: boolean): void {
    if (marked.size === 0) return;
    if (reconnecting) return openReconnect();
    const targets = markedAliases.filter((a) => a.is_active !== active);
    if (targets.length === 0) {
      toasts.push({ kind: 'ok', text: `Already ${active ? 'active' : 'inactive'}` });
      return;
    }
    void runBulk(targets, active ? 'Reactivated' : 'Deactivated', active ? 'reactivate' : 'deactivate', (a) =>
      aliasStore.setActive(a.anonymous_id, active),
      (a) => aliasStore.setActive(a.anonymous_id, !active)
    );
  }
  // Shift+Enter with a selection: deactivate them, unless none is active, then reactivate.
  function bulkToggle(): void {
    bulkSetActive(!markedAliases.some((a) => a.is_active));
  }
  function openBulkDelete(): void {
    if (marked.size === 0) return;
    if (reconnecting) return openReconnect();
    dialog = { kind: 'bulkDelete' };
  }
  function confirmBulkDelete(): void {
    const targets = markedAliases;
    closeDialog();
    void runBulk(targets, 'Deleted', 'delete', (a) => aliasStore.remove(a.anonymous_id));
  }
  async function copyMarked(): Promise<void> {
    const text = markedAliases.map((a) => a.hme).join('\n');
    if (text === '') return;
    const r = await copyText(text);
    if (r === 'copied') toasts.push({ kind: 'ok', text: `Copied ${plural(markedAliases.length)}` });
    else if (dialog === null) dialog = { kind: 'copy', text };
  }

  function cycleSort(): void {
    const i = SORTS.findIndex((s) => s.id === sortMode);
    setSort(SORTS[(i + 1) % SORTS.length]?.id ?? 'newest');
  }

  function cycleFilter(): void {
    const i = FILTERS.findIndex((f) => f.id === filter);
    setFilter(FILTERS[(i + 1) % FILTERS.length]?.id ?? 'all');
  }

  function openReconnect(): void {
    reconnectOpen = true;
  }

  function closeDialog(): void {
    dialog = null;
  }

  let flashId = $state<string | null>(null);
  let flashTimer: ReturnType<typeof setTimeout> | undefined;

  async function copyAlias(a: Alias | null): Promise<void> {
    if (!a) return;
    const hme = a.hme;
    const r = await copyText(hme);
    if (r === 'copied') {
      toasts.push({ kind: 'ok', text: `Copied ${hme}` });
      flashId = a.anonymous_id;
      if (flashTimer !== undefined) clearTimeout(flashTimer);
      flashTimer = setTimeout(() => (flashId = null), 700);
    }
    else if (dialog === null) dialog = { kind: 'copy', text: hme };
  }

  // The last deactivate/reactivate that can still be undone, from its toast's Undo button or
  // Cmd/Ctrl+Z. Valid for as long as that toast is on screen.
  let undoable: { toast: number; run: () => void } | null = null;
  function offerUndo(text: string, run: () => Promise<void>): void {
    const go = (): void => {
      undoable = null;
      run().then(
        () => toasts.push({ kind: 'ok', text: 'Undone' }),
        (e: unknown) => toasts.push({ kind: 'error', text: `Couldn't undo. ${describeError(e)}` })
      );
    };
    const toast = toasts.push({ kind: 'ok', text, action: { label: 'Undo', run: go } });
    undoable = { toast, run: go };
  }
  function runUndo(): void {
    const u = undoable;
    if (u === null || !get(toasts).some((t) => t.id === u.toast)) return;
    toasts.dismiss(u.toast);
    u.run();
  }

  const pendingCount = $derived($pendingChanges);
  function exportPendingAndLogout(): Promise<void> {
    const ids = new Set(queue.snapshot().map((o) => o.id));
    download('aliasfox-unsynced.csv', toCsv(list.filter((a) => ids.has(a.anonymous_id))), 'text/csv');
    return sessionStore.logout();
  }

  async function toggleSelected(): Promise<void> {
    const a = selected;
    if (!a) return;
    if (reconnecting) return openReconnect();
    const next = !a.is_active;
    const verb = next ? 'reactivate' : 'deactivate';
    const past = next ? 'Reactivated' : 'Deactivated';
    try {
      await aliasStore.setActive(a.anonymous_id, next);
      offerUndo(`${past} ${a.hme}${queuedAmong([a]) ? ' · will sync when online' : ''}`, () => aliasStore.setActive(a.anonymous_id, !next));
    } catch (e) {
      toasts.push({ kind: 'error', text: `Couldn't ${verb} ${a.hme}. ${describeError(e)}` });
    }
  }

  function pickImport(): void {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.csv,text/csv';
    input.onchange = () => void onImportPicked(input.files?.[0]);
    input.click();
  }

  async function onImportPicked(file: File | undefined): Promise<void> {
    if (!file) return;
    if (file.size > IMPORT_MAX_BYTES) {
      toasts.push({ kind: 'error', text: 'That file is too big to be an alias export.' });
      return;
    }
    try {
      const plan = planImport(await file.text(), list);
      if (plan.updates.length === 0) {
        toasts.push({ kind: 'ok', text: `Nothing to update. ${importSummary(plan)}` });
      } else dialog = { kind: 'import', plan };
    } catch (e) {
      toasts.push({ kind: 'error', text: e instanceof Error ? e.message : "Couldn't read that file." });
    }
  }
  function importSummary(p: ImportPlan): string {
    const parts = [`${p.unchanged} unchanged`];
    if (p.unknown) parts.push(`${p.unknown} not in your list`);
    if (p.inactive) parts.push(`${p.inactive} inactive (reactivate them first)`);
    return parts.join(', ') + '.';
  }
  function confirmImport(plan: ImportPlan): void {
    closeDialog();
    const next = new Map(plan.updates.map((u) => [u.alias.anonymous_id, u]));
    void runBulk(
      plan.updates.map((u) => u.alias),
      'Updated',
      'update',
      (a) => aliasStore.update(a.anonymous_id, { label: next.get(a.anonymous_id)!.label, note: next.get(a.anonymous_id)!.note }),
      (a) => aliasStore.update(a.anonymous_id, { label: a.label ?? '', note: a.note ?? '' })
    );
  }

  function openNew(): void {
    if (reconnecting) return openReconnect();
    dialog = { kind: 'new' };
  }
  function openEdit(): void {
    if (!selected) return;
    if (reconnecting) return openReconnect();
    dialog = { kind: 'edit', alias: selected };
  }
  function openDelete(): void {
    if (!selected) return;
    if (reconnecting) return openReconnect();
    dialog = { kind: 'delete', alias: selected };
  }
  function openForward(): void {
    if (reconnecting) return openReconnect();
    dialog = { kind: 'forward', current: selected?.forward_to ?? list.find((a) => a.forward_to)?.forward_to ?? '' };
  }

  function confirmDelete(a: Alias): void {
    closeDialog();
    aliasStore.remove(a.anonymous_id).then(
      () => toasts.push({ kind: 'ok', text: `Deleted ${a.hme}` }),
      (e: unknown) => toasts.push({ kind: 'error', text: `Couldn't delete ${a.hme}. ${describeError(e)}` })
    );
  }

  async function onCreated(a: Alias | null, copied: 'copied' | 'fallback'): Promise<void> {
    closeDialog();
    if (a === null) {
      // Created, but without a usable result: show it by reloading the list.
      toasts.push({ kind: 'ok', text: 'Created' });
      await refresh();
      return;
    }
    if (!results.some((x) => x.anonymous_id === a.anonymous_id)) {
      q = '';
      filter = 'all';
    }
    pendingId = a.anonymous_id;
    const i = results.findIndex((x) => x.anonymous_id === a.anonymous_id);
    if (i >= 0) select(i);
    if (copied === 'copied') {
      toasts.push({ kind: 'ok', text: 'Created and copied' });
    } else {
      toasts.push({ kind: 'ok', text: 'Created' });
      await tick();
      if (dialog === null) dialog = { kind: 'copy', text: a.hme };
    }
  }

  async function disconnect(): Promise<void> {
    await api.disconnect();
    await aliasStore.clear();
    closeDialog();
    await sessionStore.boot();
  }

  async function refresh(): Promise<void> {
    await aliasStore.refresh();
    const err = get(aliasStore).loadError;
    if (err !== null) toasts.push({ kind: 'error', text: `Couldn't refresh. ${describeError(new ApiError(0, err))}` });
  }

  // Coming back to the tab refreshes quietly when the list is older than this, so a quick
  // switch away and back does not hit Apple again.
  const STALE_MS = 60_000;
  let lastSync = Date.now();
  $effect(() => {
    if (!$aliasStore.refreshing) lastSync = Date.now();
  });
  function onVisible(): void {
    if (document.visibilityState !== 'visible' || anyOpen || $aliasStore.refreshing) return;
    if ($sessionStore.state !== 'ready' || Date.now() - lastSync < STALE_MS) return;
    void aliasStore.refresh();
  }

  const keysFor = (id: KeyId): string[] | undefined => {
    const b = KEYMAP.find((k) => k.id === id);
    return b && (mac && b.keysMac ? b.keysMac : b.keys)[0]?.map((n) => cap(n, mac));
  };

  const menuActions = $derived.by((): MenuAction[] => {
    const none = selected === null;
    const n = marked.size;
    const bulk: MenuAction[] =
      n > 0
        ? [
            { id: 'bulk-deactivate', label: `Deactivate ${n} selected`, disabled: bulkBusy },
            { id: 'bulk-reactivate', label: `Reactivate ${n} selected`, disabled: bulkBusy },
            { id: 'bulk-delete', label: `Delete ${n} selected`, danger: true, disabled: bulkBusy },
            { id: 'bulk-copy', label: `Copy ${n} selected addresses` },
            { id: 'bulk-clear', label: 'Clear selection', keys: keysFor('clearMarks') }
          ]
        : [];
    return [
      ...bulk,
      { id: 'copy', label: 'Copy address', keys: keysFor('copy'), disabled: none },
      { id: 'toggle', label: selected && !selected.is_active ? 'Reactivate' : 'Deactivate', keys: keysFor('toggle'), disabled: none },
      { id: 'new', label: 'New alias', keys: keysFor('new') },
      { id: 'edit', label: 'Edit label and note', keys: keysFor('edit'), disabled: none },
      { id: 'delete', label: 'Delete', keys: keysFor('delete'), disabled: none, danger: true },
      { id: 'mark', label: 'Select or unselect this alias', keys: keysFor('mark'), disabled: none },
      { id: 'mark-all', label: 'Select all in the list', keys: keysFor('markAll') },
      { id: 'forward', label: 'Change forward-to address' },
      { id: 'sort-cycle', label: 'Cycle sort order', keys: keysFor('sort') },
      ...SORTS.map((s) => ({ id: `sort-${s.id}`, label: `Sort: ${s.label}`, current: sortMode === s.id })),
      { id: 'filter-cycle', label: 'Cycle filter', keys: keysFor('filter') },
      { id: 'filter-all', label: 'Filter: All', current: filter === 'all' },
      { id: 'filter-active', label: 'Filter: Active', current: filter === 'active' },
      { id: 'filter-inactive', label: 'Filter: Inactive', current: filter === 'inactive' },
      { id: 'refresh', label: 'Refresh from Apple', keys: keysFor('refresh') },
      { id: 'stats', label: 'Stats', aliases: ['Count', 'Summary', 'Top sites'] },
      { id: 'export-csv', label: 'Export aliases as CSV', aliases: ['Download', 'Backup'] },
      { id: 'export-json', label: 'Export aliases as JSON', aliases: ['Download', 'Backup'] },
      { id: 'import', label: 'Import labels and notes from CSV', aliases: ['Upload'] },
      { id: 'help', label: 'Keyboard shortcuts', keys: keysFor('help') },
      ...($sessionStore.mode === 'multi' ? [{ id: 'account', label: 'Account' }] : []),
      { id: 'disconnect', label: 'Disconnect Apple account', danger: true },
      { id: 'logout', label: 'Log out', aliases: ['Sign out', 'Logout', 'Signout'] }
    ];
  });

  async function runAction(id: string): Promise<void> {
    closeDialog();
    await tick();
    switch (id) {
      case 'copy':
        return copyAlias(selected);
      case 'toggle':
        return toggleSelected();
      case 'new':
        return openNew();
      case 'edit':
        return openEdit();
      case 'delete':
        return openDelete();
      case 'forward':
        return openForward();
      case 'sort-cycle':
        return cycleSort();
      case 'sort-newest':
      case 'sort-oldest':
      case 'sort-label':
      case 'sort-address':
        return setSort(id.slice(5) as SortMode);
      case 'bulk-deactivate':
        return bulkSetActive(false);
      case 'bulk-reactivate':
        return bulkSetActive(true);
      case 'bulk-delete':
        return openBulkDelete();
      case 'bulk-copy':
        return copyMarked();
      case 'bulk-clear':
        return clearMarks();
      case 'mark':
        return markSelected();
      case 'mark-all':
        return markAll();
      case 'filter-cycle':
        return cycleFilter();
      case 'filter-all':
        return setFilter('all');
      case 'filter-active':
        return setFilter('active');
      case 'filter-inactive':
        return setFilter('inactive');
      case 'refresh':
        return refresh();
      case 'help':
        dialog = { kind: 'help' };
        return;
      case 'stats':
        dialog = { kind: 'stats' };
        return;
      case 'export-csv':
        return download('aliasfox-aliases.csv', toCsv(list), 'text/csv');
      case 'export-json':
        return download('aliasfox-aliases.json', JSON.stringify(list, null, 2), 'application/json');
      case 'import':
        if (reconnecting) return openReconnect();
        return pickImport();
      case 'disconnect':
        dialog = { kind: 'disconnect' };
        return;
      case 'account':
        dialog = { kind: 'account' };
        return;
      case 'logout':
        // Changes still waiting to sync are lost on logout: offer them as a CSV first.
        if (pendingCount > 0) dialog = { kind: 'logout' };
        else return sessionStore.logout();
        return;
    }
  }

  function caretAtStart(): boolean {
    const el = searchEl;
    return !!el && el.selectionStart === 0 && el.selectionEnd === 0;
  }

  function keyCtx(): KeyCtx {
    return {
      searchEmpty: q === '',
      caretAtStart: caretAtStart(),
      hasMarks: marked.size > 0,
      blocked: anyOpen,
      mac,
      jump,
      copy: () => void (marked.size > 0 ? copyMarked() : copyAlias(selected)),
      toggle: () => (marked.size > 0 ? bulkToggle() : void toggleSelected()),
      new: openNew,
      edit: openEdit,
      delete: () => (marked.size > 0 ? openBulkDelete() : openDelete()),
      menu: () => (dialog = { kind: 'menu' }),
      up: () => select(selIndex - 1),
      down: () => select(selIndex + 1),
      first: () => select(0),
      last: () => select(results.length - 1),
      pageUp: () => select(selIndex - pageSize(viewH)),
      pageDown: () => select(selIndex + pageSize(viewH)),
      clear: () => (q = ''),
      refresh: () => void refresh(),
      filter: cycleFilter,
      sort: cycleSort,
      mark: markSelected,
      markAll,
      markDown: () => markMove(1),
      markUp: () => markMove(-1),
      clearMarks,
      undo: runUndo,
      help: () => (dialog = { kind: 'help' })
    };
  }

  // macOS Option+letter is a dead key: even with preventDefault some engines start a composition
  // and insert a stray accent. After a handled Alt+letter shortcut, any composition/input on the
  // search field within DEAD_KEY_MS is undone by restoring the previous value.
  const DEAD_KEY_MS = 600;
  let deadGuard: { value: string; until: number } | null = null;

  function onSearchKey(e: KeyboardEvent): void {
    const before = q;
    const handled = handleKey(e, keyCtx());
    if (handled && isAltLetter(e)) deadGuard = { value: before, until: performance.now() + DEAD_KEY_MS };
    else if (e.key !== 'Dead' && e.key !== 'Process') deadGuard = null; // real typing ends the guard
  }

  function undoDeadKey(): void {
    const g = deadGuard;
    if (!g || performance.now() > g.until) {
      deadGuard = null;
      return;
    }
    if (q !== g.value) q = g.value;
    if (searchEl && searchEl.value !== g.value) searchEl.value = g.value;
  }

  function onSearchCompositionEnd(): void {
    undoDeadKey();
    deadGuard = null;
  }

  // Keys pressed while nothing in particular has focus still drive the palette.
  function onWindowKey(e: KeyboardEvent): void {
    if (anyOpen || !searchEl) return;
    // "/" jumps back to search from a button or filter tab, but is a plain character in any text field.
    if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && !e.isComposing && e.target !== searchEl) {
      const t = e.target instanceof HTMLElement ? e.target : null;
      if (!t?.closest('input, textarea, select, [contenteditable]')) {
        e.preventDefault();
        focusSearch();
        return;
      }
    }
    if (e.target !== document.body && e.target !== rootEl) return;
    if (handleKey(e, keyCtx())) return;
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) focusSearch();
  }

  function rowIndex(e: Event): number {
    const row = e.target instanceof Element ? e.target.closest<HTMLElement>('[role="option"]') : null;
    const i = row ? Number(row.dataset.index) : NaN;
    return Number.isInteger(i) ? i : -1;
  }

  let resizeObs: ResizeObserver | undefined;
  const measure = (): void => {
    if (listEl) viewH = listEl.clientHeight;
  };

  onMount(() => {
    if (!touch) searchEl?.focus({ preventScroll: true });
    measure();
    window.addEventListener('resize', measure);
    document.addEventListener('visibilitychange', onVisible);
    if (typeof ResizeObserver !== 'undefined' && listEl) {
      resizeObs = new ResizeObserver(measure);
      resizeObs.observe(listEl);
    }
    const lst = listEl;
    const root = rootEl;
    const down = (e: MouseEvent): void => {
      if (rowIndex(e) >= 0) e.preventDefault(); // keep focus in the search field
    };
    // Touch long-press on a row starts selecting it; the click that follows the release is swallowed.
    let pressTimer: ReturnType<typeof setTimeout> | undefined;
    let pressX = 0;
    let pressY = 0;
    let pressed = false;
    const cancelPress = (): void => {
      if (pressTimer !== undefined) clearTimeout(pressTimer);
      pressTimer = undefined;
    };
    const pressStart = (e: PointerEvent): void => {
      pressed = false;
      cancelPress();
      const i = e.pointerType === 'touch' ? rowIndex(e) : -1;
      if (i < 0) return;
      pressX = e.clientX;
      pressY = e.clientY;
      pressTimer = setTimeout(() => {
        pressed = true;
        const a = results[i];
        if (a) {
          select(i);
          marked.add(a.anonymous_id);
          navigator.vibrate?.(10);
        }
      }, 450);
    };
    const pressMove = (e: PointerEvent): void => {
      if (Math.hypot(e.clientX - pressX, e.clientY - pressY) > 10) cancelPress();
    };
    // Three fingers dragging over the list select every row they cross, from the row they landed on.
    let swipeAnchor = -1;
    let swipeAdded = new SvelteSet<string>();
    const centre = (e: TouchEvent): { x: number; y: number } => {
      let x = 0;
      let y = 0;
      for (const t of e.touches) {
        x += t.clientX;
        y += t.clientY;
      }
      return { x: x / e.touches.length, y: y / e.touches.length };
    };
    const rowAt = (x: number, y: number): number => {
      const row = document.elementFromPoint(x, y)?.closest<HTMLElement>('[role="option"]');
      const i = row ? Number(row.dataset.index) : NaN;
      return Number.isInteger(i) ? i : -1;
    };
    const swipeStart = (e: TouchEvent): void => {
      if (e.touches.length < 2) return;
      cancelPress(); // a second finger means this is not a long-press
      if (e.touches.length !== 3) return;
      const c = centre(e);
      swipeAnchor = rowAt(c.x, c.y);
      swipeAdded = new SvelteSet<string>();
    };
    const swipeMove = (e: TouchEvent): void => {
      if (swipeAnchor < 0 || e.touches.length !== 3) return;
      e.preventDefault();
      const c = centre(e);
      if (lst) {
        const r = lst.getBoundingClientRect();
        if (c.y < r.top + 40) lst.scrollTop -= 14;
        else if (c.y > r.bottom - 40) lst.scrollTop += 14;
      }
      const to = rowAt(c.x, c.y);
      if (to < 0) return;
      const ids = new SvelteSet<string>();
      for (let k = Math.min(swipeAnchor, to); k <= Math.max(swipeAnchor, to); k++) {
        const a = results[k];
        if (a) ids.add(a.anonymous_id);
      }
      for (const id of swipeAdded) {
        if (!ids.has(id)) {
          marked.delete(id);
          swipeAdded.delete(id);
        }
      }
      for (const id of ids) {
        if (!marked.has(id)) {
          marked.add(id);
          swipeAdded.add(id);
        }
      }
    };
    const swipeEnd = (): void => {
      swipeAnchor = -1;
    };
    const noMenu = (e: Event): void => {
      if (rowIndex(e) >= 0) e.preventDefault(); // long-press would otherwise open the browser menu
    };
    const click = (e: MouseEvent): void => {
      if (pressed) {
        pressed = false;
        return;
      }
      const i = rowIndex(e);
      if (i >= 0) {
        select(i);
        focusSearch();
        if (stacked()) {
          // While selecting on mobile there's no other way to tick rows, so a tap marks instead of opening.
          if (marked.size > 0) toggleMark(results[i]!.anonymous_id);
          else dialog = { kind: 'detail' };
        }
      }
    };
    const dbl = (e: MouseEvent): void => {
      const i = rowIndex(e);
      if (i >= 0) void copyAlias(results[i] ?? null);
    };
    const scroll = (): void => {
      if (lst) scrollTop = lst.scrollTop;
    };
    const rootClick = (e: MouseEvent): void => {
      const t = e.target instanceof Element ? e.target : null;
      if (!t || t.closest('button, input, textarea, a, [role="option"], [role="listbox"]')) return;
      const s = window.getSelection?.();
      if (s && !s.isCollapsed) return; // the user is selecting text to copy
      focusSearch();
    };
    lst?.addEventListener('mousedown', down);
    lst?.addEventListener('click', click);
    lst?.addEventListener('pointerdown', pressStart);
    lst?.addEventListener('pointermove', pressMove);
    lst?.addEventListener('pointerup', cancelPress);
    lst?.addEventListener('pointercancel', cancelPress);
    lst?.addEventListener('contextmenu', noMenu);
    lst?.addEventListener('touchstart', swipeStart, { passive: true });
    lst?.addEventListener('touchmove', swipeMove, { passive: false });
    lst?.addEventListener('touchend', swipeEnd);
    lst?.addEventListener('touchcancel', swipeEnd);
    lst?.addEventListener('dblclick', dbl);
    lst?.addEventListener('scroll', scroll, { passive: true });
    root?.addEventListener('click', rootClick);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      lst?.removeEventListener('mousedown', down);
      lst?.removeEventListener('click', click);
      cancelPress();
      lst?.removeEventListener('pointerdown', pressStart);
      lst?.removeEventListener('pointermove', pressMove);
      lst?.removeEventListener('pointerup', cancelPress);
      lst?.removeEventListener('pointercancel', cancelPress);
      lst?.removeEventListener('contextmenu', noMenu);
      lst?.removeEventListener('touchstart', swipeStart);
      lst?.removeEventListener('touchmove', swipeMove);
      lst?.removeEventListener('touchend', swipeEnd);
      lst?.removeEventListener('touchcancel', swipeEnd);
      lst?.removeEventListener('dblclick', dbl);
      lst?.removeEventListener('scroll', scroll);
      root?.removeEventListener('click', rootClick);
    };
  });

  onDestroy(() => {
    if (announceTimer !== undefined) clearTimeout(announceTimer);
    if (flashTimer !== undefined) clearTimeout(flashTimer);
    cancelAlt(false);
    window.removeEventListener('resize', measure);
    resizeObs?.disconnect();
  });
</script>

<svelte:window
  onkeydown={(e) => {
    altDown(e);
    onWindowKey(e);
  }}
  onkeyup={(e) => {
    if (e.key === 'Alt') cancelAlt(true);
  }}
  onblur={() => cancelAlt(true)}
/>

<section
  class="palette"
  data-screen="ready"
  data-testid="palette-root"
  tabindex="-1"
  aria-label="Aliases"
  bind:this={rootEl}
  style:--row-h="{ROW_HEIGHT}px"
  style:--list-pad="{LIST_PADDING}px"
>
  <header class="top">
    <div class="brand"><span class="mark" aria-hidden="true"></span><span>AliasFox</span></div>
    <div class="chips">
      {#if $aliasStore.refreshing && list.length > 0}
        <span class="chip"><span class="pulse" aria-hidden="true"></span><span class="chip-text">Updating…</span></span>
      {/if}
      {#if $aliasStore.fromCache && $aliasStore.refreshing}
        <span class="chip">Offline copy</span>
      {/if}
    </div>
    <button type="button" class="primary new" onclick={openNew}>
      New alias<kbd class="hint-kbd" aria-hidden="true">{cap('Alt', mac)} N</kbd>
    </button>
    <button type="button" class="ghost more" aria-label="Actions" onclick={() => (dialog = { kind: 'menu' })}>
      <span class="dots" aria-hidden="true"></span><kbd class="hint-kbd" aria-hidden="true">{mac ? '⌘' : 'Ctrl'} K</kbd>
    </button>
  </header>

  {#if reconnecting}
    <div class="banner" role="status">
      <p>{bannerText}</p>
      <button type="button" class="reconnect" onclick={openReconnect}>Reconnect</button>
    </div>
  {/if}

  <OfflineBanner />

  <div class="search">
    <span class="glass" aria-hidden="true"></span>
    <input
      bind:this={searchEl}
      bind:value={q}
      type="text"
      role="combobox"
      aria-label="Search aliases"
      aria-expanded={results.length > 0}
      aria-controls={listId}
      aria-autocomplete="list"
      aria-activedescendant={activeDescendant}
      placeholder="Search aliases"
      autocomplete="off"
      autocapitalize="off"
      spellcheck="false"
      enterkeyhint="go"
      onkeydown={onSearchKey}
      oninput={undoDeadKey}
      oncompositionend={onSearchCompositionEnd}
    />
    {#if q !== ''}<kbd class="esc" aria-hidden="true">Esc</kbd>{/if}
  </div>

  <div class="toolbar">
    <div class="seg" role="group" aria-label="Status">
      {#each FILTERS as f (f.id)}
        <button
          type="button"
          aria-pressed={filter === f.id}
          onmousedown={(e) => e.preventDefault()}
          onclick={() => setFilter(f.id)}
          >{f.label}{#if !loadingEmpty}<span class="n" aria-hidden="true">{counts[f.id].toLocaleString()}</span>{/if}</button
        >
      {/each}
    </div>
    <button
      type="button"
      class="ghost sort"
      aria-label="Sort: {sortLabel}. Change sort order"
      onmousedown={(e) => e.preventDefault()}
      onclick={cycleSort}>{sortLabel}</button
    >
    <p class="count" aria-hidden="true">{countText}</p>
    <p class="visually-hidden" data-testid="result-count" role="status" aria-live="polite">{announced}</p>
  </div>

  {#if marked.size > 0}
    <div class="bulkbar" role="region" aria-label="Selected aliases">
      <p class="bulk-count">
        <button type="button" class="ghost bulk-clear" aria-label="Clear" title="Clear selection" onmousedown={(e) => e.preventDefault()} onclick={() => { clearMarks(); focusSearch(); }}>
          <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" fill="none" /></svg>
        </button>
        {marked.size.toLocaleString()} selected{#if hiddenMarked > 0}<span class="muted"> · {hiddenMarked.toLocaleString()} hidden</span>{/if}
      </p>
      <div class="bulk-actions">
        <button type="button" disabled={bulkBusy} onmousedown={(e) => e.preventDefault()} onclick={() => { bulkSetActive(false); focusSearch(); }}>Deactivate</button>
        <button type="button" disabled={bulkBusy} onmousedown={(e) => e.preventDefault()} onclick={() => { bulkSetActive(true); focusSearch(); }}>Reactivate</button>
        <button type="button" class="danger" disabled={bulkBusy} onclick={openBulkDelete}>Delete</button>
      </div>
    </div>
  {/if}

  <div class="body">
    <div class="list-wrap">
      <div
        class="list"
        id={listId}
        role="listbox"
        aria-label="Aliases"
        aria-busy={loadingEmpty}
        tabindex="-1"
        bind:this={listEl}
      >
        {#if loadingEmpty}
          {#each SKELETON as i (i)}
            <div class="skeleton" aria-hidden="true"><span></span><span></span></div>
          {/each}
        {/if}
        <div class="spacer" aria-hidden="true" style:height="{win.top}px"></div>
        {#each visible as a, k (a.anonymous_id)}
          <AliasRow
            alias={a}
            id={optId(win.start + k)}
            index={win.start + k}
            total={results.length}
            selected={win.start + k === selIndex}
            flash={a.anonymous_id === flashId && dialog?.kind !== 'detail'}
            marked={marked.has(a.anonymous_id)}
            markMode={marked.size > 0}
            hint={altHeld ? numberHint(win.start + k - numberBase) : undefined}
          />
        {/each}
        <div class="spacer" aria-hidden="true" style:height="{win.bottom}px"></div>
      </div>

      {#if errorEmpty}
        <div class="state">
          <p class="state-title">Couldn't load your aliases.</p>
          <p class="muted">{errorText}</p>
          <button type="button" class="primary" onclick={() => void aliasStore.refresh()}>Retry</button>
        </div>
      {:else if empty}
        <div class="state">
          <span class="mark big" aria-hidden="true"></span>
          <p class="state-title">No aliases yet. Press {mac ? 'Option' : 'Alt'}+N to create your first one.</p>
        </div>
      {:else if noResults}
        <div class="state">
          <p class="state-title">
            {q !== '' ? `No aliases match “${q}”.` : `No ${filter} aliases.`}
          </p>
          {#if filter !== 'all'}
            <button type="button" onclick={() => setFilter('all')}>Show all aliases</button>
          {/if}
        </div>
      {/if}
    </div>

    <div class="detail">
      <DetailPane
      alias={selected}
      onCopy={() => {
        void copyAlias(selected);
        focusSearch();
      }}
      onToggle={() => {
        void toggleSelected();
        focusSearch();
      }}
      onEdit={openEdit}
      onDelete={openDelete}
      />
    </div>
  </div>

  <footer class="hints" aria-hidden="true">
    <span><kbd>{cap('Enter', mac)}</kbd> copy</span>
    <span><kbd>{cap('Shift', mac)}</kbd><kbd>{cap('Enter', mac)}</kbd> deactivate</span>
    <span><kbd>{cap('Alt', mac)}</kbd><kbd>E</kbd> edit</span>
    <span><kbd>{cap('Alt', mac)}</kbd><kbd>F</kbd> filter</span>
    <span><kbd>{cap('Alt', mac)}</kbd><kbd>S</kbd> sort</span>
    <span><kbd>{cap('Alt', mac)}</kbd><kbd>X</kbd> select</span>
    <span><kbd>{mac ? '⌘' : 'Ctrl'}</kbd><kbd>K</kbd> actions</span>
    <span><kbd>?</kbd> all shortcuts</span>
  </footer>
</section>

{#if dialog?.kind === 'new'}
  <NewAlias onClose={closeDialog} onCreated={(a, copied) => void onCreated(a, copied)} onReconnect={openReconnect} />
{:else if dialog?.kind === 'edit'}
  <EditDialog alias={dialog.alias} onClose={closeDialog} onReconnect={openReconnect} />
{:else if dialog?.kind === 'delete'}
  {@const target = dialog.alias}
  <ConfirmDialog
    title="Delete this address?"
    text="Mail sent to it will stop reaching you. This can't be undone."
    subject={target.hme}
    confirmLabel="Delete"
    danger
    focus="confirm"
    onConfirm={() => confirmDelete(target)}
    onCancel={closeDialog}
  />
{:else if dialog?.kind === 'bulkDelete'}
  <ConfirmDialog
    title="Delete {plural(markedAliases.length)}?"
    text="Mail sent to them will stop reaching you. This can't be undone.{hiddenMarked > 0 ? ` ${hiddenMarked} of them are hidden by the current search or filter.` : ''}"
    subject={markedAliases.slice(0, 3).map((a) => a.hme).join(', ') + (markedAliases.length > 3 ? ` and ${markedAliases.length - 3} more` : '')}
    confirmLabel="Delete {markedAliases.length}"
    danger
    focus="cancel"
    onConfirm={confirmBulkDelete}
    onCancel={closeDialog}
  />
{:else if dialog?.kind === 'logout'}
  <ConfirmDialog
    title="Log out with unsynced changes?"
    text="{pendingCount} {pendingCount === 1 ? 'change has' : 'changes have'} not reached Apple yet and will be lost. The CSV lists the changed addresses; importing it restores label and note edits, not deactivations."
    confirmLabel="Download CSV and log out"
    secondaryLabel="Log out without it"
    onConfirm={exportPendingAndLogout}
    onSecondary={() => sessionStore.logout()}
    onCancel={closeDialog}
  />
{:else if dialog?.kind === 'menu'}
  <ActionsMenu actions={menuActions} onRun={(id) => void runAction(id)} onClose={closeDialog} />
{:else if dialog?.kind === 'detail' && selected}
  <Modal label="Alias details" onClose={closeDialog} size="sm">
    <DetailPane alias={selected} bare copied={selected.anonymous_id === flashId} onCopy={() => void copyAlias(selected)} onToggle={() => void toggleSelected()} onEdit={openEdit} onDelete={openDelete} />
  </Modal>
{:else if dialog?.kind === 'help'}
  <ShortcutHelp onClose={closeDialog} />
{:else if dialog?.kind === 'stats'}
  <StatsDialog {list} onClose={closeDialog} />
{:else if dialog?.kind === 'import'}
  {@const plan = dialog.plan}
  <ConfirmDialog
    title="Update {plural(plan.updates.length)}?"
    text="Labels and notes from the file replace the ones on these addresses. {importSummary(plan)} You can undo this right after."
    confirmLabel="Update {plan.updates.length}"
    focus="cancel"
    onConfirm={() => confirmImport(plan)}
    onCancel={closeDialog}
  />
{:else if dialog?.kind === 'forward'}
  <ForwardToDialog current={dialog.current} onClose={closeDialog} onReconnect={openReconnect} />
{:else if dialog?.kind === 'disconnect'}
  <ConfirmDialog
    title="Disconnect Apple account?"
    text="AliasFox forgets your Apple session and this browser's saved copy of your aliases. Your addresses keep working."
    confirmLabel="Disconnect"
    busyLabel="Disconnecting…"
    danger
    focus="cancel"
    onConfirm={disconnect}
    onCancel={closeDialog}
  />
{:else if dialog?.kind === 'account'}
  <AccountDialog onClose={closeDialog} onDisconnect={() => (dialog = { kind: 'disconnect' })} />
{:else if dialog?.kind === 'copy'}
  <CopyFallback text={dialog.text} onClose={closeDialog} />
{/if}

{#if reconnectOpen}
  <Modal label="Reconnect your Apple account" onClose={() => (reconnectOpen = false)} size="md">
    <ConnectApple />
  </Modal>
{/if}

<style>
  .palette {
    width: 100%;
    max-width: 76rem;
    align-self: stretch;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    min-width: 0;
  }
  .top {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    min-height: 44px;
  }
  .top .brand {
    font-size: 1.0625rem;
    margin-right: var(--space-2);
  }
  .chips {
    flex: 1;
    overflow: hidden;
    display: flex;
    gap: var(--space-2);
    min-width: 0;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: 2px var(--space-3);
    border-radius: 999px;
    background: var(--surface);
    color: var(--muted);
    font-size: 0.8125rem;
    white-space: nowrap;
  }
  .pulse {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent);
    animation: aliasfox-pulse 1.2s ease-in-out infinite;
  }
  .new {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: 0 var(--space-4);
  }
  .new .hint-kbd {
    background: color-mix(in srgb, var(--accent-contrast) 14%, transparent);
    border-color: color-mix(in srgb, var(--accent-contrast) 25%, transparent);
    color: var(--accent-contrast);
  }
  .more {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    min-width: 44px;
    padding: 0 var(--space-3);
  }
  .dots,
  .dots::before,
  .dots::after {
    width: 4px;
    height: 4px;
    border-radius: 50%;
    background: currentColor;
  }
  .dots {
    position: relative;
    margin: 0 6px;
  }
  .dots::before,
  .dots::after {
    content: '';
    position: absolute;
    top: 0;
  }
  .dots::before {
    left: -7px;
  }
  .dots::after {
    left: 7px;
  }

  .banner {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-2) var(--space-2) var(--space-4);
    border: 1px solid color-mix(in srgb, var(--accent) 45%, var(--border));
    border-radius: var(--radius);
    background: color-mix(in srgb, var(--accent) 7%, var(--surface));
  }
  .banner p {
    flex: 1;
    font-size: 0.9375rem;
  }

  .search {
    position: relative;
  }
  .search input {
    min-height: 52px;
    padding-left: 2.75rem;
    padding-right: 3.5rem;
    border-color: var(--border);
    border-radius: 12px;
    font-size: 1.0625rem;
    caret-color: var(--accent);
  }
  .search input:focus-visible {
    outline: none;
    border-color: color-mix(in srgb, var(--accent) 70%, var(--border));
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 22%, transparent);
  }
  .glass {
    position: absolute;
    left: 1rem;
    top: 50%;
    width: 14px;
    height: 14px;
    margin-top: -8px;
    border: 2px solid var(--muted);
    border-radius: 50%;
    pointer-events: none;
  }
  .glass::after {
    content: '';
    position: absolute;
    right: -5px;
    bottom: -4px;
    width: 6px;
    height: 2px;
    background: var(--muted);
    transform: rotate(45deg);
  }
  .esc {
    position: absolute;
    right: var(--space-3);
    top: 50%;
    transform: translateY(-50%);
    pointer-events: none;
  }

  .toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
  }
  .seg {
    display: inline-flex;
    padding: 2px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
  }
  .seg button {
    min-height: 44px;
    padding: 0 var(--space-3);
    border: 0;
    border-radius: 8px;
    background: transparent;
    color: var(--muted);
    font-size: 0.875rem;
  }
  .seg .n {
    margin-left: var(--space-2);
    color: var(--muted);
    font-size: 0.75rem;
    font-variant-numeric: tabular-nums;
  }
  .seg button[aria-pressed='true'] .n {
    color: inherit;
    opacity: 0.8;
  }
  .seg button:hover:not(:disabled) {
    color: var(--text);
  }
  .seg button[aria-pressed='true'] {
    background: var(--elevated);
    color: var(--text);
    box-shadow: var(--shadow-1);
  }
  .bulkbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-3);
    border: 1px solid color-mix(in srgb, var(--accent) 45%, var(--border));
    border-radius: var(--radius);
    background: color-mix(in srgb, var(--accent) 8%, var(--surface));
  }
  .bulk-count {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-weight: 600;
  }
  .bulk-count .bulk-clear {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 28px;
    width: 28px;
    padding: 0;
    border-radius: 999px;
  }
  .bulk-actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
  }
  .bulk-actions button {
    min-height: 36px;
    padding: 0 var(--space-4);
  }
  .sort {
    margin-left: auto;
    white-space: nowrap;
    padding: 0 var(--space-3);
    font-size: 0.875rem;
  }
  .sort::after {
    content: '';
    display: inline-block;
    margin-left: var(--space-2);
    border: 4px solid transparent;
    border-top-color: currentColor;
    transform: translateY(2px);
  }
  .count {
    white-space: nowrap;
    color: var(--muted);
    font-size: 0.875rem;
    font-variant-numeric: tabular-nums;
  }

  .body {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-3);
  }
  .list-wrap {
    position: relative;
    height: max(16rem, 52dvh);
    min-height: 0;
  }
  .list {
    height: 100%;
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: var(--list-pad) var(--space-1);
    border: 1px solid var(--border);
    border-radius: 14px;
    background: color-mix(in srgb, var(--surface) 45%, var(--bg));
  }
  .list:focus {
    outline: none;
  }
  .spacer {
    flex: none;
  }
  .skeleton {
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 6px;
    height: var(--row-h);
    padding: 0 var(--space-4);
  }
  .skeleton span {
    height: 10px;
    border-radius: 4px;
    background: var(--elevated);
    animation: aliasfox-pulse 1.4s ease-in-out infinite;
  }
  .skeleton span:first-child {
    width: 40%;
  }
  .skeleton span:last-child {
    width: 62%;
    opacity: 0.6;
  }
  .state {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--space-3);
    padding: var(--space-5);
    text-align: center;
    pointer-events: none;
  }
  .state button {
    pointer-events: auto;
  }
  .state-title {
    max-width: 28rem;
    overflow-wrap: anywhere;
  }
  .mark.big {
    width: 40px;
    height: 40px;
    margin-bottom: var(--space-2);
  }

  .hints {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2) var(--space-5);
    color: var(--muted);
    font-size: 0.8125rem;
  }
  .hints span {
    display: inline-flex;
    align-items: center;
    gap: 3px;
  }
  .hints kbd {
    margin-right: 2px;
  }
  .hint-kbd {
    display: none;
  }

  @media (max-width: 639px) {
    /* Only the pulsing dot: the full chip got squeezed under the New alias button. */
    .chip-text {
      display: none;
    }
    .toolbar {
      flex-wrap: wrap;
    }
    .seg {
      flex: 1 1 100%;
    }
    .seg button {
      flex: 1;
      min-width: 0;
      padding: 0 var(--space-2);
      white-space: nowrap;
    }
    .count {
      order: 2;
      margin-right: auto;
    }
    .sort {
      order: 3;
      margin-left: 0;
    }
    .list-wrap {
      height: max(16rem, 62dvh);
    }
  }
  @media (max-width: 899px) {
    .detail {
      display: none;
    }
  }
  @media (min-width: 640px) {
    .hint-kbd {
      display: inline-flex;
    }
  }
  @media (min-width: 900px) {
    .palette {
      height: calc(100dvh - 2 * var(--space-6));
      min-height: 30rem;
    }
    .body {
      grid-template-columns: minmax(0, 1fr) minmax(18rem, 24rem);
      grid-template-rows: minmax(0, 1fr);
    }
    .list-wrap {
      height: auto;
    }
  }
  @media (hover: none) {
    .hints,
    .hint-kbd,
    .esc {
      display: none;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .pulse,
    .skeleton span {
      animation: none;
    }
  }
</style>
