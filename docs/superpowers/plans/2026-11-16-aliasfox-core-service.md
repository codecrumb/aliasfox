# AliasFox Core Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Dockerized single-user FastAPI service that connects to iCloud (pasted cookie, then password + 2FA), keeps the Apple session encrypted at rest, and exposes a JSON API for all Hide My Email operations.

**Architecture:** One FastAPI app. A thin sync `httpx` client speaks Apple's HME endpoints from a stored session; `pyicloud` is used only for password + 2FA login to produce that session. Sessions are AES-256-GCM encrypted in SQLAlchemy (SQLite under `/data`, or Postgres via `DATABASE_URL`). Sync calls run in a threadpool behind a per-user lock.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, SQLAlchemy 2, httpx, cryptography, argon2-cffi, pydantic-settings, itsdangerous, pyicloud (timlaing), pytest. Docker.

**Spec:** `docs/superpowers/specs/2026-11-16-aliasfox-design.md`. This is plan 1 of 3. Plan 2 is the UI (palette, browser cache, search). Plan 3 is multi-user mode, password-derived keys, TOTP/passkeys, rate limits for signup, ToS, Alembic migrations and the Render + Neon deploy. Out of scope here.

## Global Constraints

- Encryption: AES-256-GCM for Apple sessions; Argon2id for passwords. No default `ALIASFOX_SECRET_KEY`; the app refuses to start without it when storing sessions.
- The Apple password is never written, logged or put in an exception message. Cookies and tokens are never logged.
- Auth off means bind `127.0.0.1` only; refuse to listen publicly without auth. Check `Host` and `Origin`.
- `/healthz` returns 200 and touches neither Apple nor the database lock.
- `ICLOUD_COOKIE` is a local-testing seed only; refreshed sessions are written back to encrypted storage.
- Aliases are never persisted server-side; the list is cached in memory for 60 seconds.
- Target: lists of 10,000 aliases without lag; runs in 512 MB.
- No `Co-Authored-By` or authorship trailers in commits.
- (my call) `ALIASFOX_ALLOW_UNAUTHENTICATED_NETWORK=true` lets a container bind `0.0.0.0` with auth off, because inside Docker the process must bind `0.0.0.0`. The shipped compose file publishes the port on `127.0.0.1` only.

## Review Focus

1. A 10,000-alias list parses, caches and serializes quickly (under 1 s in tests).
2. Apple answering 401/421 or `success:false` becomes a 401 `session_expired` from our API, never a 500.
3. Pasted cookies arrive as `Cookie: ...` lines, curl `-H` lines, quoted strings with newlines, or Cookie-Editor JSON; one missing the web-auth token gets a clear error.
4. Double-clicking Generate (two concurrent calls for one user) serializes: both succeed and the stored session is not corrupted.
5. No secret (cookie value, password, token) appears in logs or error responses on any failure path.

---

## File Structure

```
pyproject.toml, Dockerfile, docker-compose.yml, .gitignore, .env.example, README.md
src/aliasfox/config.py        Settings, validate_startup
src/aliasfox/crypto.py        encrypt/decrypt, derive_key, hash/verify password
src/aliasfox/db.py            engine, models, init_db
src/aliasfox/sessions.py      AppleSessionState, SessionStore
src/aliasfox/apple/hme.py     Alias, HmeClient, HmeError, SessionExpired
src/aliasfox/apple/connect.py parse_cookie_header, connect_with_cookie
src/aliasfox/apple/login.py   pyicloud password + 2FA flow
src/aliasfox/service.py       AliasService: lock, 60 s cache, threadpool
src/aliasfox/ratelimit.py     in-memory sliding-window limiter
src/aliasfox/auth.py          single-user app auth + Host/Origin guard
src/aliasfox/api.py           routes
src/aliasfox/main.py          create_app(settings), uvicorn entry
tests/                        one test module per source module
scripts/spike.py              read-only live spike (manual)
```

### Task 1: Scaffold, config, healthz

**Files:** Create `pyproject.toml`, `.gitignore` (ignores `.env*`, `data/`, `.venv`), `.env.example`, `src/aliasfox/{__init__,config,main}.py`, `tests/test_config.py`, `tests/test_health.py`.

