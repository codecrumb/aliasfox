# AliasFox Multi-User and Launch Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AliasFox a multi-user service (Neon Auth identity, per-user wrapped data keys, isolation proven by tests) with a landing page, privacy policy, terms, licence and security policy, ready for a public launch that the owner triggers later.

**Architecture:** The browser signs in with Neon Auth and posts the JWT once to `POST /api/auth/exchange`; the server verifies it (EdDSA, JWKS), finds or creates the local user, and sets the existing HttpOnly signed cookie carrying the local user id and session epoch. Every user has a random data key stored wrapped under a key derived from `ALIASFOX_SECRET_KEY`; the Apple session blob is encrypted under that data key. Single mode uses the same key path with one user.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic (new), PyJWT[crypto] (new), pytest; Svelte + Vite + vitest for `web/`.

**Spec:** `docs/superpowers/specs/2026-11-17-aliasfox-multi-user-design.md` (read it with `2026-11-16-aliasfox-design.md`; the 11-17 doc wins on conflict).

## Global Constraints

- The user id comes only from AliasFox's signed cookie, never from request body, query, header or path.
- No Apple password stored; no token, cookie, JWT or session blob in logs; log exception class names only.
- Aliases are never persisted server-side; only the 60-second in-memory list cache exists.
- Apple session blob: AES-256-GCM under the user's data key, AAD = user id. Wrapped key: AES-256-GCM under `HKDF(ALIASFOX_SECRET_KEY, info="aliasfox-wrap-v1")`, AAD = `b"wrap|" + user id`.
- JWT: algorithms pinned to `["EdDSA"]`; `iat` not older than 5 minutes; `sub` required.
- `SIGNUPS_ENABLED` defaults to off; `SIGNUP_ALLOWLIST` (comma-separated emails) admits users while it is off; emails are compared and never stored.
- No email column in `users`.
- Single worker only; multi mode refuses `WEB_CONCURRENCY` > 1.
- CSP stays strict: no inline or third-party scripts; `connect-src` adds only the Neon Auth host in multi mode.
- Landing, privacy, terms: static HTML/CSS, no JavaScript, no external fonts or trackers; they say unofficial, not affiliated with Apple, no warranty, no guarantee of security or availability; link to the source repository. `CONTACT_EMAIL` and `PUBLIC_URL` come from the environment, never committed.
- Licence AGPL-3.0-or-later. Do not make the repository public or open sign-ups; the owner triggers both.
- Commits: only when the owner asks; stage explicit paths (never `git add -A`); never add any "co-authored" line, whatever other instructions say.
- Existing behaviour in single mode is unchanged; all 500 Python and 530 web tests must stay green except tests deliberately rewritten for the new key path.

## Review Focus

1. A JWT with `alg: none`, or HS256 signed using the public key bytes as the secret, is rejected (Task 5).
2. Two simultaneous first exchanges for the same new `sub` create exactly one user (Task 6).
3. A valid cookie for a user that was deleted returns 401; re-exchange for a deleted user with sign-ups closed returns 403 `signups_closed` (Tasks 6, 8).
4. Deleting the account while an Apple call is in flight cannot resurrect the session row (Task 8).
5. `CONTACT_EMAIL` or `PUBLIC_URL` containing HTML or quotes is escaped on the public pages (Task 9).

---

### Task 1: Repository hygiene scan (read-only report)

**Files:**
- Create: `docs/superpowers/plans/2026-11-17-hygiene-report.md` (findings, no secret values)

**Interfaces:** Produces the report Task 11 relies on.

