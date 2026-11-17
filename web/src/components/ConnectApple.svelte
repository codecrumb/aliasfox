<script lang="ts">
  import { onDestroy, onMount, tick } from 'svelte';
  import { ApiError, api } from '../lib/api';
  import { createCountdown } from '../lib/countdown';
  import { sessionStore } from '../lib/session';
  import { toasts } from '../lib/toasts';
  import CodeInput from './CodeInput.svelte';
  import StepTransition from './StepTransition.svelte';

  type Scope = 'main' | 'cookie';
  type Problem = { scope: Scope; text: string } | null;

  const LIMITED = new Set(['too_many_attempts', 'rate_limited', 'busy']);
  const UNREACHABLE = new Set(['apple_unreachable', 'network_error', 'bad_response']);
  const MESSAGE_MAX = 200;
  const MSG = {
    unreachable: "Can't reach Apple right now. Try again in a moment.",
    generic: 'Something went wrong. Try again.',
    loginFailed: "Apple didn't accept those details. Check your Apple ID and password.",
    expired: 'That sign-in took too long. Enter your password again.',
    resent: 'A new code is on its way.',
    resendLimit: 'Too many new codes for this sign-in. Go back and sign in again.',
    badCookie: "That cookie didn't work. Copy it again from your browser.",
    tooLong: "That's too long to be a cookie header."
  };

  let step = $state<1 | 2 | 3>(1);
  let direction = $state<'forward' | 'back'>('forward');
  let appleId = $state('');
  let password = $state('');
  let china = $state(false);
  let flowId = $state('');
  let busy = $state(false);
  let problem = $state<Problem>(null);
  let done = $state<{ scope: Scope; text: string } | null>(null);
  let limited = $state<Scope | null>(null); // survives navigation; only the countdown clears it
  let remaining = $state(0);
  let announced = $state(0); // fixed when the limit hits; read once by screen readers
  let codeError = $state(0);
  let resendDone = $state(false); // the server refused another resend for this sign-in

  let cookieOpen = $state(false);
  let cookie = $state('');

  let idInput: HTMLInputElement | undefined = $state();
  let pwInput: HTMLInputElement | undefined = $state();
  let code: CodeInput | undefined = $state();
  let cookieInput: HTMLTextAreaElement | undefined = $state();
  let root: HTMLElement | undefined = $state();
  let limitScope: Scope = 'main';
  let alive = true;

  const countdown = createCountdown(
    (n) => {
      remaining = n;
    },
    () => {
      limited = null;
      remaining = 0;
      done = { scope: limitScope, text: 'You can try again now.' };
      const a = document.activeElement;
      if (!a || a === document.body || root?.contains(a)) {
        if (step === 2) pwInput?.focus();
        else if (step === 3) code?.clear();
        else idInput?.focus();
      }
    }
  );

  const reason = $derived($sessionStore.state === 'reconnect' ? $sessionStore.reason : null);
  const banner = $derived(
    reason === 'session_expired'
      ? 'Your Apple session expired. Reconnect to continue.'
      : reason === 'session_unreadable'
        ? "AliasFox can't read the stored Apple session (the server key may have changed). Reconnect to continue."
        : reason !== null
          ? 'Reconnect to continue.'
          : ''
  );
  const locked = $derived(limited !== null && remaining > 0);
  const truncated = $derived(cookie.includes('…') || cookie.includes('...'));

  const plural = (n: number): string => `${n} ${n === 1 ? 'second' : 'seconds'}`;
  function alertText(scope: Scope): string {
    if (limited === scope) return `Too many attempts. Try again in about ${plural(announced)}.`;
    if (!problem || problem.scope !== scope) return '';
    return problem.text;
  }

  onMount(() => {
    idInput?.focus();
  });
  onDestroy(() => {
    alive = false;
    countdown.stop();
  });

  function go(to: 1 | 2 | 3): void {
    if (to === 3) {
      codeError = 0; // a fresh code screen never inherits an old wrong-code state
      resendDone = false;
      cookie = ''; // the cookie panel is hidden on this step: never keep unsent cookie text around
    }
    if (to === 1) password = '';
    direction = to > step ? 'forward' : 'back';
    step = to;
    problem = null;
    done = null;
    void tick().then(() => {
      if (to === 1) idInput?.focus();
      else if (to === 2) pwInput?.focus();
      else code?.clear();
    });
  }

  function say(scope: Scope, text: string): void {
    done = null;
    problem = { scope, text };
  }

  function limit(scope: Scope, err: ApiError): void {
    limitScope = scope;
    done = null;
    problem = null;
    limited = scope;
    announced = countdown.start(err.retryAfter ?? 30);
  }

  function capped(msg: string | undefined): string {
    return msg ? Array.from(msg).slice(0, MESSAGE_MAX).join('') : '';
  }

  // Maps a failed request to a message or state change; not_authenticated is handed to the session store.
  function handle(err: unknown, scope: Scope): void {
    const e = err instanceof ApiError ? err : new ApiError(0, 'network_error');
    const d = e.detail;
    if (d === 'not_authenticated') {
      sessionStore.onApiError(e);
      return;
    }
    if (d === 'resend_limit') {
      resendDone = true;
      say('main', MSG.resendLimit);
      return;
    }
    if (LIMITED.has(d)) {
      limit(scope, e);
      if (step === 3) code?.clear();
      return;
    }
    if (scope === 'cookie') {
      if (d === 'invalid_cookie') say('cookie', capped(e.serverMessage) || MSG.badCookie);
      else if (e.status === 413 || d === 'payload_too_large') say('cookie', MSG.tooLong);
      else if (UNREACHABLE.has(d)) say('cookie', MSG.unreachable);
      else if (d === 'apple_error') say('cookie', `Apple returned an error. ${capped(e.serverMessage)}`.trim());
      else say('cookie', MSG.generic);
      return;
    }
    if (d === 'apple_login_failed') {
      if (step !== 2) go(2);
      say('main', MSG.loginFailed);
      void tick().then(() => pwInput?.focus());
    } else if (d === 'flow_expired') {
      flowId = '';
      if (step !== 2) go(2);
      say('main', MSG.expired);
      void tick().then(() => pwInput?.focus());
    } else if (d === 'invalid_code' && step === 3) {
      problem = null;
      codeError += 1;
    } else if (UNREACHABLE.has(d)) {
      say('main', MSG.unreachable);
      if (step === 3) code?.clear();
    } else if (d === 'apple_error') {
      say('main', `Apple returned an error. ${capped(e.serverMessage)}`.trim());
      if (step === 3) code?.clear();
    } else {
      say('main', MSG.generic);
      if (step === 3) code?.clear();
    }
  }

  async function succeed(scope: Scope): Promise<void> {
    toasts.push({ kind: 'ok', text: 'Apple account connected' });
    try {
      await sessionStore.markReady();
    } catch (err) {
      if (alive) handle(err, scope);
    }
  }

  function nextFromId(e: SubmitEvent): void {
    e.preventDefault();
    if (locked || appleId.trim() === '') return;
    appleId = appleId.trim();
    go(2);
  }

  async function submitPassword(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    if (busy || locked || password === '') return;
    busy = true;
    problem = null;
    done = null;
    let outcome: 'ok' | 'code' | 'failed' = 'failed';
    let failure: unknown;
    try {
      const body = { apple_id: appleId, password, ...(china ? { china: true } : {}) };
      const res = await api.connectLogin(body);
      if (res.status === 'needs_2fa') {
        flowId = res.flow_id;
        outcome = 'code';
      } else {
        outcome = 'ok';
      }
    } catch (err) {
      failure = err;
    } finally {
      password = '';
      busy = false;
    }
    if (!alive) return;
    if (outcome === 'ok') await succeed('main');
    else if (outcome === 'code') go(3);
    else handle(failure, 'main');
  }

  async function submitCode(value: string): Promise<void> {
    if (busy || locked || flowId === '') return;
    busy = true;
    problem = null;
    done = null;
    let ok = false;
    let failure: unknown;
    try {
      await api.connectVerify({ flow_id: flowId, code: value });
      ok = true;
    } catch (err) {
      failure = err;
    } finally {
      busy = false;
    }
    if (!alive) return;
    if (ok) await succeed('main');
    else handle(failure, 'main');
  }

  async function resendCode(): Promise<void> {
    if (busy || locked || resendDone || flowId === '') return;
    busy = true;
    problem = null;
    done = null;
    let ok = false;
    let failure: unknown;
    try {
      await api.connectResend({ flow_id: flowId });
      ok = true;
    } catch (err) {
      failure = err;
    } finally {
      busy = false;
    }
    if (!alive) return;
    if (ok) {
      code?.clear();
      done = { scope: 'main', text: MSG.resent };
    } else handle(failure, 'main');
  }

  async function submitCookie(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    if (busy || locked || cookie.trim() === '') return;
    busy = true;
    problem = null;
    done = null;
    let ok = false;
    let failure: unknown;
    try {
      await api.connectCookie(cookie.trim());
      ok = true;
    } catch (err) {
      failure = err;
    } finally {
      cookie = '';
      busy = false;
    }
    if (!alive) return;
    if (ok) await succeed('cookie');
    else handle(failure, 'cookie');
  }

  function toggleCookie(): void {
    cookieOpen = !cookieOpen;
    if (!cookieOpen) cookie = '';
    problem = problem?.scope === 'cookie' ? null : problem;
    if (cookieOpen) void tick().then(() => cookieInput?.focus());
  }
