import js from '@eslint/js';
import globals from 'globals';
import svelte from 'eslint-plugin-svelte';
import tseslint from 'typescript-eslint';

// Defence in depth only: these lint bans catch the obvious ways to turn strings into markup or
// code. They are NOT a security boundary (aliases, destructuring and exotic APIs can slip past
// static selectors). The Content-Security-Policy (script-src 'self'; style-src 'self') served by
// the backend is the backstop. Computed-key assignment (`el[k] = v`) is banned in src because it
// can reach innerHTML; use a Map/array method, or a literal key, for dynamic data.
const HTML_SINKS = ['innerHTML', 'outerHTML', 'srcdoc'];
const SINK_RE = `/^(${HTML_SINKS.join('|')})$/`;
const UNSAFE_METHODS =
  '/^(insertAdjacentHTML|setHTMLUnsafe|parseHTMLUnsafe|createContextualFragment|parseFromString|execCommand)$/';
const BAD_ATTRS = '/^(on.*|srcdoc|href|src|action|formaction)$/i';

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
    selector: 'AssignmentExpression > MemberExpression.left[computed=true]:not([property.type="Literal"])',
    message:
      'Assignment through a computed key can reach innerHTML; use textContent, a literal key, or a Map.'
  },
  {
    selector: `Property[key.name=${SINK_RE}]`,
    message: 'innerHTML/outerHTML/srcdoc keys are banned (text-only rendering). Use textContent.'
  },
  {
    selector: `Property[key.value=${SINK_RE}]`,
    message: 'innerHTML/outerHTML/srcdoc keys are banned (text-only rendering). Use textContent.'
  },
  {
    selector: `MemberExpression[property.name=${UNSAFE_METHODS}]`,
    message: 'HTML-parsing/injection APIs are banned (text-only rendering). Use textContent.'
  },
  {
    selector: `MemberExpression[property.value=${UNSAFE_METHODS}]`,
    message: 'HTML-parsing/injection APIs are banned (text-only rendering). Use textContent.'
  },
  {
    selector: "MemberExpression[object.name='document'][property.name=/^(write|writeln)$/]",
    message: 'document.write/writeln are banned.'
  },
  {
    selector: "MemberExpression[object.name='document'][property.value=/^(write|writeln)$/]",
    message: 'document.write/writeln are banned.'
  },
  {
    selector: `CallExpression[callee.property.name=/^setAttribute(NS)?$/][arguments.0.value=${BAD_ATTRS}]`,
    message: 'setAttribute of on*/srcdoc/href/src/action/formaction is banned.'
  },
  {
    selector: 'CallExpression[callee.property.name=/^setAttribute(NS)?$/][arguments.0.type!="Literal"]',
    message: 'setAttribute needs a literal attribute name (so it can be checked).'
  },
  {
    selector:
      "AssignmentExpression[left.property.name=/^(href|src)$/][right.type='Literal'][right.value=/^\\s*(javascript:|data:text\\/html)/i]",
    message: 'javascript:/data:text/html URLs are banned.'
  },
  {
    selector:
      "CallExpression[callee.property.name='createElement'][arguments.0.value=/^(script|style|iframe|object|embed)$/i]",
    message: 'Creating script/style/iframe/object/embed elements is banned.'
  },
  {
    selector: "CallExpression[callee.property.name='createElement'][arguments.0.type!='Literal']",
    message: 'createElement needs a literal tag name (so it can be checked).'
  },
  {
    selector:
      "MemberExpression[property.name='eval']",
    message: 'eval is banned.'
  },
  {
    selector:
      "MemberExpression[property.value='eval']",
    message: 'eval is banned.'
  },
  {
    selector: "CallExpression[callee.name='Function']",
    message: 'Function() is eval.'
  },
  {
    selector:
      "CallExpression[callee.name=/^(setTimeout|setInterval)$/][arguments.0.type=/^(Literal|TemplateLiteral|BinaryExpression)$/]",
    message: 'setTimeout/setInterval must receive a function, not a string.'
  },
  {
    selector:
      "CallExpression[callee.property.name=/^(setTimeout|setInterval)$/][arguments.0.type=/^(Literal|TemplateLiteral|BinaryExpression)$/]",
    message: 'setTimeout/setInterval must receive a function, not a string.'
  },
  {
    selector: "SvelteElement[name.name='svelte:element']",
    message: '<svelte:element> (dynamic tag names) is banned.'
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
