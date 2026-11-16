<script lang="ts" module>
  import { getContext, setContext } from 'svelte';

  // Open modals, bottom to top. Only the top one is interactive; the ones below are inert.
  const stack: HTMLElement[] = [];
  const RETURN_KEY = Symbol('modal-return-focus');

  /** Where focus goes when the last modal closes (e.g. the palette search field). */
  export function setModalReturnFocus(fn: () => void): void {
    setContext(RETURN_KEY, fn);
  }

  export function openModalCount(): number {
    return stack.length;
  }

  const FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function focusablesIn(el: HTMLElement): HTMLElement[] {
    return Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((n) => !n.closest('[inert]'));
  }
</script>

<script lang="ts">
  import { onDestroy, onMount, type Snippet } from 'svelte';

  interface Props {
    /** Accessible name; or use labelledby with the id of a visible heading. */
    label?: string;
    labelledby?: string;
    describedby?: string;
    onClose: () => void;
    /** Backdrop clicks do not close. */
    persistent?: boolean;
    /** Esc closes (default). */
    escape?: boolean;
    size?: 'sm' | 'md' | 'lg';
    children: Snippet;
  }
  let { label, labelledby, describedby, onClose, persistent = false, escape = true, size = 'md', children }: Props =
    $props();

  const returnFocus = getContext<(() => void) | undefined>(RETURN_KEY);

  let layer: HTMLElement | undefined = $state();
  let panel: HTMLElement | undefined = $state();
  let opener: Element | null = null;
  let downOnBackdrop = false;

  function onKey(e: KeyboardEvent): void {
    if (!panel || stack[stack.length - 1] !== layer) return;
    if (e.key === 'Escape' && !e.isComposing) {
      e.preventDefault();
      e.stopPropagation();
      if (escape) onClose();
      return;
    }
    if (e.key !== 'Tab') return;
    const items = focusablesIn(panel);
    const first = items[0];
    const last = items[items.length - 1];
    if (!first || !last) {
      e.preventDefault();
      panel.focus();
      return;
    }
    const active = document.activeElement;
    if (e.shiftKey && (active === first || !panel.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && (active === last || !panel.contains(active))) {
      e.preventDefault();
      first.focus();
    }
  }

  onMount(() => {
    const el = layer;
    if (!el || !panel) return;
    opener = document.activeElement;
    stack[stack.length - 1]?.setAttribute('inert', '');
    stack.push(el);
    document.body.classList.add('modal-open');
    // Content components may move focus to a specific field in their own onMount (which runs after
    // this one); default to the first focusable control.
    const target = panel.querySelector<HTMLElement>('[data-autofocus]') ?? focusablesIn(panel)[0] ?? panel;
    target.focus({ preventScroll: true });

    const down = (e: PointerEvent | MouseEvent): void => {
      downOnBackdrop = e.target instanceof Element && e.target.classList.contains('backdrop');
    };
    const click = (e: MouseEvent): void => {
      const onBackdrop = e.target instanceof Element && e.target.classList.contains('backdrop');
      if (onBackdrop && downOnBackdrop && !persistent && stack[stack.length - 1] === el) onClose();
      downOnBackdrop = false;
    };
    el.addEventListener('keydown', onKey);
    el.addEventListener('mousedown', down);
    el.addEventListener('click', click);
    return () => {
      el.removeEventListener('keydown', onKey);
      el.removeEventListener('mousedown', down);
      el.removeEventListener('click', click);
    };
  });

  onDestroy(() => {
    const el = layer;
    const i = el ? stack.indexOf(el) : -1;
    if (i >= 0) stack.splice(i, 1);
    const top = stack[stack.length - 1];
    top?.removeAttribute('inert');
    if (stack.length === 0) document.body.classList.remove('modal-open');
    const back = opener;
    // Wait until the closing render has finished (e.g. the palette's inert attribute is gone).
    queueMicrotask(() => {
      if (top) {
        if (back instanceof HTMLElement && back.isConnected && top.contains(back)) back.focus({ preventScroll: true });
        else (focusablesIn(top)[0] ?? top.querySelector<HTMLElement>('[role="dialog"]'))?.focus({ preventScroll: true });
        return;
      }
      if (stack.length > 0) return;
      if (returnFocus) returnFocus();
      else if (back instanceof HTMLElement && back.isConnected) back.focus({ preventScroll: true });
    });
  });
</script>

<div class="modal-layer" bind:this={layer}>
  <div class="backdrop" aria-hidden="true"></div>
  <div
    class="panel {size}"
    role="dialog"
    aria-modal="true"
    aria-label={label}
    aria-labelledby={labelledby}
    aria-describedby={describedby}
    tabindex="-1"
    bind:this={panel}
  >
    {@render children()}
  </div>
</div>

<style>
  .modal-layer {
    position: fixed;
    inset: 0;
    z-index: var(--z-modal);
    display: grid;
    justify-items: center;
    align-items: start;
    padding: max(10vh, var(--space-6)) max(var(--gutter), env(safe-area-inset-right))
      max(var(--gutter), env(safe-area-inset-bottom)) max(var(--gutter), env(safe-area-inset-left));
    overflow-y: auto;
    overscroll-behavior: contain;
  }
  .backdrop {
    position: fixed;
    inset: 0;
    background: rgb(5 6 8 / 0.72);
    animation: modal-fade var(--dur) var(--ease) both;
  }
  .panel {
    position: relative;
    width: 100%;
    max-width: 30rem;
    padding: var(--space-5);
    background: var(--elevated);
    border-radius: 14px;
    box-shadow: var(--shadow-2);
    animation: modal-in var(--dur) var(--ease) both;
  }
  .panel.sm {
    max-width: 24rem;
  }
  .panel.lg {
    max-width: 36rem;
  }
  .panel:focus {
    outline: none;
  }
  @keyframes modal-fade {
    from {
      opacity: 0;
    }
    to {
      opacity: 1;
    }
  }
  @keyframes modal-in {
    from {
      opacity: 0;
      transform: translateY(-6px) scale(0.985);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .backdrop,
    .panel {
      animation: none;
    }
  }
</style>
