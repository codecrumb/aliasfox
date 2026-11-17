import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

export default {
  preprocess: vitePreprocess(),
  // CSS ships as a file: no runtime <style> injection (CSP style-src 'self').
  compilerOptions: { css: 'external' }
};