**Interfaces:**
- Produces: `class Settings` (fields `aliasfox_mode: Literal["single","multi"]="single"`, `auth_enabled: bool=False`, `database_url: str|None`, `data_dir: Path=Path("/data")`, `secret_key: str|None`, `password_hash: str|None`, `icloud_cookie: str|None`, `allow_unauthenticated_network: bool=False`, `host: str="127.0.0.1"`, `port: int=8080`; env names are the upper-case field names with `ALIASFOX_` prefix on `secret_key`, `password_hash`, `mode`, `allow_unauthenticated_network`); `get_settings() -> Settings`; `validate_startup(s: Settings) -> None` raising `ConfigError`; `create_app(settings: Settings | None = None) -> FastAPI`.

- [ ] **Step 1:** `git init`; write `tests/test_config.py::test_missing_secret_key_refuses_start` (`ConfigError` when `secret_key=None`), `test_public_bind_without_auth_refused` (host `0.0.0.0`, auth off, `ConfigError`), `test_public_bind_allowed_with_escape_hatch`, `test_public_bind_allowed_with_auth`; and `tests/test_health.py::test_healthz_ok` (`GET /healthz` returns 200 `{"status":"ok"}`).
- [ ] **Step 2:** Run `pytest -v`. Expected: FAIL (modules missing).
- [ ] **Step 3:** Implement the interface above with `pydantic-settings`; `/healthz` takes no dependencies.
- [ ] **Step 4:** Run `pytest -v`. Expected: PASS.
- [ ] **Step 5:** Commit `feat: project scaffold, settings and healthz`.

### Task 2: Crypto

**Files:** Create `src/aliasfox/crypto.py`, `tests/test_crypto.py`.

**Interfaces:**
- Produces: `derive_key(secret: str, *, info: bytes = b"aliasfox-session-v1") -> bytes` (HKDF-SHA256, 32 bytes); `encrypt(key: bytes, plaintext: bytes, *, aad: bytes = b"") -> bytes` (12-byte random nonce prepended); `decrypt(key, blob, *, aad=b"") -> bytes` raising `DecryptionError`; `hash_password(pw: str) -> str` (Argon2id); `verify_password(hash: str, pw: str) -> bool`.

- [ ] **Step 1:** Tests: `test_roundtrip`, `test_nonce_is_random` (two encryptions of the same plaintext differ), `test_wrong_key_fails`, `test_tampered_blob_fails`, `test_wrong_aad_fails`, `test_derive_key_is_deterministic_and_32_bytes`, `test_password_hash_verifies_and_is_argon2id` (hash starts `$argon2id$`), `test_wrong_password_false`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement with `cryptography` (`AESGCM`, `HKDF`) and `argon2-cffi`. **Step 4:** Run; expect PASS. **Step 5:** Commit `feat: crypto helpers`.

### Task 3: Database and encrypted session store

**Files:** Create `src/aliasfox/db.py`, `src/aliasfox/sessions.py`, `tests/test_sessions.py`.

**Interfaces:**
- Consumes: `Settings`, `derive_key`, `encrypt`, `decrypt`.
- Produces: `make_engine(settings) -> Engine` (Postgres from `database_url`, else SQLite at `data_dir/aliasfox.db`); `init_db(engine) -> None` (`create_all`, and creates default `User(id=1)`); models `User(id, email nullable, created_at)`, `AppleSessionRow(user_id pk, blob LargeBinary, updated_at)`; `@dataclass AppleSessionState(cookies: dict[str,str], webservices_url: str, dsid: str | None, extra: dict)`; `SessionStore(engine, key: bytes)` with `save(user_id: int, state) -> None`, `load(user_id: int) -> AppleSessionState | None`, `delete(user_id: int) -> None`. Encryption AAD is `str(user_id).encode()`.

- [ ] **Step 1:** Tests with in-memory SQLite: `test_save_load_roundtrip`, `test_blob_in_db_contains_no_cookie_value` (read the raw column, assert the cookie value bytes are absent), `test_blob_bound_to_user` (copy user 1's blob to user 2's row, `load(2)` raises `DecryptionError`), `test_load_missing_returns_none`, `test_delete`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement; state is JSON-serialized then encrypted. **Step 4:** Run; expect PASS. **Step 5:** Commit `feat: encrypted apple session store`.

