# AliasFox UI Implementation Plan (plan 2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. UI tasks (4, 5, 7) must also load the `frontend-design` skill before writing markup/CSS.

**Goal:** A minimal, dark, keyboard-first single-page app, served by the same FastAPI service, for logging in, connecting an Apple account, and searching/generating/editing Hide My Email aliases.

**Architecture:** A Svelte + TypeScript + Vite app in `web/` is built into `src/aliasfox/static/` (git-ignored) by a Node stage in the Dockerfile; FastAPI serves it at `/` with an SPA fallback. The browser keeps the alias list in an AES-GCM-encrypted IndexedDB cache (key from a new `GET /api/session`), renders the cache first, refreshes in the background, and searches a client-side in-memory index. All Apple traffic stays server-side.

**Tech Stack:** Svelte 5, TypeScript (strict), Vite, Vitest + @testing-library/svelte + jsdom + fake-indexeddb, `@leeoniya/ufuzzy` (search), ESLint with a ban on `{@html}`/`innerHTML`. Backend additions use the existing FastAPI app.

**Spec:** `docs/superpowers/specs/2026-11-16-aliasfox-design.md` (sections 4.5, 4.6, 10, 10.1). Plan 1 (core service) is merged; plan 3 (multi-user, Neon Auth, TOTP/passkeys, Alembic) comes after.

## Global Constraints

- Dark theme only; minimal; keyboard-first (spec 4.6). `prefers-reduced-motion` disables slide/shake (colour change only).
- Everything from Apple or the user (labels, notes, domains, app names, error messages) is untrusted text: render only as text nodes. No `{@html}`, no `innerHTML`, no inline event-handler attributes, no third-party scripts or fonts, no inline `<script>`, no `style="..."` attributes in markup (the CSP is `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'`; CSSOM via JS such as `el.style.setProperty` is allowed).
- Aliases are never sent anywhere except `/api/*` on the same origin; the browser cache is encrypted with AES-GCM using the key from `/api/session`; logout deletes the IndexedDB cache and the in-memory key.
- API contract: JSON; errors carry `detail`; clients MUST branch on `detail`, not the HTTP status (401 is overloaded: `not_authenticated`, `session_expired`, `apple_login_failed`, `invalid_code`). Codes: 409 `not_connected`, 409 `session_unreadable`, 410 `flow_expired`, 429 `rate_limited`/`too_many_attempts`/`busy` (+Retry-After), 502 `apple_error`/`apple_unreachable`, 413 `payload_too_large`, 422 validation.
- Alias JSON fields (snake_case): `anonymous_id, hme, label, note, is_active, forward_to, origin, domain, created_ms, origin_app_name, app_bundle_id`. Origins seen live: `IN_APP, MAIL, ON_DEMAND, SAFARI`.
- Targets: typing in search stays under 50 ms per keystroke at 10,000 aliases (index query only, measured in a test); the list renders a window of rows, never 10,000 DOM nodes; first paint from cache before the network answers.
- Shortcuts use `event.code` (my call, owner may change): `Enter` copy address; `Shift+Enter` toggle active; `Alt+N` new alias; `Alt+E` edit label/note; `Alt+Backspace` delete (confirm: Enter confirms, Esc cancels); `Ctrl/Cmd+K` actions menu; `↑/↓` or `Ctrl+J/K` move; `Esc` clear search/close; `?` shortcut help (when search is empty). Search is always focused.
- Multiple Apple accounts and object storage are out of scope (owner decision); leave an `AccountSwitcher` slot out of the DOM, do not build it.
- Commits have no authorship trailers.

## Review Focus

1. Hostile strings: a label/note/domain/app name like `<img src=x onerror=alert(1)>`, `"><script>`, 10,000 characters, RTL text, zero-width characters and emoji render as inert text and never break layout or the keyboard map.
2. 10,000 aliases: typing, arrow navigation and filtering never block (>50 ms) and DOM row count stays bounded.
3. Session expiry mid-use (`session_expired` / `not_authenticated`): the app moves to the right screen (reconnect / login) without losing what the user typed, and without showing stale data as if fresh.
4. Cache hygiene: logout wipes IndexedDB and the key; a cache written under a different key (epoch bump, rotated secret) fails to decrypt and is silently discarded and refetched; corrupted cache never throws to the user.
5. Double actions and denied permissions: double Enter on generate/reserve creates one alias; clipboard write denied falls back to a visible selectable field with a clear message; Apple `apple_unreachable`/`rate_limited` show a retryable message, not a blank screen.

