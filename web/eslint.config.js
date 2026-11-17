import js from '@eslint/js';
import globals from 'globals';
import svelte from 'eslint-plugin-svelte';
import tseslint from 'typescript-eslint';

// Defence in depth only: these lint bans catch the obvious ways to turn strings into markup or
// code. They are NOT a security boundary (aliases, destructuring and exotic APIs can slip past
// static selectors). The Content-Security-Policy (script-src 'self'; style-src 'self') served by
// the backend is the backstop. Computed-key assignment is banned only when the key is dynamically
// built (concatenation with strings, templates, calls, conditionals, ...). `el[key] = v` with an
// Identifier key is a KNOWN best-effort gap, backed by the CSP and by the property/setAttribute bans.
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
  ...[
    "[property.type=/^(CallExpression|ConditionalExpression|LogicalExpression|SequenceExpression)$/]",
    "[property.type='TemplateLiteral'][property.expressions.length>0]",
    "[property.type='TemplateLiteral'][property.quasis.0.value.raw=/^(innerHTML|outerHTML|srcdoc|insertAdjacentHTML)$/]",
    "[property.type='BinaryExpression'][property.operator='+'][property.left.raw=/^[\"']/]",
    "[property.type='BinaryExpression'][property.operator='+'][property.right.raw=/^[\"']/]",
    "[property.type='BinaryExpression'][property.operator='+']:matches([property.left.type='TemplateLiteral'], [property.right.type='TemplateLiteral'])",
    "[property.value=/^(innerHTML|outerHTML|srcdoc|insertAdjacentHTML)$/]"
  ].map((cond) => ({
    selector: `AssignmentExpression > MemberExpression.left[computed=true]${cond}`,
    message:
      'Assignment through a dynamically built computed key can reach innerHTML; use textContent or a plain key.'
  })),
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
    selector: `CallExpression[callee.property.name='setAttribute'][arguments.0.value=${BAD_ATTRS}]`,
    message: 'setAttribute of on*/srcdoc/href/src/action/formaction is banned.'
  },
  {
    selector: `CallExpression[callee.property.name='setAttributeNS'][arguments.1.value=${BAD_ATTRS}]`,
    message: 'setAttributeNS of on*/srcdoc/href/src/action/formaction is banned.'
  },
  {
    selector: "CallExpression[callee.property.name='setAttribute'][arguments.0.type!='Literal']",
    message: 'setAttribute needs a literal attribute name (so it can be checked).'
  },
  {
    selector: "CallExpression[callee.property.name='setAttributeNS'][arguments.1.type!='Literal']",
    message: 'setAttributeNS needs a literal attribute name (so it can be checked).'
  },
  {
    selector:
      "AssignmentExpression[left.property.name=/^(href|src)$/][right.type='Literal'][right.value=/^\\s*(javascript:|data:text\\/html)/i]",
    message: 'javascript:/data:text/html URLs are banned.'
  },
  {
    selector:
      "AssignmentExpression[left.property.value=/^(href|src)$/][right.type='Literal'][right.value=/^\\s*(javascript:|data:text\\/html)/i]",
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
      "CallExpression[callee.property.name='createElementNS'][arguments.1.value=/^(script|style|iframe|object|embed)$/i]",
    message: 'Creating script/style/iframe/object/embed elements is banned.'
  },
  {
    selector: "CallExpression[callee.property.name='createElementNS'][arguments.1.type!='Literal']",
    message: 'createElementNS needs a literal tag name (so it can be checked).'
  },
  {
    selector: ":matches(CallExpression, NewExpression)[callee.property.name='Function']",
    message: 'window.Function()/globalThis.Function() is eval.'
  },
  {
    selector:
      "CallExpression[callee.type='SequenceExpression']:matches([callee.expressions.1.name='eval'], [callee.expressions.2.name='eval'])",
    message: 'Indirect eval is banned.'
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
    selector: ":matches(NewExpression, CallExpression)[callee.property.value='Function']",
    message: "globalThis['Function'] is eval."
  },
  {
    selector: "NewExpression[callee.name='Function']",
    message: 'new Function() is eval.'
  },
  // Best effort: these write arbitrary properties (innerHTML, srcdoc, on*) onto whatever object
  // they are given, which could be a DOM node. Object.assign is allowed only into an object literal.
  ...['assign', 'defineProperty', 'defineProperties'].flatMap((m) => {
    const guard = m === 'assign' ? ':not([arguments.0.type=\'ObjectExpression\'])' : '';
    return [
      {
        selector: `CallExpression[callee.object.name='Object'][callee.property.name='${m}']${guard}`,
        message: `Object.${m} on a non-literal target can write innerHTML/srcdoc onto a node; assign explicit safe properties instead.`
      },
      {
        selector: `CallExpression[callee.object.name='Object'][callee.property.value='${m}']${guard}`,
        message: `Object.${m} on a non-literal target can write innerHTML/srcdoc onto a node; assign explicit safe properties instead.`
      }
    ];
  }),
  {
    selector: "CallExpression[callee.object.name='Reflect'][callee.property.name=/^(set|defineProperty)$/]",
    message: 'Reflect.set/defineProperty can write innerHTML/srcdoc onto a node; assign explicit safe properties instead.'
  },
  {
    selector: "CallExpression[callee.object.name='Reflect'][callee.property.value=/^(set|defineProperty)$/]",
    message: 'Reflect.set/defineProperty can write innerHTML/srcdoc onto a node; assign explicit safe properties instead.'
  },
  {
    selector: "CallExpression[callee.name='Function']",
    message: 'Function() is eval.'
  },
  ...[
    "CallExpression[callee.name=/^(setTimeout|setInterval)$/]",
    "CallExpression[callee.property.name=/^(setTimeout|setInterval)$/]"
  ].map((call) => ({
    selector: `${call}:not([arguments.0.type=/^(ArrowFunctionExpression|FunctionExpression|Identifier|MemberExpression|TSNonNullExpression)$/])`,
    message:
      'setTimeout/setInterval must receive a function (arrow, function, identifier or member), never a computed string.'
  })),
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
