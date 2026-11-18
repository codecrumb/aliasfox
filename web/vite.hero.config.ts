import { defineConfig } from 'vite';

// One self-contained ES module for the public landing page; not part of the SPA bundle.
export default defineConfig({
  publicDir: false,
  build: {
    outDir: '../src/aliasfox/page_templates',
    emptyOutDir: false,
    sourcemap: false,
    lib: { entry: 'hero3d/main.js', formats: ['es'], fileName: () => 'hero3d.js' }
  }
});