---

## File Structure

```
web/package.json, vite.config.ts, tsconfig.json, svelte.config.js, eslint.config.js, index.html, vitest.setup.ts
web/src/main.ts, App.svelte
web/src/lib/api.ts            typed fetch client + ApiError
web/src/lib/types.ts          Alias, errors, API shapes
web/src/lib/session.ts        auth/connection state machine (store)
web/src/lib/crypto.ts         AES-GCM helpers for the cache
web/src/lib/cache.ts          encrypted IndexedDB alias cache
web/src/lib/search.ts         ufuzzy index + query
web/src/lib/aliases.ts        alias store: stale-while-revalidate, optimistic mutations
web/src/lib/keys.ts           keyboard map + handler
web/src/lib/clipboard.ts      copy with fallback
web/src/components/*.svelte   Login, ConnectApple, CodeInput, Palette, AliasRow, DetailPane, NewAlias, EditDialog, ConfirmDialog, ActionsMenu, Toasts, StepTransition, ShortcutHelp
web/src/styles/tokens.css, base.css
web/tests/*.test.ts
src/aliasfox/web.py           static + SPA fallback
src/aliasfox/main.py          (modify) mount web, add /api/session
tests/test_web.py, tests/test_session_endpoint.py
Dockerfile (modify)           node build stage
```

### Task 1: Backend — static hosting and `/api/session`

**Files:** Create `src/aliasfox/web.py`, `tests/test_web.py`, `tests/test_session_endpoint.py`; modify `src/aliasfox/main.py`, `src/aliasfox/api.py` (route only), `.gitignore` (`src/aliasfox/static/`, `web/node_modules/`, `web/dist/`).

**Interfaces:**
- Produces: `install_web(app: FastAPI, dist_dir: Path | None = None) -> None` — serves files from `dist_dir` (default `src/aliasfox/static`; env `WEB_DIST_DIR` overrides) at `/assets/*` and `/favicon.*`, and returns `index.html` for any other `GET` path NOT starting with `/api`, `/healthz`; unknown `/api/*` stays a JSON 404; missing dist directory → `/` returns 404 JSON, app still starts. Route `GET /api/session` (behind `current_user`) → `{"cache_key": <base64url 32 bytes>, "mode": str, "auth_enabled": bool}` where `cache_key = HKDF-SHA256(secret_key, info=b"aliasfox-cache-v1|" + str(user_id) + b"|" + str(epoch))`.

