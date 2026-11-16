import { expect, test } from 'vitest';

test('crypto.subtle and indexedDB exist in the test environment', () => {
  expect(globalThis.crypto.subtle).toBeDefined();
  expect(globalThis.indexedDB).toBeDefined();
});