### Task 4: HME HTTP client

**Files:** Create `src/aliasfox/apple/{__init__,hme}.py`, `tests/test_hme_client.py`.

**Interfaces:**
- Consumes: `AppleSessionState`.
- Produces: `HmeError(code: int|str|None, message: str)`; `SessionExpired(HmeError)`; `@dataclass Alias(anonymous_id, hme, label, note, is_active, forward_to, origin, domain, created_ms: int|None, raw: dict)`; `HmeClient(state, *, transport: httpx.BaseTransport | None = None, on_state_change: Callable[[AppleSessionState], None] | None = None)` with `list() -> list[Alias]`, `generate() -> str`, `reserve(hme: str, label: str, note: str = "") -> Alias`, `update_metadata(anonymous_id: str, label: str, note: str | None = None) -> None`, `set_active(anonymous_id: str, active: bool) -> None`, `delete(anonymous_id: str) -> None`, `update_forward_to(address: str) -> None`.
- Endpoints (from reading pyicloud, Yimikami, Raycast and the browser extension; the spike confirms): base is `state.webservices_url`; `GET /v2/hme/list`, `POST /v1/hme/generate` (`{"langCode":"en-us"}`), `/v1/hme/reserve` (`{hme,label,note}`), `/v1/hme/updateMetaData` (`{anonymousId,label,note}`), `/v1/hme/deactivate`, `/reactivate`, `/delete` (`{anonymousId}`), `/v1/hme/updateForwardTo` (`{forwardToEmail}`). Requests send `Origin` and `Referer` of `https://www.icloud.com`, `Content-Type: text/plain`, the session cookies, and `dsid` when known. Responses are `{success, result, error}`.

- [ ] **Step 1:** Tests using `httpx.MockTransport`: `test_list_parses_aliases` (fields incl. unicode/emoji labels preserved), `test_list_10000_aliases_under_one_second`, `test_reserve_sends_expected_body`, `test_set_active_hits_deactivate_or_reactivate`, `test_delete_deactivates_then_deletes_on_failure` (first delete fails, then deactivate, then delete succeeds), `test_success_false_raises_hme_error_with_code`, `test_401_and_421_raise_session_expired`, `test_set_cookie_rotation_calls_on_state_change_with_new_cookies`, `test_errors_do_not_contain_cookie_values`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement with a sync `httpx.Client`; merge `Set-Cookie` responses into state and call `on_state_change`. **Step 4:** Run; expect PASS. **Step 5:** Commit `feat: HME http client`.

### Task 5: Cookie connection and env seed

**Files:** Create `src/aliasfox/apple/connect.py`, `tests/test_connect.py`.

**Interfaces:**
- Consumes: `AppleSessionState`, `SessionStore`, `Settings`.
- Produces: `parse_cookie_header(text: str) -> dict[str, str]` (accepts a `Cookie:` line, a curl `-H 'cookie: ...'` line, quoted multi-line text, or Cookie-Editor JSON list of `{name,value}`); `class InvalidCookie(Exception)`; `connect_with_cookie(text: str, *, transport: httpx.BaseTransport | None = None) -> AppleSessionState` (calls `POST https://setup.icloud.com/setup/ws/1/validate`, takes `webservices.premiummailsettings.url` and `dsInfo.dsid`); `seed_from_env(settings, store, *, transport=None) -> bool` (only if no valid stored session; returns whether it seeded).

- [ ] **Step 1:** Tests: `test_parse_cookie_line`, `test_parse_curl_header`, `test_parse_json_export`, `test_parse_quoted_with_newlines`, `test_missing_webauth_token_raises_invalid_cookie_with_clear_message`, `test_connect_extracts_webservices_url`, `test_validate_failure_raises_invalid_cookie_without_echoing_cookie`, `test_seed_skipped_when_valid_session_exists`, `test_seed_runs_when_stored_session_invalid`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS. **Step 5:** Commit `feat: cookie connect and env seed`.

### Task 6: Live spike (manual gate, read-only)

**Files:** Create `scripts/spike.py`, `docs/spike-results.md`.