</script>

{#snippet messages(scope: Scope)}
  <div class="messages">
    <p
      class="error"
      class:visually-hidden={limited === scope}
      role="alert"
    >{alertText(scope)}</p>
    {#if limited === scope}
      <p class="error countdown" aria-hidden="true">
        Too many attempts. Try again in {plural(remaining)}.
      </p>
    {/if}
    <p class="visually-hidden" role="status" aria-live="polite">{done?.scope === scope ? done.text : ''}</p>
  </div>
{/snippet}

<section class="screen connect" data-screen="connect" tabindex="-1" aria-label="Connect your Apple account" bind:this={root}>
  <div class="brand"><span class="mark" aria-hidden="true"></span><span>AliasFox</span></div>

  {#if banner}
    <p class="banner">{banner}</p>
  {/if}

  <StepTransition {step} {direction}>
    <div class="stage">
      {#if step === 1}
        <h2>Connect your Apple account</h2>
        <p class="lede">AliasFox signs in to iCloud to create Hide My Email addresses for you.</p>
        <form onsubmit={nextFromId} novalidate>
          <label for="connect-apple-id">Apple ID</label>
          <input
            id="connect-apple-id"
            bind:this={idInput}
            bind:value={appleId}
            type="email"
            autocomplete="username"
            inputmode="email"
            autocapitalize="off"
            spellcheck="false"
            required
          />
          {@render messages('main')}
          <button type="submit" class="primary" disabled={locked}>Continue</button>
        </form>
      {:else if step === 2}
        <h2>Enter your password</h2>
        <form onsubmit={submitPassword}>
          <input
            class="visually-hidden"
            type="text"
            value={appleId}
            readonly
            tabindex="-1"
            aria-hidden="true"
            autocomplete="username"
          />
          <p class="lede who">{appleId}</p>
          <label for="connect-password">Password</label>
          <input
            id="connect-password"
            bind:this={pwInput}
            bind:value={password}
            type="password"
            autocomplete="current-password"
            readonly={busy}
          />
          <label class="check">
            <input type="checkbox" bind:checked={china} />
            <span>My Apple ID is for mainland China</span>
          </label>
          {@render messages('main')}
          <div class="actions">
            <button type="button" disabled={busy} onclick={() => !busy && go(1)}>Back</button>
            <button type="submit" class="primary" disabled={busy || locked}>
              {busy ? 'Signing in…' : 'Continue'}
            </button>
          </div>
        </form>
      {:else}
        <h2>Enter the verification code</h2>
        <p class="lede">Approve the sign-in on your Apple devices, then enter the 6-digit code they show.</p>
        <CodeInput bind:this={code} onComplete={submitCode} error={codeError} disabled={busy || locked} />
        {@render messages('main')}
        <div class="actions">
          <button type="button" disabled={busy} onclick={() => !busy && go(2)}>Back</button>
          <button type="button" disabled={busy || locked || resendDone} onclick={() => void resendCode()}>
            Resend code
          </button>
        </div>
      {/if}
    </div>
  </StepTransition>

  {#if step !== 3}
    <div class="cookie">
      <button
        type="button"
        class="quiet"
        aria-expanded={cookieOpen}
        aria-controls="connect-cookie-panel"
        onclick={toggleCookie}
      >
        <span class="chev" aria-hidden="true"></span>Use a cookie instead
      </button>
      {#if cookieOpen}
        <div id="connect-cookie-panel" class="panel">
          <p>
            A pasted cookie is your full iCloud web session. Anyone who has it can use your iCloud account.
            AliasFox stores it encrypted, but prefer the password option if you can.
          </p>
          <p class="muted">
            In your browser's DevTools → Network → click a request to icloud.com → Headers → right-click the
            cookie header → Copy value.
          </p>
          <form onsubmit={submitCookie}>
            <label for="connect-cookie">Cookie header</label>
            <textarea
              id="connect-cookie"
              bind:this={cookieInput}
              bind:value={cookie}
              rows="4"
              spellcheck="false"
              autocomplete="off"
              autocapitalize="off"
              autocorrect="off"
            ></textarea>
            {#if truncated}
              <p class="hint">This looks truncated — copy the cookie header again using Copy value.</p>
            {/if}
            {@render messages('cookie')}
            <button type="submit" class="primary" disabled={busy || locked || cookie.trim() === ''}>
              Connect with cookie
            </button>
          </form>
        </div>
      {/if}
    </div>
  {/if}

  <button type="button" class="quiet" onclick={() => void sessionStore.logout()}>Log out</button>
</section>

<style>
  .stage {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }
  form {
    width: 100%;
    display: flex;
    flex-direction: column;
  }
  form > :global(button.primary) {
    margin-top: var(--space-5);
  }
  .lede {
    color: var(--muted);
    margin-top: calc(var(--space-2) * -1);
  }
  .who {
    margin: 0 0 var(--space-4);
    color: var(--text);
    overflow-wrap: anywhere;
  }
  .banner {
    width: 100%;
    padding: var(--space-3) var(--space-4);
    border: 1px solid color-mix(in srgb, var(--accent) 45%, var(--border));
    border-radius: var(--radius);
    background: var(--surface);
  }
  .messages {
    margin: var(--space-2) 0 0;
    min-height: 1.5em;
  }
  .error {
    margin: 0;
    color: var(--danger);
    font-size: 0.9375rem;
    overflow-wrap: anywhere;
  }
  .check {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    min-height: 44px;
    margin: var(--space-3) 0 0;
    cursor: pointer;
  }
  .check input {
    width: 1.125rem;
    min-height: 0;
    height: 1.125rem;
    margin: 0;
    accent-color: var(--accent);
  }
  .actions {
    display: flex;
    gap: var(--space-3);
    margin-top: var(--space-5);
  }
  .actions button {
    flex: 0 0 auto;
  }
  .actions :global(button.primary) {
    flex: 1;
  }
  .cookie {
    width: 100%;
  }
  .quiet {
    border-color: transparent;
    background: transparent;
    color: var(--muted);
    padding: 0 var(--space-2);
    margin-left: calc(var(--space-2) * -1);
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
  }
  .quiet:hover:not(:disabled) {
    border-color: transparent;
    color: var(--text);
  }
  .chev {
    width: 0.5rem;
    height: 0.5rem;
    border-right: 2px solid currentColor;
    border-bottom: 2px solid currentColor;
    transform: rotate(-45deg);
    transition: transform var(--dur) var(--ease);
  }
  [aria-expanded='true'] > .chev {
    transform: rotate(45deg);
  }
  .panel {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    margin-top: var(--space-3);
    padding: var(--space-4);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
    font-size: 0.9375rem;
  }
  textarea {
    width: 100%;
    padding: var(--space-3);
    border: 1px solid var(--field-border);
    border-radius: var(--radius);
    background: var(--bg);
    color: inherit;
    font-family: var(--font-mono);
    font-size: 0.8125rem;
    resize: vertical;
  }
  .hint {
    margin-top: var(--space-2);
    color: var(--accent);
    font-size: 0.9375rem;
  }
</style>
