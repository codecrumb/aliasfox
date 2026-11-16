import { render, screen } from '@testing-library/svelte';
import { expect, test } from 'vitest';
import App from '../src/App.svelte';

test('renders the app name', () => {
  render(App);
  expect(screen.getByRole('heading', { name: 'AliasFox' })).toBeInTheDocument();
});

test('crypto.subtle and indexedDB exist in the test environment', () => {
  expect(globalThis.crypto.subtle).toBeDefined();
  expect(globalThis.indexedDB).toBeDefined();
});
