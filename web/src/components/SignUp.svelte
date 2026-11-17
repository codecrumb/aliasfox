<script lang="ts">
  import { onMount } from 'svelte';
  import { appConfig } from '../lib/config';
  import { neonAuth } from '../lib/neonAuth';
  import { enterApp, enterWithSession, signInErrorText } from '../lib/signin';
  import EmailCode from './EmailCode.svelte';

  interface Props {
    onNavigate: (path: string) => void;
  }
  let { onNavigate }: Props = $props();

  let email = $state('');
  let password = $state('');
  let submitting = $state(false);
  let error = $state('');
  let view = $state<'form' | 'code'>('form');
  let codeFor = $state('');
  let codePassword = '';
  let emailInput: HTMLInputElement | undefined = $state();

  onMount(() => emailInput?.focus());

  async function submit(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    const addr = email.trim();
    if (submitting || addr === '' || password === '') return;
    submitting = true;
    error = '';
    const pw = password;
    try {
      const r = await neonAuth.signUp(addr, pw);
      if (r.needsVerification) {
        codeFor = addr;
        codePassword = pw;
        view = 'code';
        return;
      }
      await enterWithSession();
    } catch (err) {
      error = signInErrorText(err);
    } finally {
      submitting = false;
    }
  }

  function back(): void {
    view = 'form';
    codePassword = '';
    password = '';
  }

  function go(e: MouseEvent, path: string): void {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    onNavigate(path);
  }
</script>

<section class="screen login" data-screen="signup" tabindex="-1" aria-label="Create an account">
  <div class="brand"><span class="mark" aria-hidden="true"></span><span>AliasFox</span></div>
  {#if !$appConfig.signupsOpen}
    <h2 class="auth-title">Sign-ups are closed for now</h2>
    <p class="auth-alt">Already have an account? <a href="/login" onclick={(e) => go(e, '/login')}>Log in</a></p>
  {:else if view === 'code'}
    <EmailCode email={codeFor} onVerified={() => enterApp(codeFor, codePassword)} onBack={back} />
  {:else}
    <h2 class="auth-title">Create an account</h2>
    <form class="auth-form" onsubmit={submit} novalidate>
      <div>
        <label for="signup-email">Email</label>
        <input
          id="signup-email"
          bind:this={emailInput}
          bind:value={email}
          type="email"
          name="email"
          autocomplete="email"
          spellcheck="false"
          aria-describedby="signup-error"
          readonly={submitting}
        />
      </div>
      <div>
        <label for="signup-password">Password</label>
        <input
          id="signup-password"
          bind:value={password}
          type="password"
          name="password"
          autocomplete="new-password"
          aria-describedby="signup-hint signup-error"
          readonly={submitting}
        />
        <p id="signup-hint" class="field-hint">At least 12 characters, with a digit and a symbol.</p>
      </div>
      <p id="signup-error" class="field-error" role="alert">{error}</p>
      <button type="submit" class="primary" disabled={submitting}>
        {submitting ? 'Creating account…' : 'Create account'}
      </button>
    </form>
    <p class="auth-alt">Already have an account? <a href="/login" onclick={(e) => go(e, '/login')}>Log in</a></p>
  {/if}
</section>