- [ ] **Step 1: Write failing tests**: `test_index_served_at_root_with_security_headers` (CSP/nosniff present, `Cache-Control` for `index.html` is `no-cache`), `test_assets_served_with_immutable_cache`, `test_spa_fallback_for_client_routes`, `test_unknown_api_path_is_json_404`, `test_healthz_not_shadowed`, `test_missing_dist_does_not_crash_startup`, `test_session_requires_auth`, `test_session_returns_stable_key_per_epoch_and_changes_after_logout`, `test_session_key_differs_between_secrets`, `test_session_response_no_store`. Use a tmp dist with a tiny `index.html` and `assets/app.js`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_web.py tests/test_session_endpoint.py -v` → FAIL.
- [ ] **Step 3: Implement** per the interface; mount via `StaticFiles` for `/assets`; the fallback handler must reject path traversal (`..`, encoded) and only serve from `dist_dir`.
- [ ] **Step 4: Run** the new tests and the full suite → PASS.
- [ ] **Step 5: Commit** `feat: serve the web app and expose /api/session cache key`.

### Task 2: Frontend scaffold, lint rules, Docker build stage

**Files:** Create everything under `web/` listed above (config files, `index.html`, `src/main.ts`, a placeholder `App.svelte` rendering "AliasFox"), `web/tests/smoke.test.ts`; modify `Dockerfile`, `.dockerignore`, `README.md` (dev instructions).

**Interfaces:**
- Produces: `npm run build` (in `web/`) writes `src/aliasfox/static/` (hashed assets, no inline script/style, `index.html` references only same-origin files); `npm test`, `npm run lint`, `npm run check` (svelte-check, strict TS). ESLint forbids `{@html}` (svelte rule `svelte/no-at-html-tags`) and `innerHTML`/`outerHTML`/`insertAdjacentHTML`/`document.write` assignments (`no-restricted-syntax`).

- [ ] **Step 1: Failing tests**: `web/tests/smoke.test.ts` renders `App` and expects the text "AliasFox"; `tests/test_web_build.py` (backend, skipped if `src/aliasfox/static/index.html` is absent) asserts the built `index.html` contains no `<script>` without `src`, no `style=` attribute, no `onclick=`-style attributes, and only same-origin URLs.
- [ ] **Step 2: Run** `cd web && npm test` → FAIL (no project yet).
- [ ] **Step 3: Implement** the scaffold with pinned versions (commit `package-lock.json`); Dockerfile gains a `node:22-slim` build stage running `npm ci && npm run build` and copying the output into the final image's `src/aliasfox/static`; `.dockerignore` excludes `web/node_modules`.
- [ ] **Step 4: Run** `npm run lint && npm run check && npm test && npm run build`, then `.venv/bin/pytest -q`; then `docker build -t aliasfox:ui .` and confirm `curl localhost:<port>/` returns the built page. Expected: all PASS, container serves `/`.
- [ ] **Step 5: Commit** `feat: web app scaffold with lint rules and docker build stage`.

### Task 3: API client, types, session state machine

**Files:** Create `web/src/lib/{types,api,session}.ts`, `web/tests/api.test.ts`, `web/tests/session.test.ts`.

**Interfaces:**
- Produces: `class ApiError extends Error { status: number; detail: string; retryAfter?: number }`; `api` object with `login(password)`, `logout()`, `session() -> {cache_key, mode, auth_enabled}`, `status() -> {connected, mode, auth_enabled, reason?}`, `listAliases(refresh?)`, `generate() -> {hme}`, `reserve({hme,label,note})`, `patch(id,{label,note?})`, `setActive(id,active)`, `remove(id)`, `setForwardTo(address)`, `connectCookie(cookie)`, `connectLogin({apple_id,password,china?})`, `connectVerify({flow_id,code})`, `disconnect()`. `sessionStore` (Svelte store) with `state: 'loading'|'login'|'connect'|'ready'|'reconnect'|'error'`, `boot()`, `onApiError(e: ApiError)` mapping `not_authenticated`→`login`, `not_connected|session_unreadable|session_expired`→`connect`/`reconnect`, others unchanged. Requests send `credentials: 'same-origin'`, JSON bodies, never put secrets in URLs, never log bodies.

- [ ] **Step 1: Failing tests** with a fetch mock: `test_login_posts_password_json_and_never_logs`, `test_error_detail_parsed_not_status`, `test_422_body_never_echoed_into_error_message`, `test_retry_after_header_parsed`, `test_boot_goes_login_when_not_authenticated`, `test_boot_goes_connect_when_status_not_connected`, `test_boot_goes_ready_when_connected`, `test_session_expired_moves_to_reconnect_and_keeps_alias_state`, `test_network_error_becomes_apple_unreachable_style_retry_not_crash`.
- [ ] **Step 2: Run** `npm test -- api session` → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat: typed API client and session state machine`.

### Task 4: App shell, theme, Login screen, step transitions

**Files:** Create `web/src/App.svelte` (modify), `web/src/styles/{tokens,base}.css`, `web/src/components/{Login,StepTransition,Toasts}.svelte`, `web/tests/login.test.ts`, `web/tests/transition.test.ts`.

**Interfaces:**
- Consumes: `api`, `sessionStore`. Produces: `<StepTransition step={n} direction="forward|back">` (children slide right→left with a diagonal stagger: elements nearer the top start and finish slightly earlier; duration ≈ 320 ms; no motion under `prefers-reduced-motion`); `toasts.push({kind:'ok'|'error', text})`; design tokens as CSS variables (dark only), focus rings, 16 px minimum body text, responsive to phone width with a 16 px gutter.

- [ ] **Step 1: Failing tests**: `test_login_submits_password_and_moves_on`, `test_login_wrong_password_shows_message_and_keeps_field_focus`, `test_login_429_shows_retry_after`, `test_login_has_autocomplete_current_password`, `test_transition_applies_stagger_classes_in_dom_order_and_none_when_reduced_motion`, `test_toasts_render_text_only` (a toast containing `<b>x</b>` shows the literal characters).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (load the `frontend-design` skill first; keep it restrained: one accent colour, system font stack, no third-party fonts). **Step 4: Run** → PASS. **Step 5: Commit** `feat: app shell, dark theme, login screen and step transitions`.

