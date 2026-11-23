# AliasFox API Keys, Bitwarden Endpoint and MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-user scoped API keys, a key-authenticated `/api/v1`, a SimpleLogin-compatible endpoint for Bitwarden, and an MCP server, plus the web UI to manage keys.

**Architecture:** A new `api_keys` table and `ApiKeyStore` (keyed-hash lookup). A `KeyAuth` dependency authenticates `Authorization: Bearer` / `Authentication` headers, never cookies, and resolves the user from the key row. Key routes call the existing `AliasService`; a shared `CreateGuard` limits creates for the UI and keys alike. Account-level routes stay cookie-only.

**Tech Stack:** FastAPI, SQLAlchemy + Alembic (SQLite and Postgres), pytest; Svelte + vitest for the dialog; hand-written MCP handler (Task 0 decided against the `mcp` SDK).

**Spec:** `docs/superpowers/specs/2026-11-23-aliasfox-api-keys-design.md` (read it first; this plan only records decisions the spec leaves to the implementer).

## Global Constraints

- Key format `afk_` + 43 URL-safe base64 chars (32 bytes from `secrets`); display prefix = the 8 characters after `afk_`.
- Stored hash = HMAC-SHA256 under `derive_key(secret, info=b"aliasfox-apikey-v1")`; plaintext key never stored, logged or returned twice.
- Scopes, independent: `read`, `create`, `edit`, `deactivate`, `delete`. Max 20 keys per user. No default expiry.
- Keys can never reach: Apple connect/disconnect, `DELETE /api/account`, `/api/session`, `/api/status`, `/api/keys*`, `/api/forward-to` (read or write). MCP output omits `forward_to`.
- Limits: 120 requests/min per key; failed key attempts 20/min per client IP; creates 10 hits/min per user (existing `CREATE_PER_MINUTE`), 120 hits/min global (a create is two hits: generate + reserve, so this is 60 aliases/min); cooldown 300 s after 3 consecutive Apple create errors. No hourly cap.
- Key routes: `session_expired` is 503 (not 401); `not_connected` 409; bad key 401 `invalid_api_key`; missing scope 403 `insufficient_scope`.
- `API_KEYS_ENABLED` env (default true); when false every key route answers 503 `api_keys_disabled`.
- Deploy Python is 3.13 (local venv may be 3.14): never annotate with `list[...]` inside a class that defines a method named `list`; name methods `list_for_user`.
- Commit messages: conventional prefix (`feat:`, `test:`), no co-author or attribution lines (user's CLAUDE.md).
- Never log keys, headers or hashes; log exception class names only. Request body cap stays 64 KiB.
- Tests: `uv run pytest <file> -v`; web: `cd web && npx vitest run <file>`.

## Review Focus

1. A request carrying both a valid session cookie and a key, or a cookie alone, on a key route: the cookie is ignored (401); a key on a cookie route: 401.
2. A key used again right after revoke while its cache entry is hot: 401 at once, not after the 30 s TTL.
3. A user whose Apple session is missing or expired calling the Bitwarden endpoint: JSON `{"error": ...}` with 503 and the sign-in URL, never a 500 or a 401.
4. A `Authorization` header that is huge, non-ASCII, `Bearer` with nothing after it, or has scopes in the DB the code does not know: 401 or deny, no 500, nothing logged.
5. A foreign-origin preflight or POST to a non-key path: behaves exactly as before (403 `bad_origin`, no CORS headers).

---

### Task 0: Spikes (no production code)

**Files:**
- Create: `docs/superpowers/plans/2026-11-23-api-keys-spikes.md` (findings and decisions, short)

- [ ] **Step 1: Bitwarden contract.** Read Bitwarden's SimpleLogin forwarder source (`gh search code` / `gh api` in `bitwarden/clients`, the generator "integration" code) and record: path, header name, query/body fields, success status, which response field is read, how an error body is shown, and whether the server URL has `/api` appended. Compare with the spec section 7 contract; list every difference.
- [ ] **Step 2: MCP SDK.** In a scratch dir (not the repo) build a stateless, JSON-response streamable-HTTP server with the `mcp` package and call it with `initialize`, `tools/list`, `tools/call` over plain HTTP. Record: transitive dependencies added to `uv.lock`/`pyproject.toml`, whether per-request auth (reading `Authorization`, injecting a user) is possible, and the decision: SDK or hand-written handler.
- [ ] **Step 3: Write the findings file and commit** (`docs: spike notes for api keys`). If step 1 differs from the spec, edit the spec's section 7 and Task 7's tests below before continuing.

### Task 1: Key store and migration

**Files:**
- Create: `src/aliasfox/apikeys.py`, `src/aliasfox/migrations/versions/0003_api_keys.py`
- Modify: `src/aliasfox/db.py` (add `ApiKeyRow`), `src/aliasfox/users.py` (`delete` also removes the user's keys), `src/aliasfox/config.py` (`api_keys_enabled: bool`, env `API_KEYS_ENABLED`, default True)
- Test: `tests/test_apikeys.py`, `tests/test_migrations.py`, `tests/test_users.py`

**Interfaces:**
- Produces (`apikeys.py`):
  ```python
  SCOPES: tuple[str, ...] = ("read", "create", "edit", "deactivate", "delete")
  PRESETS: dict[str, tuple[str, ...]]   # bitwarden, readonly, assistant, full
  MAX_KEYS_PER_USER = 20
  class TooManyKeys(Exception)
  @dataclass(frozen=True) class KeyInfo:
      id: int; user_id: int; name: str; preset: str | None; scopes: frozenset[str]
      prefix: str; created_at: datetime; last_used_at: datetime | None; expires_at: datetime | None
  class ApiKeyStore:
      def __init__(self, engine: Engine, secret: str) -> None
      def create(self, user_id: int, name: str, scopes: Iterable[str], preset: str | None = None,
                 expires_at: datetime | None = None) -> tuple[str, KeyInfo]  # ValueError on empty/unknown scopes or blank name; TooManyKeys
      def list_for_user(self, user_id: int) -> "list[KeyInfo]"
      def revoke(self, user_id: int, key_id: int) -> bool   # False if not that user's key
      def lookup(self, key: str) -> KeyInfo | None           # None if unknown or expired; unknown scope strings in the row are dropped
      def touch(self, key_id: int) -> None                   # sets last_used_at = now
      def delete_for_user(self, user_id: int) -> int
      def delete_for_subjects(self, subjects: frozenset[str]) -> int
  ```
- `ApiKeyRow` columns exactly as the spec's section 4 table; `key_hash` unique.

- [ ] **Step 1: Write failing tests** in `tests/test_apikeys.py`: `test_key_format` (starts `afk_`, length 47, `key[4:12] == info.prefix`); `test_hash_is_keyed_and_plaintext_absent` (a store with another secret cannot `lookup` the key; no table column, read as text, contains the key); `test_scopes_validated` (`ValueError` for `[]`, `["bogus"]`, blank name); `test_lookup_unknown_and_expired_and_revoked_are_none`; `test_revoke_other_users_key_is_false`; `test_twenty_one_keys_raises_too_many_keys`; `test_delete_for_user`; `test_delete_for_subjects` (joins `users.auth_subject`); `test_unknown_scope_in_row_is_dropped_on_lookup` (write the row with raw SQL). Add `test_users.py::test_delete_user_removes_keys` and a `test_migrations.py` case: upgrade to head creates `api_keys`, downgrade drops it.
- [ ] **Step 2:** Run `uv run pytest tests/test_apikeys.py tests/test_migrations.py tests/test_users.py -v`; expect FAIL (module/table missing).
- [ ] **Step 3:** Implement the interfaces above; migration 0003 follows 0002's style (`batch_alter_table` not needed for a new table; index on `user_id`, unique on `key_hash`); `UserDirectory.delete` deletes `ApiKeyRow` rows in its transaction.
- [ ] **Step 4:** Run the same command; expect PASS. Run `uv run pytest -q` for regressions.
- [ ] **Step 5:** Commit `feat: api key store and migration`.

### Task 2: Key authentication

**Files:**
- Create: `src/aliasfox/keyauth.py`
- Modify: `src/aliasfox/main.py` (build `ApiKeyStore` next to `SessionStore`, keep it on `app.state.api_keys`; build `KeyAuth` on `app.state.key_auth`; register its limiters with `track`)
- Test: `tests/test_keyauth.py`

**Interfaces:**
- Consumes: Task 1 `ApiKeyStore`, `KeyInfo`; `ratelimit.RateLimiter`, `track`; `auth.client_ip`, `auth.limiter_key`.
- Produces:
  ```python
  class KeyAuthError(Exception):
      status: int; detail: str; needed: str | None       # 401 invalid_api_key | 403 insufficient_scope | 429 too_many_attempts|rate_limited | 503 api_keys_disabled
  class KeyAuth:
      def __init__(self, store: ApiKeyStore, settings: Settings, *, clock: Callable[[], float] = time.monotonic,
                   cache_ttl: float = 30.0) -> None
      limiters: "list[RateLimiter]"
      def authenticate(self, request: Request, scope: str | None) -> KeyInfo   # scope None = any valid key; raises KeyAuthError
      def require(self, scope: str | None = None) -> Callable[[Request], KeyInfo]   # FastAPI dependency; converts KeyAuthError to HTTPException(status, {"detail": ...}) with Retry-After on 429
      def invalidate(self, key_id: int) -> None
      def invalidate_user(self, user_id: int) -> None
      def evict_expired(self) -> int
  ```
  Reads only `Authorization: Bearer <key>` or `Authentication: <key>`; never query string or cookies. Cache `hmac -> KeyInfo` bounded to 1024 entries, TTL `cache_ttl`; `touch` at most once per 300 s per key.

- [ ] **Step 1: Write failing tests** (tiny FastAPI app with `Depends(auth.require("read"))`, fake clock): `test_missing_garbage_and_valid_headers` (none/`Bearer`/`Bearer x`/10 KB header/non-ASCII → 401 `invalid_api_key`; both header styles accepted when valid); `test_query_string_key_rejected`; `test_cookie_alone_is_401`; `test_insufficient_scope_403_names_needed_scope`; `test_revoke_plus_invalidate_is_immediate`; `test_cache_ttl_expiry_rechecks_db`; `test_touch_throttled_to_once_per_300s` (spy `store.touch`); `test_failed_attempts_limit_21st_is_429`; `test_per_key_121st_request_is_429_with_retry_after`; `test_disabled_returns_503_api_keys_disabled`; `test_expired_key_401`.
- [ ] **Step 2:** Run `uv run pytest tests/test_keyauth.py -v`; expect FAIL.
- [ ] **Step 3:** Implement. HMAC key derived once in `ApiKeyStore`; `KeyAuth` asks the store for lookup by raw key (the store hashes). Failed-attempt limiter keys on `limiter_key(client_ip(...))`; per-key limiter keys on the key id. Sweep: `KeyAuth.evict_expired()` is called from `main.sweep_memory`.
- [ ] **Step 4:** Run the file, then `uv run pytest -q`; expect PASS.
- [ ] **Step 5:** Commit `feat: api key authentication dependency`.

### Task 3: Key management routes (cookie-only)

**Files:**
- Create: `src/aliasfox/key_routes.py`
- Modify: `src/aliasfox/main.py` (include router), `src/aliasfox/api.py` only if the error handlers need a `TooManyKeys` mapping (prefer handling in the router)
- Test: `tests/test_key_routes.py` (uses `MultiUserEnv` from `tests/helpers.py`)

**Interfaces:**
- Consumes: Task 1 store, `auth.current_user`, Task 2 `app.state.key_auth`.
- Produces: `build_key_router(store: ApiKeyStore, key_auth: KeyAuth) -> APIRouter` with
  `GET /api/keys` → `[{id, name, preset, scopes, prefix, created_at, last_used_at, expires_at}]`;
  `POST /api/keys` body `{name: str(1..80), preset: "bitwarden"|"readonly"|"assistant"|"full"|None, scopes: list[str]|None, expires_days: int(1..365)|None}` (exactly one of `preset`/`scopes`, else 422) → 201 `{...same fields, "key": "<full key>"}`; 409 `too_many_keys`;
  `DELETE /api/keys/{id}` → `{"ok": true}`, 404 if not the caller's, calls `key_auth.invalidate(id)`.

- [ ] **Step 1: Write failing tests:** `test_create_returns_key_once_and_list_has_no_key`; `test_presets_map_to_scopes` (bitwarden → `["create"]`, readonly → `["read"]`, assistant → read/create/edit/deactivate, full → all five); `test_preset_and_scopes_together_or_neither_is_422`; `test_unknown_scope_422`; `test_21st_key_409`; `test_user_b_cannot_revoke_user_a_key_and_it_still_works`; `test_key_header_on_keys_routes_is_401`; `test_revoke_invalidates_cache`.
- [ ] **Step 2:** Run `uv run pytest tests/test_key_routes.py -v`; expect FAIL.
- [ ] **Step 3:** Implement the router and include it in `create_app`; `expires_days` becomes `expires_at = now + days`.
- [ ] **Step 4:** Run the file and `uv run pytest -q`; expect PASS.
- [ ] **Step 5:** Commit `feat: api key management routes`.

### Task 4: Create guard (shared limits and cooldown)

**Files:**
- Create: `src/aliasfox/createguard.py`
- Modify: `src/aliasfox/api.py` (`build_router(..., create_guard: CreateGuard | None = None)`; `_create_limited` uses it; `generate` and `reserve` call `record_error` on `HmeError` and `record_success` after a successful reserve; `delete_account` calls `reset_user`), `src/aliasfox/main.py` (one `CreateGuard` shared with Task 5; its limiters registered with `track`)
- Test: `tests/test_createguard.py`, extend `tests/test_api.py`

**Interfaces:**
- Produces:
  ```python
  class CreateGuard:
      def __init__(self, *, per_user: int = 10, global_per_minute: int = 120, cooldown_after: int = 3,
                   cooldown_seconds: float = 300.0, clock: Callable[[], float] = time.monotonic) -> None
      limiters: "list[RateLimiter]"
      def check(self, user_id: int) -> int | None   # records one hit; None = allowed, else Retry-After seconds
      def record_error(self, user_id: int) -> None
      def record_success(self, user_id: int) -> None
      def reset_user(self, user_id: int) -> None
  ```
  Keep `api.CREATE_PER_MINUTE = 10` as the default passed in so existing tests still import it.

- [ ] **Step 1: Write failing tests** (fake clock): `test_per_user_11th_hit_denied_with_retry_after`; `test_global_ceiling_denies_other_user`; `test_three_errors_start_300s_cooldown_then_ok_at_301s`; `test_success_resets_error_count`; `test_errors_are_per_user`; `test_denied_check_does_not_extend_cooldown`. In `test_api.py`: three `apple_error` reserves in a row make the next `POST /api/aliases/generate` return 429 with `Retry-After`; existing create-limit tests stay green.
- [ ] **Step 2:** Run `uv run pytest tests/test_createguard.py tests/test_api.py -v`; expect FAIL.
- [ ] **Step 3:** Implement; per-user and global limiters are `RateLimiter(…, 60, clock=…)`; cooldown is a per-user `(consecutive_errors, until)` dict, pruned by a `sweep()` called through `limiters`-style registration (add `CreateGuard.sweep` to `sweep_memory`).
- [ ] **Step 4:** Run both files and `uv run pytest -q`; expect PASS.
- [ ] **Step 5:** Commit `feat: shared create guard with global ceiling and cooldown`.

### Task 5: `/api/v1` routes

**Files:**
- Create: `src/aliasfox/v1.py`
- Modify: `src/aliasfox/main.py` (include router)
- Test: `tests/test_v1.py`

**Interfaces:**
- Consumes: Tasks 2 and 4; `AliasService`; `api.alias_json`.
- Produces: `build_v1_router(service: AliasService, key_auth: KeyAuth, guard: CreateGuard) -> APIRouter` (prefix `/api/v1`):
  `GET /me` (any key) → `{name, scopes (sorted list), connected}`; `GET /aliases` (read); `POST /aliases` (create) body `{label: str<=256, note: str<=2048 = ""}` → 201 `alias_json`; `PATCH /aliases/{id}` (edit); `POST /aliases/{id}/active` (deactivate) body `{active: bool}`; `DELETE /aliases/{id}` (delete). All return `{"ok": true}` where the UI route does.
  Create = `guard.check` twice (generate then reserve, matching the UI's two hits), `service.generate`, `service.reserve`; on `HmeError` with `code == -41003` regenerate and reserve once more; any `HmeError` calls `guard.record_error`, success calls `record_success`. `SessionExpired` becomes 503 `{"detail": "session_expired"}` on these routes only.

- [ ] **Step 1: Write failing tests** (MultiUserEnv + `FakeAppleWorld`, keys made through `env.app.state.api_keys.create`): `test_me_shape`; `test_list_matches_ui_list`; `test_create_201_generate_then_reserve_in_world_ops`; `test_create_retries_once_on_41003_then_502_on_second_failure`; `test_sixth_create_in_a_minute_is_429`; `test_patch_active_delete_each_need_their_scope`; `test_not_connected_is_409`; `test_session_expired_is_503_not_401`; `test_label_and_note_length_422`; `test_user_id_comes_from_key_not_body` (extra `user_id` field in body ignored/422, other user's data unchanged).
- [ ] **Step 2:** Run `uv run pytest tests/test_v1.py -v`; expect FAIL.
- [ ] **Step 3:** Implement the router; handlers are thin calls to `service`.
- [ ] **Step 4:** Run the file and `uv run pytest -q`; expect PASS.
- [ ] **Step 5:** Commit `feat: key-authenticated v1 alias api`.

### Task 6: Origin guard and CORS for key paths

**Files:**
- Modify: `src/aliasfox/middleware.py` (add `KEY_PATHS` check `is_key_path(path: str) -> bool` and `KeyCorsMiddleware`), `src/aliasfox/auth.py` (`request_guards` skips the `bad_origin` check when `is_key_path(request.url.path)`), `src/aliasfox/main.py` (add the middleware between `BodyLimitMiddleware` and `SecurityHeadersMiddleware`)
- Test: `tests/test_key_cors.py`

**Interfaces:**
- Produces: `is_key_path` is true for `/api/v1`, anything under `/api/v1/`, exactly `/api/alias/random/new`, and `/mcp` plus anything under `/mcp/`. `KeyCorsMiddleware` answers `OPTIONS` on those paths with 204 and `Access-Control-Allow-Origin: *`, `Access-Control-Allow-Headers: Authorization, Authentication, Content-Type, Mcp-Session-Id, Mcp-Protocol-Version`, `Access-Control-Allow-Methods: GET, POST, PATCH, DELETE, OPTIONS`, `Access-Control-Max-Age: 600`; adds `Access-Control-Allow-Origin: *` to every response on those paths; never sends `Access-Control-Allow-Credentials`.

- [ ] **Step 1: Write failing tests:** `test_preflight_on_v1_is_204_with_star_and_authentication_header`; `test_no_allow_credentials_anywhere`; `test_foreign_origin_post_with_key_not_bad_origin`; `test_foreign_origin_post_to_cookie_route_still_403_bad_origin` (`/api/aliases` and `/api/keys`); `test_options_on_non_key_path_has_no_cors_headers`; `test_is_key_path_table` (includes `/api/v1x`, `/api/alias/random/newx`, `/mcpx` → False).
- [ ] **Step 2:** Run `uv run pytest tests/test_key_cors.py -v`; expect FAIL.
- [ ] **Step 3:** Implement as pure ASGI middleware (same style as the others in `middleware.py`).
- [ ] **Step 4:** Run the file and `uv run pytest -q` (existing `test_middleware.py`, `test_auth.py` must stay green); expect PASS.
- [ ] **Step 5:** Commit `feat: origin guard exemption and CORS for key paths`.

### Task 7: SimpleLogin endpoint (Bitwarden)

**Files:**
- Create: `src/aliasfox/simplelogin.py`
- Modify: `src/aliasfox/main.py` (include router)
- Test: `tests/test_simplelogin.py`

**Interfaces:**
- Consumes: Task 2 `KeyAuth.authenticate` (not `require`: errors here are `{"error": ...}`), Task 4 guard, `settings.public_url`.
- Produces: `build_simplelogin_router(service, key_auth, guard, settings) -> APIRouter` with `POST /api/alias/random/new?hostname=<site>`, body optional JSON `{"note": str}` → 201 `{"alias": "<address>"}`; label = `hostname` or `"Bitwarden"`, note truncated to 2048. Errors, all `{"error": text}`: 401 `Invalid API key`; 403 `This key cannot create addresses`; 429 `Too many addresses created. Try again in <n> seconds.`; 503 `Apple session expired. Sign in again at <public_url>` (also for not connected; omit the URL part when `public_url` is unset); 502 `iCloud: <apple message, 200 chars>`. Uses the same create flow as Task 5 (extract it into one shared function in `v1.py`, e.g. `async def create_alias(service, guard, user_id, label, note) -> Alias`, and import it here).
- Task 0 confirmed the contract with Bitwarden's source: no redirects on this path, accepts `Authentication: <key>` verbatim; Bitwarden prefixes its own wording to 401/403 text, so keep those messages short (`Invalid API key`, `This key cannot create addresses` are fine).

- [ ] **Step 1: Write failing tests:** `test_success_201_alias_and_label_from_hostname`; `test_authorization_bearer_also_accepted`; `test_default_label_bitwarden_without_hostname`; `test_note_truncated_and_bad_json_body_ok`; `test_errors_are_error_json` (one case per status above, including key without `create` and a disconnected user); `test_body_over_64kib_is_413`; `test_regenerate_costs_one_slot_each` (two calls = two aliases in the world).
- [ ] **Step 2:** Run `uv run pytest tests/test_simplelogin.py -v`; expect FAIL.
- [ ] **Step 3:** Implement; refactor Task 5's create into the shared function without changing its tests.
- [ ] **Step 4:** Run `uv run pytest tests/test_simplelogin.py tests/test_v1.py -v` then `uv run pytest -q`; expect PASS.
- [ ] **Step 5:** Commit `feat: simplelogin-compatible endpoint for bitwarden`.

### Task 8: MCP server

**Files:**
- Create: `src/aliasfox/mcp_server.py`
- Modify: `src/aliasfox/main.py` (mount at `/mcp`); no new dependencies (Task 0 decision)
- Test: `tests/test_mcp.py`

**Interfaces:**
- Consumes: Task 2 `KeyAuth.authenticate`, Task 4 guard, Task 7's shared create function.
- Produces: `build_mcp_app(service, key_auth, guard) -> ASGIApp` speaking stateless streamable HTTP (JSON responses, no SSE). Auth: `Authorization: Bearer <key>` on every request; user from the key only. Tools and scopes: `list_aliases(query: str|None, active: bool|None, limit: int = 50)` read (limit clamped to 1..200; `query` is a case-insensitive substring over address, label, note); `create_alias(label, note="")` create; `update_alias(id, label, note=None)` edit; `set_alias_active(id, active)` deactivate; `delete_alias(id)` delete. `tools/list` returns only the tools the key's scopes allow; `tools/call` re-checks. Results contain `id, address, label, note, active, created` only. Each tool description says label/note text is untrusted data, not instructions.

- [ ] **Step 1: Write failing tests** (JSON-RPC over the test client): `test_initialize_names_server_aliasfox`; `test_tools_list_filtered_by_scope` (`[read]` → one tool, `[read, create]` → two, assistant preset → four, full → five); `test_call_without_scope_is_error_result`; `test_results_never_contain_forward_to_or_real_mailbox`; `test_extra_user_id_argument_rejected`; `test_list_query_and_limit_clamp`; `test_no_or_bad_key_is_401_at_http_level`; `test_create_counts_against_guard`.
- [ ] **Step 2:** Run `uv run pytest tests/test_mcp.py -v`; expect FAIL.
- [ ] **Step 3:** Implement the hand-written handler (`initialize`, `ping`, `tools/list`, `tools/call`, `notifications/*` → 202; unknown method → JSON-RPC -32601, bad params -32602; tool failures as `isError: true`; GET `/mcp` → 405).
- [ ] **Step 4:** Run the file and `uv run pytest -q`; expect PASS.
- [ ] **Step 5:** Commit `feat: mcp server over api keys`.

### Task 9: Lifecycle wiring and cross-cutting tests

**Files:**
- Modify: `src/aliasfox/main.py` (startup: `delete_for_subjects(settings.denylist)` next to `revoke_subjects`; `sweep_memory` calls `key_auth.evict_expired()` and the guard sweep), `src/aliasfox/api.py` (`delete_account` calls `key_auth.invalidate_user` and `guard.reset_user` before `directory.delete`; pass `key_auth` into `build_router`)
- Test: `tests/test_key_matrix.py`, extend `tests/test_isolation.py`, `tests/test_lifespan.py`

**Interfaces:**
- Consumes: everything above; `route_table(app)` from `tests/test_isolation.py`.

- [ ] **Step 1: Write failing tests:** `test_scope_matrix` (table of `(method, path, required_scope)` for every key route; a key with only scope S succeeds exactly where S is required, 403 elsewhere; deny by default); `test_all_scope_key_is_401_on_every_cookie_route` (iterate `route_table`, skip an explicit allowlist of public routes: `/healthz`, `/api/config`, `/api/auth/*`, pages, key paths); `test_cookie_on_key_routes_is_401`; isolation: `test_a_key_never_touches_b_aliases`, `test_deleted_account_keys_401_with_hot_cache`, `test_denylisted_subject_keys_deleted_at_startup`; `test_api_keys_disabled_env_makes_key_routes_503_but_ui_works`; lifespan: sweep evicts expired key-cache entries and cooldown state.
- [ ] **Step 2:** Run `uv run pytest tests/test_key_matrix.py tests/test_isolation.py tests/test_lifespan.py -v`; expect FAIL on the new tests.
- [ ] **Step 3:** Implement the wiring listed under Files.
- [ ] **Step 4:** Run `uv run pytest -q`; expect all green.
- [ ] **Step 5:** Commit `feat: api key lifecycle wiring and isolation tests`.

### Task 10: Web UI for keys

**Files:**
- Create: `web/src/components/ApiKeysDialog.svelte`, `web/tests/apikeys.test.ts`
- Modify: `web/src/lib/api.ts` (`api.listKeys()`, `api.createKey(body)`, `api.revokeKey(id)`), `web/src/lib/types.ts` (`ApiKey`, `CreatedApiKey`), `web/src/components/Palette.svelte` (action `{ id: 'apikeys', label: 'API keys', aliases: ['Api', 'Key', 'Token', 'Bitwarden', 'MCP', 'Integration', 'Access'] }` and `dialog.kind === 'apikeys'`, following the `stats` entry), `web/tests/contract.test.ts` and `web/tests/a11y.test.ts` if they enumerate routes or dialogs

**Interfaces:**
- Consumes: Task 3 routes. `ApiKey = {id, name, preset, scopes, prefix, created_at, last_used_at, expires_at}`; `CreatedApiKey = ApiKey & {key: string}`.
- Behavior: list with scope chips and Revoke (through `ConfirmDialog`); New key form with name, preset radios (Bitwarden, Read only, Assistant (MCP), Full access, Custom) filling scope checkboxes, optional expiry; choosing `delete` (Full or Custom) shows a warning and requires a confirm checkbox before Create enables; after create the full key sits in a read-only field with Copy (use `copyText`) and "You won't see this again" plus the preset's how-to text (Bitwarden steps with this site's origin; MCP `claude mcp add --transport http aliasfox <origin>/mcp --header "Authorization: Bearer <key>"`); closing discards the key from memory. Online-only: the entry is disabled with the offline banner state.

- [ ] **Step 1: Write failing tests** in `web/tests/apikeys.test.ts` (fakes from `tests/fakes.ts`): `preset Bitwarden checks only create`; `Full access requires the delete confirm before Create enables`; `created key shown once with Copy, absent after close and on reopen (prefix only)`; `revoke asks for confirmation then calls api.revokeKey`; `palette finds the action by "bitwarden"`; `api error 409 too_many_keys shows a message`.
- [ ] **Step 2:** Run `cd web && npx vitest run tests/apikeys.test.ts`; expect FAIL.
- [ ] **Step 3:** Implement client methods, types, dialog and palette entry.
- [ ] **Step 4:** Run the new file, then `cd web && npm test && npm run check && npm run build`; expect PASS (known pre-existing lint errors in `Palette.svelte` swipe code are not yours).
- [ ] **Step 5:** Commit `feat: api keys dialog`.

### Task 11: Documents and live checks

**Files:**
- Modify: `src/aliasfox/page_templates/` privacy and terms templates (find with `grep -ril privacy src/aliasfox/page_templates`), `README.md` (new section "API keys, Bitwarden and MCP", env var `API_KEYS_ENABLED`, limits), `web/tests/readme.test.ts` if it pins README text
- Test: `tests/test_pages.py`, `tests/test_repo_files.py`

- [ ] **Step 1: Write failing tests:** privacy page text states that keys are stored as a keyed hash with name, scopes and timestamps, can act through the Apple session within their scopes, and are deleted on revoke and account deletion; terms mention automation and Apple limits; README mentions the Bitwarden steps and `API_KEYS_ENABLED`.
- [ ] **Step 2:** Run `uv run pytest tests/test_pages.py tests/test_repo_files.py -v`; expect FAIL.
- [ ] **Step 3:** Write the copy (plain, no promise the code does not keep).
- [ ] **Step 4:** Run `uv run pytest -q` and `cd web && npm test`; expect PASS.
- [ ] **Step 5:** Commit `docs: api keys in privacy, terms and readme`. Then append the manual live checklist to `docs/superpowers/plans/2026-11-17-live-check.md`: real Bitwarden client creates an address; real Claude Code connects to `/mcp` with a header; revoke stops both within a second; foreign-origin browser call behaves as spec section 8; update `~/AI/aliasfox/CONTEXT.md`.

## Self-review notes

- Spec coverage: sections 4 (Task 1, 2), 5 and 6 (Tasks 3, 5), 7 (Tasks 4, 7), 8 (Task 6), 9 (Task 8), 10 (Tasks 10, 11), 11 (tests in every task plus Task 9), 12 (task order; spikes first), kill switch (Tasks 1, 2, 9), startup denylist and account deletion (Tasks 1, 9).
- Types: `KeyInfo`, `KeyAuth.authenticate/require`, `CreateGuard.check`, and the shared `create_alias` function are defined once and referenced by name later.
- Known unknowns the executor must resolve, not guess: Bitwarden's real request (Task 0), MCP SDK choice (Task 0), whether `HmeError.code` is an int for `-41003` (read `src/aliasfox/apple/hme.py`).
