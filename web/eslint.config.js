import js from '@eslint/js';
import globals from 'globals';
import svelte from 'eslint-plugin-svelte';
import tseslint from 'typescript-eslint';

const HTML_SINKS = ['innerHTML', 'outerHTML'];

const restrictedSyntax = [
  ...HTML_SINKS.flatMap((name) => [
    {
      selector: `AssignmentExpression > MemberExpression.left[property.name='${name}']`,
      message: `Assigning ${name} is banned (text-only rendering). Use textContent or Svelte text.`
    },
    {
      selector: `AssignmentExpression > MemberExpression.left[property.value='${name}']`,
      message: `Assigning ${name} is banned (text-only rendering). Use textContent or Svelte text.`
    }
  ]),
  {
    selector: "MemberExpression[property.name='insertAdjacentHTML']",
    message: 'insertAdjacentHTML is banned (text-only rendering).'
  },
  {
    selector: "MemberExpression[property.value='insertAdjacentHTML']",
    message: 'insertAdjacentHTML is banned (text-only rendering).'
  },
  {
    selector:
      "MemberExpression[object.name='document'][property.name=/^(write|writeln)$/]",
    message: 'document.write/writeln are banned.'
  },
  {
    selector: "MemberExpression[object.name='document'][property.value=/^(write|writeln)$/]",
    message: 'document.write/writeln are banned.'
  }
];

export default tseslint.config(
  { ignores: ['node_modules/**', 'dist/**', '../src/aliasfox/static/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...svelte.configs['flat/recommended'],
  {
    languageOptions: { globals: { ...globals.browser, ...globals.node } }
  },
  {
    files: ['**/*.svelte', '**/*.svelte.ts'],
    languageOptions: { parserOptions: { parser: tseslint.parser } }
  },
  {
    files: ['src/**/*.{ts,js,svelte}'],
    rules: {
      'svelte/no-at-html-tags': 'error',
      'no-restricted-syntax': ['error', ...restrictedSyntax],
      'no-eval': 'error',
      'no-implied-eval': 'error',
      'no-new-func': 'error'
    }
  }
);
