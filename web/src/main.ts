import { mount } from 'svelte';
import './styles/base.css';
import App from './App.svelte';
import { isDemo } from './lib/demoMode';

const target = document.getElementById('app');
if (!target) throw new Error('missing #app root');
const root = target;

// /demo swaps the API for in-browser sample data before anything boots; the demo code is a
// separate chunk that normal visits never load.
async function start(): Promise<ReturnType<typeof mount>> {
  if (isDemo) (await import('./lib/demo')).installDemo();
  return mount(App, { target: root });
}

export default start();