### Task 5: Connect-to-Apple flow

**Files:** Create `web/src/components/{ConnectApple,CodeInput}.svelte`, `web/tests/connect.test.ts`, `web/tests/codeinput.test.ts`.

**Interfaces:**
- Consumes: `api.connect*`, `StepTransition`, `toasts`. Produces: steps Apple ID → password → 2FA code (spec 10.1), plus a "paste a cookie instead" panel with a collapsed explanation (full iCloud session warning; DevTools "Copy value" tip; detect `…` and say "this looks truncated — copy it again"); `<CodeInput onComplete(code)>` six single-digit boxes grouped 3+3 with a gap; paste of "123 456"/"123-456" fills all boxes; on `invalid_code` the group shakes once and turns red until the next keystroke then returns to normal; `flow_expired` returns to the password step with the Apple ID kept; the Apple password lives only in component state and is cleared as soon as `connectLogin` resolves.

- [ ] **Step 1: Failing tests**: `test_steps_advance_email_then_password_then_code`, `test_code_input_groups_3_3_and_autoadvances_and_backspace_moves_back`, `test_code_input_paste_fills_all_boxes`, `test_wrong_code_shakes_turns_red_and_recovers_on_typing`, `test_reduced_motion_no_shake_class_but_red_state_kept`, `test_flow_expired_returns_to_password_step_keeping_apple_id`, `test_password_cleared_from_state_after_submit`, `test_cookie_panel_truncated_hint_on_ellipsis`, `test_cookie_connect_success_goes_ready`, `test_rate_limited_message_shows_retry_after`, `test_6_digit_code_only_digits_accepted`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (load `frontend-design`). **Step 4: Run** → PASS. **Step 5: Commit** `feat: connect-to-Apple flow with 2FA code entry and cookie paste`.

### Task 6: Encrypted cache, search index, alias store

**Files:** Create `web/src/lib/{crypto,cache,search,aliases,clipboard}.ts`, `web/tests/{cache,search,aliases}.test.ts`.

**Interfaces:**
- Produces: `deriveCacheKey(b64: string): Promise<CryptoKey>` (imports the 32-byte key for AES-GCM); `encryptJson(key, value)`/`decryptJson(key, blob)` (random 12-byte IV prefixed, AAD `"aliasfox-cache-v1"`); `cache.load(key) -> Alias[] | null` (null on missing/corrupt/wrong key, never throws), `cache.save(key, aliases)`, `cache.clear()`; `buildIndex(aliases) -> Index` and `query(index, text) -> Alias[]` searching label, hme, note, domain, forward_to, origin, origin_app_name (case/diacritic-insensitive, ufuzzy-ranked, empty query returns all in `created_ms` desc order); `aliasStore` with `list`, `loading`, `refresh()` (cache first via `load`, then network, then `save`; `refresh` calls `listAliases(true)` only on explicit user action), `generate()`, `reserve()`, `update()`, `setActive()`, `remove()`, optimistic updates with rollback on error, a single in-flight guard per action (double-trigger creates one request), `clear()` on logout; `copyText(text) -> 'copied'|'fallback'`.

- [ ] **Step 1: Failing tests**: `test_encrypt_decrypt_roundtrip_and_random_iv`, `test_wrong_key_returns_null_not_throw`, `test_corrupt_blob_returns_null`, `test_cache_roundtrip_in_fake_indexeddb_and_blob_has_no_plaintext_hme`, `test_clear_removes_everything`, `test_query_matches_label_domain_note_forward_and_app_name`, `test_query_10000_aliases_under_50ms` (build once, time 20 queries, assert each < 50 ms), `test_empty_query_sorted_newest_first`, `test_hostile_strings_are_searchable_and_unchanged`, `test_store_shows_cache_then_fresh_data`, `test_optimistic_toggle_rolls_back_on_error`, `test_double_reserve_sends_one_request`, `test_logout_clears_store_and_cache`, `test_copy_falls_back_when_clipboard_denied`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS. **Step 5: Commit** `feat: encrypted alias cache, fuzzy search index and alias store`.

### Task 7: Palette screen and actions

