<script lang="ts">
  import { onMount } from 'svelte';
  import ConnectApple from './components/ConnectApple.svelte';
  import Login from './components/Login.svelte';
  import Palette from './components/Palette.svelte';
  import StepTransition from './components/StepTransition.svelte';
  import Toasts from './components/Toasts.svelte';
  import { aliasStore } from './lib/aliases';
  import { sessionStore } from './lib/session';

  const STEPS: Record<string, number> = {
    loading: 0,
    error: 0,
    login: 1,
    connect: 2,
    reconnect: 2,
    ready: 3
  };
  const TITLES: Record<string, string> = {
    loading: 'Loading AliasFox',
    login: 'Log in to AliasFox',
    connect: 'Connect your Apple account',
    reconnect: 'Reconnect your Apple account',
    ready: 'AliasFox',
    error: 'Something went wrong'
  };

  const sessionState = $derived($sessionStore.state);

  // Once the palette has been shown, an Apple session expiry keeps it mounted (stale data,
  // read-only, with a reconnect banner) so open dialogs keep what was typed. A cold start into
  // reconnect with nothing to show gets the full-screen connect flow.
  let paletteSeen = $state(false);
  $effect.pre(() => {
    if (sessionState === 'ready') paletteSeen = true;
    else if (sessionState !== 'reconnect') paletteSeen = false;
  });
  const showPalette = $derived(
    sessionState === 'ready' || (sessionState === 'reconnect' && (paletteSeen || $aliasStore.list.length > 0))
  );
  const screen = $derived(showPalette ? 'ready' : sessionState);
  const step = $derived(STEPS[screen] ?? 0);
  const title = $derived(TITLES[screen] ?? 'AliasFox');

  let direction = $state<'forward' | 'back'>('forward');
  let prevStep = 0;
  $effect.pre(() => {
    if (step !== prevStep) {
      direction = step > prevStep ? 'forward' : 'back';
      prevStep = step;
    }
  });

  let host: HTMLElement | undefined;
  let firstScreen = true;
  // After a screen swap, hand keyboard focus to the new screen unless the user is already
  // focused somewhere real (inside the new screen, or e.g. an open dialog).
  $effect(() => {
    void screen;
    if (firstScreen) {
      firstScreen = false;
      return;
    }
    const a = document.activeElement;
    if (a && a !== document.body && a !== document.documentElement) return;
    host?.querySelector<HTMLElement>('[data-screen]')?.focus({ preventScroll: true });
  });

  onMount(() => {
    void sessionStore.boot();
  });
</script>

<h1 class="visually-hidden" aria-live="polite">{title}</h1>

<main class="shell" bind:this={host}>
  <StepTransition {step} {direction}>
    {#if screen === 'loading'}
      <section class="screen splash" data-screen="loading" tabindex="-1" aria-label="Loading">
        <div class="brand"><span class="mark" aria-hidden="true"></span><span>AliasFox</span></div>
      </section>
    {:else if screen === 'login'}
      <Login />
    {:else if screen === 'connect' || screen === 'reconnect'}
      <ConnectApple />
    {:else if screen === 'ready'}
      <Palette />
    {:else}
      <section class="screen" data-screen="error" tabindex="-1" aria-label="Error">
        <p>Something went wrong.</p>
        <p class="muted">{$sessionStore.errorText ?? ''}</p>
        <button type="button" class="primary" onclick={() => void sessionStore.boot()}>Retry</button>
      </section>
    {/if}
  </StepTransition>
</main>

<Toasts />
