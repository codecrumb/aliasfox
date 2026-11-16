<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { ApiError } from '../lib/api';
  import { sessionStore } from '../lib/session';

  type Problem = 'invalid' | 'limited' | 'network' | null;

  let password = $state('');
  let submitting = $state(false);
  let problem = $state<Problem>(null);
  let remaining = $state(0);
  let announcedSeconds = $state(0); // fixed at the moment of the error; read once by screen readers
  let done = $state('');
  let input: HTMLInputElement | undefined;
  let form: HTMLFormElement | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let deadline = 0;

  const LIMITED = new Set(['too_many_attempts', 'rate_limited', 'busy']);

  const plural = (n: number): string => `${n} ${n === 1 ? 'second' : 'seconds'}`;

  const alertText = $derived.by(() => {
    if (problem === 'invalid') return 'Incorrect password.';
    if (problem === 'limited') {
      return `Too many attempts. Try again in about ${plural(announcedSeconds)}.`;
    }
    if (problem === 'network') {
      return "Can't reach AliasFox. Check your connection and try again.";
    }
    return '';
  });

  const locked = $derived(problem === 'limited' && remaining > 0);

  onMount(() => {
    input?.focus();
  });

  onDestroy(() => {
    if (timer !== undefined) clearTimeout(timer);
  });

  function finish(): void {
    problem = null;
    remaining = 0;
    done = 'You can try again now.';
    const a = document.activeElement;
    if (!a || a === document.body || (form && form.contains(a))) input?.focus();
  }

  // Re-render once per second, but derive the number from a deadline so a throttled
  // background tab cannot drift.
  function tick(): void {
    timer = setTimeout(() => {
      const left = Math.ceil((deadline - Date.now()) / 1000);
      if (left <= 0) {
        finish();
      } else {
        remaining = left;
        tick();
      }
    }, 1000);
  }

  function startCountdown(seconds: number): void {
    if (timer !== undefined) clearTimeout(timer);
    const n = Math.min(Math.max(Math.ceil(seconds), 1), 3600);
    deadline = Date.now() + n * 1000;
    announcedSeconds = n;
    remaining = n;
    problem = 'limited';
    tick();
  }

  async function submit(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    if (submitting || locked || password === '') return;
    submitting = true;
    problem = null;
    done = '';
    const attempt = password;
    try {
      await sessionStore.login(attempt);
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : '';
      if (detail === 'invalid_credentials') {
        problem = 'invalid';
      } else if (LIMITED.has(detail)) {
        startCountdown(err instanceof ApiError ? (err.retryAfter ?? 30) : 30);
      } else {
        problem = 'network';
      }
    } finally {
      password = '';
      submitting = false;
    }
    if (problem === 'invalid' || problem === 'network') input?.focus();
  }
</script>

<section
  class="screen login"
  data-screen="login"
  tabindex="-1"
  aria-label="Log in"
>
  <div class="brand"><span class="mark" aria-hidden="true"></span><span>AliasFox</span></div>
  <form onsubmit={submit} bind:this={form}>
    <label for="login-password">Password</label>
    <input
      id="login-password"
      bind:this={input}
      bind:value={password}
      type="password"
      name="password"
      autocomplete="current-password"
      aria-describedby="login-error"
      aria-invalid={problem === 'invalid'}
      readonly={submitting}
    />
    <div class="messages">
      <p
        id="login-error"
        class="error"
        class:visually-hidden={problem === 'limited'}
        role="alert"
      >{alertText}</p>
      {#if problem === 'limited'}
        <p class="error countdown" aria-hidden="true">
          Too many attempts. Try again in {plural(remaining)}.
        </p>
      {/if}
      <p class="visually-hidden" role="status" aria-live="polite">{done}</p>
    </div>
    <button type="submit" class="primary" disabled={submitting || locked}>
      {submitting ? 'Logging in…' : 'Log in'}
    </button>
  </form>
</section>

<style>
  form {
    width: 100%;
    display: flex;
    flex-direction: column;
  }
  .messages {
    margin: var(--space-2) 0 var(--space-5);
    min-height: 1.5em;
  }
  .error {
    margin: 0;
    color: var(--danger);
    font-size: 0.9375rem;
  }
</style>
