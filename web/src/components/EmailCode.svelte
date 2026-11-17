<script lang="ts">
  import { onMount } from 'svelte';
  import { neonAuth, neonAuthMessage } from '../lib/neonAuth';
  import { signInErrorText } from '../lib/signin';

  interface Props {
    email: string;
    /** Runs after the code is accepted (signs in and enters the app); its errors show here. */
    onVerified: () => Promise<void>;
    onBack: () => void;
  }
  let { email, onVerified, onBack }: Props = $props();

  const uid = $props.id();
  let code = $state('');
  let busy = $state(false);
  let error = $state('');
  let status = $state('');
  let verified = false; // a failure after verification must not ask for the code again
  let input: HTMLInputElement | undefined;

  onMount(() => input?.focus());

  async function submit(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    const c = code.replace(/\s+/g, '');
    if (busy || (c === '' && !verified)) return;
    busy = true;
    error = '';
    status = '';
    try {
      if (!verified) {
        try {
          await neonAuth.verifyEmailCode(email, c);
        } catch (err) {
          error = neonAuthMessage(err);
          code = '';
          input?.focus();
          return;
        }
        verified = true;
      }
      await onVerified();
    } catch (err) {
      error = signInErrorText(err);
    } finally {
      busy = false;
    }
  }

  async function resend(): Promise<void> {
    if (busy) return;
    error = '';
    status = '';
    try {
      await neonAuth.sendVerificationCode(email);
      status = 'A new code is on its way.';
    } catch (err) {
      error = neonAuthMessage(err);
    }
    input?.focus();
  }
</script>

<h2 class="auth-title">Check your email</h2>
<p class="muted">Enter the code we sent to <span class="mono">{email}</span>.</p>
<form class="auth-form" onsubmit={submit} novalidate>
  <div>
    <label for="{uid}-code">Code</label>
    <input
      id="{uid}-code"
      bind:this={input}
      bind:value={code}
      name="code"
      inputmode="numeric"
      autocomplete="one-time-code"
      spellcheck="false"
      maxlength="12"
      aria-describedby="{uid}-error"
      aria-invalid={error !== ''}
      readonly={busy}
    />
  </div>
  <p id="{uid}-error" class="field-error" role="alert">{error}</p>
  <p class="muted" role="status" aria-live="polite">{status}</p>
  <button type="submit" class="primary" disabled={busy}>{busy ? 'Checking…' : 'Continue'}</button>
</form>
<p class="auth-alt">
  <button type="button" class="ghost" onclick={() => void resend()} disabled={busy}>Send a new code</button>
  <button type="button" class="ghost" onclick={onBack} disabled={busy}>Back</button>
</p>
