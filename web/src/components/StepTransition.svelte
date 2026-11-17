<script lang="ts">
  import { onDestroy, type Snippet } from 'svelte';

  interface Props {
    step: number;
    direction: 'forward' | 'back';
    children: Snippet;
  }
  let { step, direction, children }: Props = $props();

  let wrap: HTMLElement | undefined;
  let cls = $state('');
  let timer: ReturnType<typeof setTimeout> | undefined;
  let lastStep: number | undefined;

  // A screen is usually one container; stagger its children rather than the container itself.
  function targets(el: HTMLElement): Element[] {
    const kids = Array.from(el.children);
    const only = kids.length === 1 ? kids[0] : undefined;
    return only && only.children.length > 0 ? Array.from(only.children) : kids;
  }

  function reduced(): boolean {
    return window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true;
  }

  $effect(() => {
    const s = step;
    const dir = direction;
    if (!wrap) return;
    const items = targets(wrap);
    items.forEach((el, i) => {
      if (el instanceof HTMLElement) {
        el.style.setProperty('--i', String(i));
        el.classList.add('step-item');
      }
    });
    const changed = lastStep !== undefined && lastStep !== s;
    lastStep = s;
    if (timer !== undefined) clearTimeout(timer);
    cls = '';
    if (!changed || reduced()) return;
    cls = dir === 'back' ? 'enter-back' : 'enter-forward';
    const total = 320 + Math.min(items.length, 6) * 40 + 60;
    timer = setTimeout(() => {
      cls = '';
    }, total);
  });

  onDestroy(() => {
    if (timer !== undefined) clearTimeout(timer);
  });
</script>

<div class="step {cls}" bind:this={wrap}>
  {@render children()}
</div>

<style>
  .step {
    display: contents;
  }
  .step.enter-forward :global(.step-item),
  .step.enter-back :global(.step-item) {
    animation-duration: var(--dur-slide);
    animation-timing-function: ease-out;
    animation-fill-mode: both;
    animation-delay: calc(min(var(--i, 0), 6) * var(--stagger));
  }
  .step.enter-forward :global(.step-item) {
    animation-name: aliasfox-in-forward;
  }
  .step.enter-back :global(.step-item) {
    animation-name: aliasfox-in-back;
  }
  @keyframes -global-aliasfox-in-forward {
    from {
      opacity: 0;
      transform: translateX(24px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }
  @keyframes -global-aliasfox-in-back {
    from {
      opacity: 0;
      transform: translateX(-24px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .step :global(.step-item) {
      animation: none !important;
    }
  }
</style>