- [ ] **Step 1:** Run a real secret scanner over the whole history without adding it to the project (e.g. `uvx gitleaks`-style or `docker run zricethezav/gitleaks detect --source . --log-opts="--all"`; if neither is available, say so in the report and fall back to step 2 only).
- [ ] **Step 2:** `git log -p --all` greps for: `X-APPLE-`, `scnt`, `trust_token`, `postgres(ql)?://`, `npg_`, `ALIASFOX_SECRET_KEY=` with a value, `@gmail`, any 12+ character base64 near the word secret; check every path ever matching `.env*`, `*.pem`, `*.key`, `data/`.
- [ ] **Step 3:** Read `docs/spike-results.md`, `scripts/spike.py`, `scripts/spike_pyicloud.py`, `neon.ts`, `README.md`, `docs/` for personal identifiers (owner's names, addresses, Render/Neon project ids, alias addresses). Record file and line, never the value.
- [ ] **Step 4:** Write the report: "nothing found" or a list. If anything real is found, STOP and report to the owner before any history rewrite.
- [ ] **Step 5:** Stage the report only; commit only if the owner asks.

### Task 2: Dependencies, config and Alembic baseline

**Files:**
- Modify: `pyproject.toml` (add `alembic`, `pyjwt[crypto]`), `src/aliasfox/config.py`, `src/aliasfox/db.py`, `.env.example`
- Create: `alembic.ini`, `src/aliasfox/migrations/env.py`, `src/aliasfox/migrations/versions/0001_baseline.py`, `0002_multi_user.py`
- Test: `tests/test_config.py`, `tests/test_migrations.py`

**Interfaces:**
- Produces `Settings` fields: `signups_enabled: bool = False` (`SIGNUPS_ENABLED`), `signup_allowlist: str = ""` (`SIGNUP_ALLOWLIST`) plus property `allowlist -> frozenset[str]` (lowercased, trimmed, empties dropped), `neon_auth_base_url: str|None` (`NEON_AUTH_BASE_URL`), `neon_auth_jwks_url: str|None` (`NEON_AUTH_JWKS_URL`), `public_url: str|None` (`PUBLIC_URL`), `contact_email: str|None` (`CONTACT_EMAIL`).
- Produces `db.User` columns `id`, `auth_subject: str` (unique, not null), `wrapped_key: bytes|None`, `created_at`, `session_epoch`; no `email`. `db.init_db(engine)` runs `alembic upgrade head` programmatically; a database that has the old tables but no `alembic_version` is stamped at 0001 first. `DEFAULT_USER_ID = 1` stays.

- [ ] **Step 1: Write failing tests.** `test_migrations.py::test_fresh_db_reaches_head`, `::test_old_schema_is_upgraded` (create the old `users`/`apple_sessions` tables by hand with a row, run `init_db`, assert `auth_subject` and `wrapped_key` exist and `email` does not), `::test_init_db_idempotent`. `test_config.py`: `test_allowlist_parsing` (`" A@x.com, ,b@y.com"` -> `{"a@x.com","b@y.com"}`), `test_signups_default_off`, `test_multi_requires_neon_urls_database_and_trusted_proxy`, `test_multi_refuses_web_concurrency_above_one` (monkeypatch `WEB_CONCURRENCY=2`), `test_multi_no_longer_refused_when_configured`, `test_public_url_must_be_https_unless_localhost`.
- [ ] **Step 2:** Run `PYTHONPATH=src .venv/bin/pytest tests/test_migrations.py tests/test_config.py -q`; expect FAIL.
- [ ] **Step 3:** Implement the settings fields; change `validate_startup`: remove the "multi mode is not implemented" error, add the multi rules above (in multi, `AUTH_ENABLED` and `ALIASFOX_PASSWORD_HASH` are ignored). Add Alembic (programmatic config from the engine, script location inside the package so the Docker image carries it; verify `pyproject.toml` package data includes `migrations/**`). `0001_baseline` recreates the current schema; `0002_multi_user` adds the two columns and drops `email` (batch mode for SQLite), existing user rows get `auth_subject = 'single'`.
- [ ] **Step 4:** Run the same command; expect PASS. Run the full suite; fix fallout only where tests construct `User(email=...)`.
- [ ] **Step 5:** Update `.env.example` with the new variables and comments. Stage; commit only if asked.

### Task 3: Key ring and per-user session encryption

**Ordering:** Tasks 3 and 4 depend on each other (the store reads `users.wrapped_key`; the directory needs `KeyRing`). Implement in this order inside one gate: `KeyRing` (Task 3), `UserDirectory` (Task 4), then the `SessionStore` rewrite and test-helper updates (Task 3). The suite is green only at the end of both.

**Files:**
- Create: `src/aliasfox/keys.py`
- Modify: `src/aliasfox/sessions.py`, `src/aliasfox/main.py`, tests that build `SessionStore(engine, key)` (`tests/test_sessions.py`, `test_service.py`, `test_api.py`, `test_e2e_app.py`, others found by grep)
- Test: `tests/test_keys.py`, `tests/test_sessions.py`

**Interfaces:**
- Produces `class KeyRing: __init__(self, secret: str)`, `new_wrapped(self, user_id: int) -> tuple[bytes, bytes]` (returns `(data_key, wrapped)`), `unwrap(self, user_id: int, wrapped: bytes) -> bytes` (raises `crypto.DecryptionError`).
- Produces `SessionStore(engine: Engine, keyring: KeyRing)` with the same `save/load/delete(user_id)`; `load` and `save` fetch `users.wrapped_key` for that user, unwrap it, and use it; a missing user, missing wrapped key or unwrap failure raises `SessionUnreadable` (save raises it too, never creates a user).

- [ ] **Step 1: Write failing tests.** `test_keys.py`: `test_roundtrip`, `test_wrapped_key_bound_to_user` (unwrap with another user id raises `DecryptionError`), `test_wrong_master_secret_fails`, `test_data_keys_are_random_32_bytes`. `test_sessions.py`: `test_save_load_roundtrip_per_user`, `test_blob_swapped_between_users_is_unreadable`, `test_wrapped_key_swapped_between_users_is_unreadable`, `test_save_for_unknown_user_raises`, `test_error_message_has_no_key_material`.
- [ ] **Step 2:** Run those files; expect FAIL.
- [ ] **Step 3:** Implement `KeyRing` with `crypto.derive_key(secret, info=b"aliasfox-wrap-v1")` and `crypto.encrypt/decrypt` using the AAD in Global Constraints. Rework `SessionStore`; wire `KeyRing(settings.secret_key)` in `create_app`. Update existing tests to a shared helper `tests/helpers.py::make_store(engine, secret) -> tuple[SessionStore, UserDirectory]` that creates user rows through Task 4's `UserDirectory` (see the ordering note above).
- [ ] **Step 4:** Run the full Python suite; expect PASS.
- [ ] **Step 5:** Stage; commit only if asked.

### Task 4: User directory (replaces DbEpochs)

**Files:**
- Create: `src/aliasfox/users.py`
- Modify: `src/aliasfox/db.py` (remove `DbEpochs` use), `src/aliasfox/auth.py` imports, `src/aliasfox/main.py`
- Test: `tests/test_users.py`

**Interfaces:**
- Produces `class UserDirectory: __init__(self, engine: Engine, keyring: KeyRing)`; `get(user_id: int) -> int | None` (session epoch) and `bump(user_id: int) -> int` (same protocol as the old epochs object); `find_by_subject(subject: str) -> int | None`; `create(subject: str) -> int` (inserts the row, flushes to get the id, then stores `wrapped_key` from `KeyRing.new_wrapped`; on a unique-subject race returns the existing id instead of raising); `ensure_single_user() -> int` (user `DEFAULT_USER_ID`, subject `"single"`, creates the wrapped key if missing); `delete(user_id: int) -> None` (removes `apple_sessions` row and the user row in one transaction; no error if absent).

- [ ] **Step 1: Write failing tests.** `test_create_returns_distinct_ids`, `test_create_same_subject_twice_returns_same_id`, `test_concurrent_create_one_row` (8 threads, one subject, assert one row), `test_bump_only_affects_that_user`, `test_get_unknown_user_is_none`, `test_delete_removes_session_and_user_and_wrapped_key`, `test_ensure_single_user_idempotent`.
- [ ] **Step 2:** Run `tests/test_users.py`; expect FAIL.
- [ ] **Step 3:** Implement. Single mode in `create_app` calls `ensure_single_user()`; multi mode does not create any default user.
- [ ] **Step 4:** Run full suite; expect PASS.
- [ ] **Step 5:** Stage; commit only if asked.

### Task 5: Neon token verifier

**Files:**
- Create: `src/aliasfox/neon_jwt.py`
- Test: `tests/test_neon_jwt.py`

**Interfaces:**
- Produces `@dataclass(frozen=True) class Identity: subject: str; email: str | None; email_verified: bool`; `class TokenRejected(Exception)` (fixed message, no token text); `class NeonTokenVerifier: __init__(self, *, jwks_url: str, issuer: str, jwks_client=None, clock=time.time, max_age_seconds: int = 300)`, `verify(self, token: str) -> Identity`.
- First step of the task: a 30-minute spike against the real Neon Auth branch (owner assists if a token is needed) to learn which claims carry email and verified status; record the answer in a comment in `neon_jwt.py` and in the hygiene-style notes. If the JWT has no email claim, `verify` returns `email=None, email_verified=False` and Task 6 handles it by calling Neon Auth's session endpoint with the token (decide in the spike).

- [ ] **Step 1: Write failing tests** with a locally generated Ed25519 key and a fake `jwks_client`: `test_valid_token`, `test_expired`, `test_wrong_issuer`, `test_missing_sub`, `test_iat_older_than_five_minutes`, `test_alg_none_rejected`, `test_hs256_with_public_key_as_secret_rejected`, `test_wrong_signing_key`, `test_garbage_and_non_ascii_token`, `test_error_text_never_contains_token`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement with `jwt.decode(..., algorithms=["EdDSA"], options={"require": ["exp", "sub", "iat"]})`; default `jwks_client` is `jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)`; wrap every failure in `TokenRejected`.
- [ ] **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Stage; commit only if asked.

### Task 6: Exchange endpoint, cookie by user id, sign-up gate

**Files:**
- Modify: `src/aliasfox/auth.py`, `src/aliasfox/main.py`, `src/aliasfox/api.py` (`/api/session` unchanged shape)
- Create: `src/aliasfox/exchange.py`
- Test: `tests/test_exchange.py`, update `tests/test_auth.py`

**Interfaces:**
- Consumes `NeonTokenVerifier`, `UserDirectory`.
- Produces in `auth.py`: `issue_cookie(response: Response, request: Request, settings: Settings, user_id: int, epoch: int) -> None` (extracted from today's login); `_validated_epoch` now accepts any integer `u` and checks it against `epochs.get(u)`, returning `(user_id, epoch)`; `current_user(request) -> int` returns that `u`. The `USER_ID` constant remains only for single-mode login. Single-mode behaviour is unchanged.
- Produces `exchange.install_exchange(app, settings, verifier, directory, *, limiter: RateLimiter | None = None) -> None` registering `POST /api/auth/exchange` with body `{"token": str}` (max length 4096). Responses: 200 `{"ok": true}` and cookie; 401 `invalid_token`; 403 `signups_closed`; 403 `email_not_verified`; 429 `too_many_attempts` (per-IP, same helpers as login). In multi mode `/api/auth/login` is not registered (404) and `/api/auth/logout` bumps the epoch of the cookie's user only.
- Sign-up rule: known `sub` -> sign in. Unknown `sub` -> require `email_verified`, then create only if `settings.signups_enabled` or the lowercased email is in `settings.allowlist`.

- [ ] **Step 1: Write failing tests** (verifier faked): `test_known_subject_gets_cookie`, `test_unknown_subject_closed_is_403`, `test_unknown_subject_open_creates_user`, `test_allowlisted_email_creates_user_while_closed`, `test_allowlist_case_insensitive`, `test_unverified_email_cannot_create`, `test_no_email_stored_in_db` (inspect `users` columns and row), `test_two_concurrent_first_exchanges_one_user`, `test_bad_token_401_no_cookie`, `test_oversized_or_non_string_token_is_422_or_401_never_500`, `test_exchange_rate_limited_per_ip`, `test_logout_bumps_only_that_user_epoch`, `test_login_route_absent_in_multi`, `test_cookie_for_deleted_user_is_401`, `test_cookie_epoch_mismatch_is_401`, `test_cookie_signed_with_other_secret_is_401`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement; wire in `create_app`: multi builds `NeonTokenVerifier` from settings and calls `install_exchange`; single keeps `install_auth` password login.
- [ ] **Step 4:** Run full suite; expect PASS.
- [ ] **Step 5:** Stage; commit only if asked.

### Task 7: Isolation and route-coverage tests

**Files:**
- Create: `tests/test_isolation.py`
- Modify: production code only if a test exposes a leak (fix the leak, never the test)

**Interfaces:** Consumes the multi-mode app factory from Tasks 3 to 6; builds two users A and B with real cookies via the exchange route and a fake Apple client factory returning distinct aliases per user.

- [ ] **Step 1: Write the tests:** `test_a_never_sees_b_aliases_status_or_session_key`; `test_user_id_in_body_query_header_path_is_ignored` (send `user_id`, `u`, `X-User-Id`, `?user=` on every mutating route and assert B's data untouched); `test_a_cannot_modify_or_delete_b_alias_by_id` (alias id belonging to B returns Apple error from A's own session, B's list unchanged); `test_b_blob_in_a_row_is_session_unreadable` and `test_a_wrapped_key_in_b_row_is_session_unreadable`; `test_a_pending_login_cannot_be_verified_resent_or_cancelled_by_b`; `test_a_disconnect_leaves_b_connected`; `test_a_logout_does_not_log_out_b`; `test_cache_key_differs_per_user_and_changes_on_logout`; `test_every_route_requires_auth_or_is_allowlisted` (iterate `app.routes`, call each with no cookie, expect 401 except `/healthz`, `/api/config`, `/api/auth/exchange`, `/api/auth/logout`, public pages and static assets; a new unlisted route fails the test).
- [ ] **Step 2:** Run `tests/test_isolation.py`; failures are leaks to fix (expected none for ids, as keys are per user already; the route test may need Task 8's `/api/config` first, so run this task after Task 8's route exists or add the allow-list entry then).
- [ ] **Step 3:** Run full suite; expect PASS.
- [ ] **Step 4:** Stage; commit only if asked.

### Task 8: Account deletion, public config and Apple-facing limits

**Files:**
- Modify: `src/aliasfox/api.py`, `src/aliasfox/service.py` (only if needed)
- Test: `tests/test_account.py`, `tests/test_connect_api.py`

**Interfaces:**
- Produces `GET /api/config` (no auth): `{"mode", "signups_open": bool, "neon_auth_url": str|None, "contact": str|None}`; `DELETE /api/account` (auth): cancels the user's pending login flows, `service.disconnect`, `UserDirectory.delete`, clears the cookie, returns `{"status": "deleted"}`.
- Produces a per-user Apple login limiter `login_user = RateLimiter(5, 3600)` keyed by user id in `connect_login` (429 `rate_limited` with `Retry-After`), kept alongside the existing per-IP and global limiters; limiter slots are released only if Apple was never reached, as today.

- [ ] **Step 1: Write failing tests:** `test_delete_account_removes_everything` (row, wrapped key, session blob gone; cookie dead; second delete is 401), `test_delete_account_does_not_touch_other_user`, `test_delete_during_inflight_apple_call_does_not_resurrect_session` (fake client blocks, delete runs, release the client, assert no `apple_sessions` row), `test_reexchange_after_delete_with_signups_closed_is_403`, `test_config_endpoint_reflects_flag_and_hides_secrets`, `test_per_user_login_cap_five_per_hour_other_user_unaffected`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement. `SessionStore.save` already raises for a missing user, so `AliasService._persist` (which logs and swallows) is the resurrection guard; confirm by the test.
- [ ] **Step 4:** Run full suite; expect PASS.
- [ ] **Step 5:** Stage; commit only if asked.

### Task 9: Public pages (landing, privacy, terms)

**Files:**
- Create: `src/aliasfox/pages.py`, `src/aliasfox/page_templates/landing.html`, `privacy.html`, `terms.html`, `site.css`
- Modify: `src/aliasfox/web.py` or `main.py` (register pages before the SPA catch-all), `src/aliasfox/middleware.py` (CSP only if the stylesheet is blocked), `pyproject.toml` package data, `Dockerfile` if it copies selectively
- Test: `tests/test_pages.py`

**Interfaces:**
- Produces `install_pages(app: FastAPI, settings: Settings, epochs) -> None`: in multi mode `GET /` returns the landing page when there is no valid cookie and falls through to the SPA when there is; `GET /privacy`, `GET /terms`, `GET /site.css`. In single mode nothing is registered (behaviour unchanged). Templates take `{{CONTACT}}`, `{{PUBLIC_URL}}`, `{{SIGNUPS}}` replaced with HTML-escaped values; copy decisions follow the spec section 5.7 and 10 (operator "AliasFox", repo owner `codecrumb`; draft banner; the repository link is the `codecrumb/aliasfox` GitHub URL).

- [ ] **Step 1: Write failing tests:** `test_landing_for_signed_out_visitor`, `test_landing_has_no_script_tag_and_no_external_urls_except_repo_link`, `test_signed_in_gets_spa_index`, `test_signups_closed_message_shown_when_flag_off`, `test_privacy_names_stored_and_not_stored_items` (assert key phrases: "encrypted", "never stored" for the Apple password, "60 seconds", "Neon", "Render", "delete"), `test_terms_state_no_warranty_unofficial_not_affiliated_with_apple_no_guarantee_security_availability`, `test_contact_email_is_escaped` (`CONTACT_EMAIL='"><script>x</script>'` never appears raw), `test_public_url_trailing_slash_and_html_escaped`, `test_no_contact_set_falls_back_to_github_issue_text`, `test_pages_absent_in_single_mode`, `test_security_headers_present_on_pages`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Write the templates: dark theme matching the app, fox logo from the built assets (`/favicon.svg`), landing headline "AliasFox manages your iCloud Hide My Email addresses", sign up / log in links to `/login` and `/signup` of the SPA, footer links, mobile-first CSS. The policy and terms content follow the spec lists exactly, are marked draft, and say plainly that a leak of the database plus the server secret exposes sessions.
- [ ] **Step 4:** Run `tests/test_pages.py` then the full suite; expect PASS. Open the pages in a browser at phone and desktop width and check them visually.
- [ ] **Step 5:** Stage; commit only if asked.

### Task 10: Frontend sign-in, sign-up and account views

**Files:**
- Create: `web/src/lib/neonAuth.ts`, `web/src/components/SignIn.svelte`, `SignUp.svelte`, `AccountDialog.svelte`
- Modify: `web/src/App.svelte`, `web/src/lib/api.ts`, `web/src/lib/session.ts`, `web/src/components/ActionsMenu.svelte`, `web/src/lib/keys.ts` only if a shortcut is added
- Test: `web/src/**/*.test.ts` next to each file, following the existing test style

**Interfaces:**
- Consumes `GET /api/config`, `POST /api/auth/exchange`, `DELETE /api/account`, `POST /api/auth/logout`.
- Produces `neonAuth.ts`: `signUp(email, password)`, `signIn(email, password)`, `verifyEmailCode(code)` if the branch requires it, `getToken(): Promise<string>`, `signOut()`, `deleteNeonUser()` (best effort; reports "pending" when Neon's API does not allow it). Implementation choice by a short spike: Neon's SDK in Svelte versus plain `fetch` against the Better Auth routes; pick the smaller and CSP-clean one, record why in a comment.

- [ ] **Step 1: Write failing tests:** sign-in success calls `getToken` then `exchange` then loads the app; wrong password shows a plain error and no exchange call; `signups_closed` shows the closed message; sign-up hidden when `/api/config` says closed; account dialog: disconnect Apple, delete account requires a typed confirmation, success returns to signed-out state; logout calls Neon sign-out and AliasFox logout; no `innerHTML` (existing lint rule stays green).
- [ ] **Step 2:** Run `npm --prefix web test`; expect FAIL.
- [ ] **Step 3:** Implement views in the existing visual style (separate screens with the existing step transition, `autocomplete="email"`, `current-password`/`new-password`), password hints per Neon's rules, keyboard-only usable. Add the Neon Auth host to the CSP `connect-src` in multi mode only (`middleware.py`, with a test in `tests/test_middleware.py`). Register the app origin as a Neon Auth trusted domain (owner step, documented in README).
- [ ] **Step 4:** Run `npm --prefix web test`, `npm --prefix web run check`, lint and build; expect all green. Run the app locally in multi mode against the real Neon Auth branch with a test account and walk sign-up, verify, sign-in, connect, delete account.
- [ ] **Step 5:** Stage; commit only if asked.

### Task 11: Licence, security policy, README, notes

**Files:**
- Create: `LICENSE` (AGPL-3.0-or-later full text), `SECURITY.md`
- Modify: `README.md`, `.env.example`, `pyproject.toml` (`license`), `~/AI/aliasfox/CONTEXT.md`, `~/AI/INDEX.md`
- Test: `tests/test_repo_files.py`

**Interfaces:** Consumes the Task 1 report.

- [ ] **Step 1: Write failing tests:** `test_license_is_agpl3` (file starts with the GNU AGPL v3 title and `pyproject.toml` declares `AGPL-3.0-or-later`), `test_security_md_has_reporting_route_and_no_promises` (mentions GitHub private vulnerability reporting and `CONTACT_EMAIL` placeholder text, contains "no guarantee"/"no promise"), `test_readme_mentions_unofficial_and_no_warranty_and_multi_mode_vars`, `test_env_example_lists_every_new_setting`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Write the files. README: multi-mode section (all new variables, Neon Auth trusted domain, custom SMTP needed before launch, single worker, `TRUSTED_PROXY_HOPS` check), the data-custody statement, link to the policy. Remove "multi is refused" text. SECURITY.md: how to report privately, no response-time or fix promises, scope, no bounty.
- [ ] **Step 4:** Run the full Python and web suites, lint and build; expect all green.
- [ ] **Step 5:** Update `~/AI/aliasfox/CONTEXT.md` with verified facts, decisions, gotchas, state; refresh the `INDEX.md` row. Mark anything not live-verified "(unverified)".
- [ ] **Step 6:** Stage; commit only if asked.

### Task 12: Live checks with the owner (no code)

- [ ] Deploy to Render only when the owner asks. Switch env to multi (`ALIASFOX_MODE=multi`, Neon Auth URLs, `TRUSTED_PROXY=true`, `CONTACT_EMAIL`, `PUBLIC_URL`, `SIGNUP_ALLOWLIST` containing the owner's email, `SIGNUPS_ENABLED` unset).
- [ ] With the owner: sign up through the allow-list, reconnect Apple, confirm a second Neon account not on the list gets `signups_closed`.
- [ ] Verify and record: Render `X-Forwarded-For` chain and the right `TRUSTED_PROXY_HOPS`; wrong-then-right 2FA code; session lifetime; datacenter-IP challenge; the real shape of Apple's reserve reply; whether Neon allows self-deletion and disabling sign-up; Neon project region is AWS.
- [ ] Update `~/AI/aliasfox/CONTEXT.md` with each result.