The owner copies their iCloud cookie into `.env.local` (git-ignored) while testing locally. Do not ask them to paste it into chat. The script only reads.

- [ ] **Step 1:** Write `scripts/spike.py`: load `.env.local`, call `connect_with_cookie`, `HmeClient.list()`, then print only the alias count, the set of field names, a count by `origin`, request timings, and whether the response carried rotated cookies. It never prints addresses, labels or cookie values.
- [ ] **Step 2:** The owner runs `python scripts/spike.py`. Record in `docs/spike-results.md`: it works or not; session lifetime (re-run an hour and a day later); whether `dsid` / `clientBuildNumber` / `clientId` params are needed (try without them first); whether the list is paginated; whether Advanced Data Protection is on for this account.
- [ ] **Step 3:** Separately, test `pyicloud` login (password + 2FA typed by the owner in their own terminal) in a throwaway script: record whether it succeeds, what the session object exposes so `login.py` can extract cookies and `webservices.premiummailsettings.url`, and whether Apple challenges from a datacenter IP (deferred to the Render deploy).
- [ ] **Step 4:** If either step fails, stop and revisit the design (the fallback is the SRP flow from icloud-prime) before Task 9. Commit `docs: spike results`.

### Task 7: AliasService (lock, cache, threadpool)

**Files:** Create `src/aliasfox/service.py`, `tests/test_service.py`.

**Interfaces:**
- Consumes: `SessionStore`, `HmeClient`, `Alias`.
- Produces: `AliasService(store: SessionStore, *, client_factory: Callable[..., HmeClient] = HmeClient, ttl_seconds: float = 60)` with async methods `list_aliases(user_id, *, refresh: bool = False) -> list[Alias]`, `generate(user_id) -> str`, `reserve(user_id, hme, label, note="") -> Alias`, `update(user_id, anonymous_id, label, note) -> None`, `set_active(user_id, anonymous_id, active) -> None`, `delete(user_id, anonymous_id) -> None`, `set_forward_to(user_id, address) -> None`; raises `NotConnected` when no stored session and `SessionExpired` passes through. One `asyncio.Lock` per user; blocking calls via `run_in_threadpool`; a mutation invalidates the cache; rotated cookies are saved back to the store.

- [ ] **Step 1:** Tests with a fake client: `test_list_cached_for_ttl_then_refetched`, `test_refresh_true_bypasses_cache`, `test_mutation_invalidates_cache`, `test_concurrent_generate_calls_are_serialized` (fake records overlap; assert no overlap and both succeed), `test_rotated_cookies_saved_to_store`, `test_not_connected_raises`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS. **Step 5:** Commit `feat: alias service`.

### Task 8: Single-user auth, rate limiting and request guards

**Files:** Create `src/aliasfox/{ratelimit,auth}.py`, `tests/test_auth.py`, `tests/test_ratelimit.py`.

**Interfaces:**
- Produces: `RateLimiter(max_events: int, window_seconds: float)` with `hit(key: str) -> bool` (False when over limit); `client_ip(request, *, trusted_proxy: bool) -> str`; `install_auth(app, settings) -> None` adding `POST /api/auth/login` (`{password}`; sets a signed `HttpOnly`, `SameSite=Strict` cookie; 429 after 5 failures per IP per 15 minutes), `POST /api/auth/logout`, a `current_user` dependency returning user id 1 (401 when auth is on and the cookie is missing), and Host/Origin checking that rejects state-changing requests whose `Origin` host differs from `Host`.

- [ ] **Step 1:** Tests: `test_login_correct_password_sets_cookie`, `test_login_wrong_password_401`, `test_sixth_failure_returns_429`, `test_protected_route_401_without_cookie_when_auth_on`, `test_auth_off_allows_requests`, `test_cross_origin_post_rejected`, `test_limiter_window_expires`, `test_password_never_in_logs` (caplog on failure path).
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement with `itsdangerous` signing using a key derived from `ALIASFOX_SECRET_KEY`. **Step 4:** Run; expect PASS. **Step 5:** Commit `feat: single-user auth and rate limiting`.

### Task 9: API routes and Apple connect endpoints

**Files:** Create `src/aliasfox/api.py`, `src/aliasfox/apple/login.py`, `tests/test_api.py`, `tests/test_login.py`; modify `main.py`.

