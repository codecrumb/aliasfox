<script lang="ts">
  import { onDestroy, onMount } from 'svelte';
  import { ApiError } from '../lib/api';
  import { sessionStore } from '../lib/session';

  type Problem = 'invalid' | 'limited' | 'network' | null;

  let password = $state('');
  let submitting = $state(false);
  let problem = $state<Problem>(null);
  let remaining = $state(0);
  let input: HTMLInputElement | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const LIMITED = new Set(['too_many_attempts', 'rate_limited', 'busy']);

  const message = $derived.by(() => {
    if (problem === 'invalid') return 'Incorrect password.';
    if (problem === 'limited') {
      return `Too many attempts. Try again in ${remaining} ${remaining === 1 ? 'second' : 'seconds'}.`;
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

  function tick(): void {
    timer = setTimeout(() => {
      remaining -= 1;
      if (remaining > 0) {
        tick();
      } else {
        problem = null;
        input?.focus();
      }
    }, 1000);
  }

  function startCountdown(seconds: number): void {
    if (timer !== undefined) clearTimeout(timer);
    remaining = Math.min(Math.max(Math.ceil(seconds), 1), 3600);
    problem = 'limited';
    tick();
  }

  async function submit(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    if (submitting || locked || password === '') return;
    submitting = true;
    problem = null;
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
    if (problem === 'invalid' || problem === 'network') {
      input?.focus();
      input?.select();
    }
  }
</script>

<section class="screen login">
  <div class="brand"><span class="mark" aria-hidden="true"></span><span>AliasFox</span></div>
  <form onsubmit={submit}>
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
    <p id="login-error" class="error" role="alert">{message}</p>
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
  .error {
    min-height: 1.5em;
    margin: var(--space-2) 0 var(--space-5);
    color: var(--danger);
    font-size: 0.9375rem;
  }
</style>
