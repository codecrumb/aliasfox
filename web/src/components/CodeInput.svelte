<script lang="ts">
  import { onDestroy, tick } from 'svelte';

  interface Props {
    onComplete: (code: string) => void;
    error?: number;
    disabled?: boolean;
  }
  let { onComplete, error = 0, disabled = false }: Props = $props();

  const LEN = 6;
  const SHAKE_MS = 420;

  let digits = $state<string[]>(Array.from({ length: LEN }, () => ''));
  let boxes: (HTMLInputElement | undefined)[] = [];
  let invalid = $state(false);
  let shaking = $state(false);
  let lastFired = '';
  let seenError = 0;
  let shakeTimer: ReturnType<typeof setTimeout> | undefined;

  function reducedMotion(): boolean {
    return window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true;
  }

  function focusBox(i: number): void {
    const el = boxes[Math.min(Math.max(i, 0), LEN - 1)];
    el?.focus();
    el?.select();
  }

  export function clear(): void {
    digits = Array.from({ length: LEN }, () => '');
    lastFired = '';
    void tick().then(() => focusBox(0));
  }

  function sync(): void {
    // keep the DOM in step with state even when the typed text equals the old state value
    boxes.forEach((el, i) => {
      if (el) el.value = digits[i] ?? '';
    });
    const code = digits.join('');
    if (code.length < LEN || digits.some((d) => d === '')) {
      lastFired = '';
      return;
    }
    if (code !== lastFired) {
      lastFired = code;
      onComplete(code);
    }
  }

  function fill(start: number, text: string): number {
    const only = text.replace(/\D/g, '');
    const next = [...digits];
    let i = start;
    for (const ch of only) {
      if (i >= LEN) break;
      next[i] = ch;
      i++;
    }
    digits = next;
    return i;
  }

  function oninput(e: Event, i: number): void {
    invalid = false;
    const el = e.currentTarget as HTMLInputElement;
    const only = el.value.replace(/\D/g, '');
    if (only === '') {
      digits[i] = '';
      sync();
      return;
    }
    // A multi-character value (autofill, IME) spreads over the following boxes.
    const end = fill(i, only);
    sync();
    focusBox(end);
  }

  function onkeydown(e: KeyboardEvent, i: number): void {
    if (e.key === 'Backspace' && digits[i] === '' && i > 0) {
      e.preventDefault();
      digits[i - 1] = '';
      sync();
      focusBox(i - 1);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      focusBox(i - 1);
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      focusBox(i + 1);
    }
  }

  function onpaste(e: ClipboardEvent, i: number): void {
    e.preventDefault();
    invalid = false;
    const only = (e.clipboardData?.getData('text') ?? '').replace(/\D/g, '');
    if (only === '') return;
    const start = only.length >= LEN ? 0 : i;
    const end = fill(start, only);
    sync();
    focusBox(end);
  }

  $effect(() => {
    const n = error;
    if (n === seenError) return;
    seenError = n;
    if (n === 0) return;
    invalid = true;
    if (shakeTimer !== undefined) clearTimeout(shakeTimer);
    if (reducedMotion()) {
      shaking = false;
    } else {
      shaking = true;
      shakeTimer = setTimeout(() => {
        shaking = false;
      }, SHAKE_MS);
    }
    digits = Array.from({ length: LEN }, () => '');
    lastFired = '';
    void tick().then(() => {
      boxes.forEach((el) => {
        if (el) el.value = '';
      });
      focusBox(0);
    });
  });

  onDestroy(() => {
    if (shakeTimer !== undefined) clearTimeout(shakeTimer);
  });
</script>

<div class="code">
  <!-- svelte-ignore a11y_role_supports_aria_props -->
  <div
    class="group"
    class:shake={shaking}
    class:invalid
    role="group"
    aria-label="Verification code"
    aria-invalid={invalid}
  >
    {#each [0, 1] as half (half)}
      <div class="triple">
        {#each [0, 1, 2] as k (k)}
          {@const i = half * 3 + k}
          <input
            bind:this={boxes[i]}
            value={digits[i]}
            type="text"
            inputmode="numeric"
            maxlength={1}
            pattern="[0-9]"
            autocomplete={i === 0 ? 'one-time-code' : 'off'}
            aria-label={`Digit ${i + 1} of ${LEN}`}
            {disabled}
            oninput={(e) => oninput(e, i)}
            onkeydown={(e) => onkeydown(e, i)}
            onpaste={(e) => onpaste(e, i)}
            onfocus={(e) => (e.currentTarget as HTMLInputElement).select()}
          />
        {/each}
      </div>
    {/each}
  </div>
  <p class="status" role="status" aria-live="polite">{invalid ? "That code didn't work." : ''}</p>
</div>

<style>
  .code {
    width: 100%;
  }
  .group {
    display: flex;
    gap: var(--space-5);
    width: 100%;
  }
  .triple {
    display: flex;
    flex: 1;
    gap: var(--space-2);
    min-width: 0;
  }
  input {
    flex: 1;
    min-width: 0;
    max-width: 3.5rem;
    min-height: 3.5rem;
    padding: 0;
    text-align: center;
    font-family: var(--font-mono);
    font-size: 1.5rem;
    font-variant-numeric: tabular-nums;
    caret-color: var(--accent);
    transition:
      border-color var(--dur-fast) var(--ease),
      color var(--dur-fast) var(--ease);
  }
  .invalid input {
    border-color: var(--danger);
    color: var(--danger);
  }
  .status {
    margin-top: var(--space-2);
    min-height: 1.5em;
    color: var(--danger);
    font-size: 0.9375rem;
  }
  .shake {
    animation: shake 420ms cubic-bezier(0.36, 0.07, 0.19, 0.97) both;
  }
  @keyframes shake {
    10%,
    90% {
      transform: translateX(-2px);
    }
    20%,
    80% {
      transform: translateX(4px);
    }
    30%,
    50%,
    70% {
      transform: translateX(-7px);
    }
    40%,
    60% {
      transform: translateX(7px);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .shake {
      animation: none;
    }
  }
</style>
