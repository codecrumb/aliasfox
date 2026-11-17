<script lang="ts">
  import { onMount } from 'svelte';
  import { appConfig } from '../lib/config';
  import { NeonAuthError, neonAuth } from '../lib/neonAuth';
  import { sessionStore } from '../lib/session';
  import { enterApp, needsVerification, signInErrorText } from '../lib/signin';
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
  let passwordInput: HTMLInputElement | undefined = $state();

  const deleted = $derived($sessionStore.accountDeleted ?? null);

  onMount(() => emailInput?.focus());

  async function submit(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    const addr = email.trim();
    if (submitting || addr === '' || password === '') return;
    submitting = true;
    error = '';
    const pw = password;
    try {
      await enterApp(addr, pw);
    } catch (err) {
      if (needsVerification(err)) {
        // Best effort: Neon may already have sent one; "Send a new code" is on the next step.
        await neonAuth.sendVerificationCode(addr).catch(() => {});
        codeFor = addr;
        codePassword = pw;
        view = 'code';
        return;
      }
      error = signInErrorText(err);
      if (err instanceof NeonAuthError && err.kind === 'invalid_credentials') {
        password = '';
        passwordInput?.focus();
      }
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

<section class="screen login" data-screen="signin" tabindex="-1" aria-label="Log in">
  <div class="brand"><span class="mark" aria-hidden="true"></span><span>AliasFox</span></div>
  {#if view === 'code'}
    <EmailCode email={codeFor} onVerified={() => enterApp(codeFor, codePassword)} onBack={back} />
  {:else}
    {#if deleted}
      <div class="auth-note" role="status">
        <p>Your account is deleted.</p>
        {#if deleted === 'pending'}
          <p class="muted">
            {$appConfig.contact
              ? `Your sign-in record is removed on request: write to ${$appConfig.contact}`
              : 'Your sign-in record is removed on request: see the privacy policy for how to reach us.'}
          </p>
        {/if}
      </div>
    {/if}
    <form class="auth-form" onsubmit={submit} novalidate>
      <div>
        <label for="signin-email">Email</label>
        <input
          id="signin-email"
          bind:this={emailInput}
          bind:value={email}
          type="email"
          name="email"
          autocomplete="email"
          spellcheck="false"
          aria-describedby="signin-error"
          readonly={submitting}
        />
      </div>
      <div>
        <label for="signin-password">Password</label>
        <input
          id="signin-password"
          bind:this={passwordInput}
          bind:value={password}
          type="password"
          name="password"
          autocomplete="current-password"
          aria-describedby="signin-error"
          aria-invalid={error !== '' && password === ''}
          readonly={submitting}
        />
      </div>
      <p id="signin-error" class="field-error" role="alert">{error}</p>
      <button type="submit" class="primary" disabled={submitting}>
        {submitting ? 'Logging in…' : 'Log in'}
      </button>
    </form>
    <p class="auth-alt">
      {$appConfig.signupsOpen ? 'New here?' : 'Invited?'}
      <a href="/signup" onclick={(e) => go(e, '/signup')}>Create an account</a>
    </p>
  {/if}
</section>