**Interfaces:**
- Consumes: `AliasService`, `current_user`, `connect_with_cookie`, `RateLimiter`.
- Produces routes (all JSON, behind `current_user`): `GET /api/aliases?refresh=`, `POST /api/aliases/generate`, `POST /api/aliases` (`{hme,label,note}`), `PATCH /api/aliases/{id}` (`{label,note}`), `POST /api/aliases/{id}/active` (`{active}`), `DELETE /api/aliases/{id}`, `PUT /api/forward-to`, `GET /api/status` (`{connected, mode, auth_enabled}`), `POST /api/connect/cookie` (`{cookie}`), `POST /api/connect/login` (`{apple_id, password, china?}` returns `{status:"ok"}` or `{status:"needs_2fa", flow_id}`), `POST /api/connect/verify` (`{flow_id, code}`), `DELETE /api/connect` (forget the stored session). `login.py`: `LoginFlow` with `start(apple_id, password, *, china=False) -> LoginResult` and `verify(flow_id, code) -> AppleSessionState`; pending flows live in memory for 10 minutes and the password is dropped as soon as `start` returns. Error mapping: `NotConnected` becomes 409 `not_connected`; `SessionExpired` becomes 401 `session_expired`; `HmeError` becomes 502 with Apple's message. Apple login attempts are limited to 5 per IP per 15 minutes.

- [ ] **Step 1:** Tests with `AliasService` faked: `test_list_returns_aliases_json`, `test_expired_session_is_401_session_expired_not_500`, `test_not_connected_is_409`, `test_connect_cookie_stores_session`, `test_connect_invalid_cookie_400_without_echo`, `test_login_needs_2fa_then_verify_stores_session` (pyicloud faked), `test_password_not_in_response_logs_or_exceptions`, `test_apple_login_rate_limited`, `test_each_mutation_route_calls_service`, `test_forget_session_deletes_row`.
- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement; use what Task 6 recorded about pyicloud to write `login.py`; run pyicloud with an explicit temp directory it owns, never its `/tmp` default, deleted after cookies are extracted. **Step 4:** Run; expect PASS. **Step 5:** Commit `feat: API routes and apple login`.

### Task 10: Wire-up, Docker, docs

**Files:** Modify `main.py`; create `Dockerfile`, `docker-compose.yml`, `README.md`.

- [ ] **Step 1:** `create_app` runs `validate_startup`, `init_db`, builds the store/service, calls `seed_from_env`, and installs auth and routes. Add `tests/test_app_startup.py::test_app_starts_and_seeds_from_env_cookie_once`.
- [ ] **Step 2:** `Dockerfile`: multi-stage, `python:3.12-slim`, non-root user, `/data` volume, non-root `CMD` uvicorn. `docker-compose.yml`: publishes `127.0.0.1:8080:8080`, mounts `./data:/data`, passes `ALIASFOX_SECRET_KEY` and optional `ICLOUD_COOKIE` from `.env`, sets the escape-hatch variable.
- [ ] **Step 3:** Run `docker compose build` then `docker compose up -d`; run `curl -s localhost:8080/healthz`. Expected: `{"status":"ok"}`. Then check memory with `docker stats --no-stream`; record in the README that it fits 512 MB.
- [ ] **Step 4:** Manual end-to-end: with the owner's cookie in `.env`, `GET /api/aliases` returns the list; restart the container and confirm the stored session still works without `ICLOUD_COOKIE`.
- [ ] **Step 5:** README: quick start, env var table, security model (what is stored and encrypted), and a warning that a pasted cookie is a full iCloud web session. Commit `feat: wire-up, docker and docs`.

## Self-review notes

- Spec coverage: 4.2 libraries (Tasks 4, 5, 9), 4.3 backend (1, 3, 7), 4.4 sessions (2, 3, 5, 7; per-user password-derived keys are in plan 3), 4.7 abuse controls (8, 9; signup rules in plan 3), 4.8 deployment (1, 10), spike (6). Section 4.5/4.6 are plan 2.
- Names checked across tasks: `AppleSessionState`, `SessionStore`, `HmeClient`, `AliasService`, `NotConnected`, `SessionExpired`.