**Files:** Create `web/src/lib/keys.ts`, `web/src/components/{Palette,AliasRow,DetailPane,NewAlias,EditDialog,ConfirmDialog,ActionsMenu,ShortcutHelp}.svelte`, `web/tests/{palette,keys,newalias}.test.ts`.

**Interfaces:**
- Consumes: `aliasStore`, `query`, `copyText`, `toasts`, `sessionStore`. Produces: `Palette` with an always-focused search field, a windowed list (row height fixed; renders only visible rows ± overscan), detail pane (address, label, note, forward-to, status, origin + app name, domain, created date), keyboard map from the Global Constraints (`keys.ts` exports `KEYMAP` and `handleKey(event, ctx)`), `NewAlias` flow (Alt+N: generate → show the address with the label field focused → Enter reserves → row appears selected and copy toast shown; Esc cancels without reserving), edit dialog, delete confirmation, forward-to action in the actions menu, disconnect and logout in the actions menu, status filter (all/active/inactive via actions menu), and a `reconnect` banner when the session expires.

- [ ] **Step 1: Failing tests**: `test_list_renders_bounded_rows_for_10000`, `test_typing_filters_and_selection_resets_to_first`, `test_arrows_and_ctrl_jk_move_selection_and_scroll_into_view`, `test_enter_copies_selected_address_and_toasts`, `test_shift_enter_toggles_active_optimistically`, `test_alt_n_generates_then_enter_reserves_with_label_once`, `test_alt_n_escape_does_not_reserve`, `test_alt_backspace_confirms_before_delete_enter_confirms_esc_cancels`, `test_alt_e_edits_label_and_note`, `test_hostile_label_and_note_render_as_text` (assert `container.innerHTML` contains escaped text and no `<img`/`<script>` elements exist), `test_long_and_rtl_labels_do_not_break_row_height`, `test_shortcut_help_on_question_mark_only_when_search_empty`, `test_session_expired_during_action_shows_reconnect_and_keeps_dialog_input`, `test_empty_state_and_error_state_have_retry`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (load `frontend-design`; labelled controls, visible focus, `aria-live` toasts, roles for the list/listbox). **Step 4: Run** → PASS. **Step 5: Commit** `feat: keyboard-first alias palette`.

### Task 8: Hardening, accessibility and release check

**Files:** Modify as needed; create `web/tests/a11y.test.ts`, `tests/test_web_e2e.py`, update `README.md`.

- [ ] **Step 1: Failing tests**: `web/tests/a11y.test.ts` (axe-core via `vitest-axe` on login, connect, palette: no violations; every interactive element reachable by Tab; focus returns after dialogs close); `tests/test_web_e2e.py` using the built static bundle with a faked Apple transport (the existing test app factory): log in through `/api/auth/login`, `/api/session` returns a key, `/api/aliases` lists, and `GET /` serves the bundle with the full security-header set (no `unsafe-inline`).
- [ ] **Step 2: Run** `npm test -- a11y` and `pytest tests/test_web_e2e.py` → FAIL. **Step 3: Fix** whatever they reveal; grep the built bundle for `innerHTML`/`eval(`/`new Function` and fail the test if found. **Step 4: Run** all: `npm run lint && npm run check && npm test && npm run build && .venv/bin/pytest -q`; then `docker build` and run the container; open it in a real browser (owner) and check DevTools console shows no CSP violations. **Step 5: Commit** `feat: accessibility, CSP checks and end-to-end release test`.

---

## Self-review notes

- Spec coverage: 4.5 (encrypted IndexedDB + in-memory index, SWR) → Tasks 1, 6; 4.6 (palette, keyboard map, detail pane, optimistic updates, expired-session state) → Tasks 3, 4, 7; 10 (CSP/XSS: text-only rendering, no innerHTML lint, no third-party scripts) → Tasks 2, 7, 8; 10.1 (connect flow steps, slide diagonal, 3+3 code boxes, shake/red, reduced motion) → Tasks 4, 5; compress responses → done in plan 1.
- Names checked across tasks: `ApiError`, `sessionStore`, `aliasStore`, `query`, `cache.load/save/clear`, `copyText`, `StepTransition`, `toasts`.
- Open owner choices (my calls, easy to change): shortcut keys (Alt-based), accent colour, system font stack, logout wipes the encrypted cache, no multi-account switcher, no object storage.
