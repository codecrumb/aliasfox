<script lang="ts">
  import { onMount } from 'svelte';
  import Login from './components/Login.svelte';
  import StepTransition from './components/StepTransition.svelte';
  import Toasts from './components/Toasts.svelte';
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

  const screen = $derived($sessionStore.state);
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

  onMount(() => {
    void sessionStore.boot();
  });
</script>

<h1 class="visually-hidden" aria-live="polite">{title}</h1>

<main class="shell">
  <StepTransition {step} {direction}>
    {#if screen === 'loading'}
      <section class="screen splash">
        <div class="brand"><span class="mark" aria-hidden="true"></span><span>AliasFox</span></div>
      </section>
    {:else if screen === 'login'}
      <Login />
    {:else if screen === 'connect' || screen === 'reconnect'}
      <section class="screen">
        <p>Connect your Apple account — coming in the next task</p>
        <button type="button" onclick={() => void sessionStore.logout()}>Log out</button>
      </section>
    {:else if screen === 'ready'}
      <section class="screen">
        <p>Palette — coming soon</p>
        <button type="button" onclick={() => void sessionStore.logout()}>Log out</button>
      </section>
    {:else}
      <section class="screen">
        <p>Something went wrong.</p>
        <p class="muted">{$sessionStore.errorText ?? ''}</p>
        <button type="button" class="primary" onclick={() => void sessionStore.boot()}>Retry</button>
      </section>
    {/if}
  </StepTransition>
</main>

<Toasts />
