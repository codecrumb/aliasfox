/// <reference types="svelte" />
/// <reference types="vite/client" />

export {};

// `autocorrect` is a real (WebKit) attribute that Svelte's element typings omit.
declare module 'svelte/elements' {
  interface HTMLTextareaAttributes {
    autocorrect?: 'on' | 'off' | null | undefined;
  }
}
