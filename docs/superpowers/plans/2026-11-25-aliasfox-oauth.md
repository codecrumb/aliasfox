# AliasFox OAuth for MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OAuth-only MCP clients (claude.ai connectors, ChatGPT, Claude Code without a header) can connect to `/mcp` through a consent screen that mints an ordinary scoped API key.

**Architecture:** A new `oauth.py` holds pure pieces (stateless signed client ids, redirect rule, PKCE, in-memory one-shot code store). `oauth_routes.py` exposes discovery documents, dynamic registration, the token endpoint and two cookie-only endpoints for the SPA consent screen. The token endpoint calls `ApiKeyStore.create`, so `KeyAuth`, scopes, limits and revocation are unchanged. The consent screen is an SPA dialog on `/oauth/authorize`.

**Tech Stack:** FastAPI, SQLAlchemy store already present (no new table, no new dependency), Svelte 5 + vitest.

**Spec:** `docs/superpowers/specs/2026-11-25-aliasfox-oauth-design.md` (read it first; it fixes every value below).

Execute on a new branch `feat/oauth` in a worktree (`.worktrees/oauth`), as the API keys work did.

## Global Constraints

- `afk_` keys, scopes, presets come from `aliasfox/apikeys.py` (`SCOPES`, `PRESETS`); do not redefine them.
- Client id: `"afc1." + b64url(json{"n","r"}) + "." + b64url(HMAC-SHA256)` under `derive_key(secret, info=b"aliasfox-oauth-client-v1")`; max 2048 chars.
- Redirect rule: `https` with host, or `http` with host `127.0.0.1`/`localhost`/`[::1]`; no fragment, no userinfo; max 5 URIs, 300 chars each; exact string match at approve and token.
- PKCE: `S256` only; challenge 43 chars base64url; verifier 43..128 chars; constant-time compare.
- Code: 32 random bytes urlsafe, TTL 60 s, single use, cap 1000, in memory.
- Limits: registration 20/hour/IP, token failures 20/min/IP.
- Token response: `{access_token, token_type: "Bearer", scope}`, `Cache-Control: no-store`; key name `"<client name> (OAuth)"` (<= 80 chars), `preset=None`, no expiry, no refresh token.
- Every OAuth endpoint answers 503 `api_keys_disabled` when `settings.api_keys_enabled` is false.
- `/api/oauth/*` are cookie-only (use `current_user`); API keys must be rejected there.
- Errors from `/oauth/*` use OAuth bodies: `{"error": "...", "error_description": "..."}`, not `{"detail"}`.
- No co-author/attribution lines in commits (owner's global rule).
- Commands: Python tests `.venv/bin/pytest -q` (check `README.md`/CI for the exact venv path), web tests `cd web && npx vitest run && npx svelte-check && npm run lint`.

## Review Focus

- `redirect_uri` not in the client's signed list, or a client id whose payload was edited: never redirects, never mints a code (Tasks 1, 4).
- Code replay, code used by another client id or another redirect URI, expired code (Tasks 1, 3).
- PKCE downgrade: `plain`, missing challenge, wrong verifier, 42/129-char verifier (Tasks 3, 4).
- A bearer key or OAuth token presented to `/api/oauth/approve` (Task 4).
- Garbage input: huge or non-JSON register body, 6 redirect URIs, duplicated form fields, non-UTF-8 form body, `state` containing `&#?` characters (Tasks 2, 3, 4).
- Consent state lost across sign-in/sign-up/email-code steps that rewrite the URL (Task 5).

---

### Task 1: Pure OAuth pieces (`oauth.py`)

**Files:**
- Create: `src/aliasfox/oauth.py`
- Test: `tests/test_oauth_core.py`

**Interfaces:**
- Produces:
  - `class ClientError(ValueError)`
  - `def make_client_id(secret: str, name: str, redirect_uris: list[str]) -> str`
  - `def read_client_id(secret: str, client_id: str) -> tuple[str, list[str]]` (name, redirect URIs; raises `ClientError`)
  - `def check_redirect_uri(uri: str) -> str` (returns it unchanged or raises `ClientError`)
  - `def pkce_ok(verifier: str, challenge: str) -> bool`
  - `def valid_challenge(challenge: str) -> bool`
  - `def append_query(uri: str, params: dict[str, str]) -> str` (adds url-encoded params, keeps an existing query)
  - `@dataclass(frozen=True) class Grant: user_id: int; scopes: tuple[str, ...]; client_id: str; redirect_uri: str; challenge: str; name: str`
  - `class CodeStore: def __init__(self, *, clock=time.monotonic, ttl=60.0, cap=1000)`; `put(grant: Grant) -> str`; `pop(code: str) -> Grant | None`
  - `def public_origin(request: Request, settings: Settings) -> str` (`settings.public_url` stripped of a trailing slash, else scheme+host built with `scope_is_https(request.scope, settings.trusted_proxy)` from `aliasfox.middleware`)

- [ ] **Step 1: Write failing tests** in `tests/test_oauth_core.py`: `test_client_id_round_trip` (name and URIs come back), `test_client_id_tamper_rejected` (flip one payload char, flip signature, wrong secret, over 2048 chars, missing prefix each raise `ClientError`), `test_redirect_rule` parametrized: accepted `https://claude.ai/api/mcp/auth_callback`, `http://127.0.0.1:53682/cb`, `http://localhost/cb`, `http://[::1]:9/cb`; rejected `http://example.com/cb`, `cursor://x/y`, `javascript:alert(1)`, `https://u:p@example.com/cb`, `https://example.com/cb#f`, `https:///cb`, a 301-char URI, empty string. `test_pkce` (RFC 7636 appendix B vector: verifier `dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk` -> challenge `E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM`; 42 and 129 char verifiers false; wrong verifier false), `test_valid_challenge`, `test_append_query` (no query, existing query, `state="a&b#c?d"` survives a `parse_qs` round trip), `test_code_store_single_use`, `test_code_store_expires` (fake clock, 61 s), `test_code_store_cap` (1001 puts, the oldest is gone, the newest works).
- [ ] **Step 2: Run** `pytest tests/test_oauth_core.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `oauth.py`** with the signatures above. Use `hmac.compare_digest`, `base64.urlsafe_b64encode` without padding, `urllib.parse.urlsplit` for the redirect rule (reject on `ValueError`, userinfo via `.username`/`.password`, host via `.hostname`), `secrets.token_urlsafe(32)` for codes, an insertion-ordered dict for the store (drop oldest at the cap, lazily drop expired on `put`/`pop`), a `threading.Lock`.
- [ ] **Step 4: Run** `pytest tests/test_oauth_core.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git add src/aliasfox/oauth.py tests/test_oauth_core.py && git commit -m "feat: OAuth building blocks (signed client ids, PKCE, one-shot codes)"`.

---

### Task 2: Discovery, registration and the 401 hint

**Files:**
- Create: `src/aliasfox/oauth_routes.py`
- Modify: `src/aliasfox/middleware.py:186-191` (`is_key_path`), `src/aliasfox/mcp_server.py:99,151-155` (WWW-Authenticate), `src/aliasfox/main.py:170-175` (include router)
- Test: `tests/test_oauth_routes.py`

**Interfaces:**
- Consumes: Task 1 (`make_client_id`, `check_redirect_uri`, `public_origin`, `CodeStore`), `KeyAuth` (for the settings and limiter pattern), `RateLimiter`, `track`.
- Produces: `def build_oauth_router(store: ApiKeyStore, key_auth: KeyAuth, settings: Settings, secret: str, codes: CodeStore) -> APIRouter` (this task adds the metadata and `/oauth/register` routes; Tasks 3 and 4 add more routes to the same router); `is_key_path` also true for `/oauth/register`, `/oauth/token`, `/.well-known/oauth-protected-resource`, `/.well-known/oauth-protected-resource/mcp`, `/.well-known/oauth-authorization-server`; `build_mcp_router(...)` unchanged signature but its 401 carries `WWW-Authenticate: Bearer resource_metadata="<origin>/.well-known/oauth-protected-resource"` (origin from `public_origin(request, key_auth._settings)`; add a small public `settings` property on `KeyAuth` instead of reaching into the private attribute).

- [ ] **Step 1: Write failing tests** in `tests/test_oauth_routes.py` using `MultiUserEnv` as `tests/test_mcp.py` does: `test_protected_resource_metadata` (both paths: `resource == <origin>/mcp`, `authorization_servers == [<origin>]`), `test_authorization_server_metadata` (exact `code_challenge_methods_supported == ["S256"]`, `token_endpoint_auth_methods_supported == ["none"]`, the three endpoint URLs under the same origin), `test_mcp_401_names_the_metadata_document` (no key -> 401, header starts with `Bearer resource_metadata="` and ends `/.well-known/oauth-protected-resource"`), `test_register_ok` (201, `client_id` startswith `afc1.`, echoes name and URIs, `token_endpoint_auth_method == "none"`), `test_register_rejects` parametrized (6 URIs, a bad URI, empty list, non-JSON body, JSON that is a list, name of 500 chars is truncated to 80 not rejected, missing name defaults to `"MCP client"`), `test_register_rate_limited` (21st call within an hour -> 429), `test_cors_preflight_and_origin_guard_on_oauth_paths` (OPTIONS `/oauth/register` -> 204 with `access-control-allow-origin: *`; a POST with a foreign `Origin` is not rejected by the origin guard), `test_oauth_disabled_by_kill_switch` (`api_keys_enabled=False` -> 503 on metadata and register).
- [ ] **Step 2: Run** `pytest tests/test_oauth_routes.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `build_oauth_router` metadata + register routes; the register handler reads the body with `await request.body()` (size already capped by `BodyLimitMiddleware`), parses JSON itself, returns OAuth-shaped errors (`invalid_client_metadata`, `invalid_redirect_uri`). Add the IP limiter (`RateLimiter(20, 3600)`) keyed with `limiter_key(client_ip(...))` like `KeyAuth`, and `track(app.state, limiter)` in `main.py`. Create one `CodeStore` in `main.py`, `app.state.oauth_codes`, passed to the router. Add the paths to `is_key_path` with a comment that they carry no cookies.
- [ ] **Step 4: Run** `pytest tests/test_oauth_routes.py tests/test_mcp.py tests/test_key_cors.py -v`. Expected: PASS (the existing MCP and CORS tests still pass).
- [ ] **Step 5: Commit** `git add src tests && git commit -m "feat: OAuth discovery documents, dynamic client registration, MCP 401 hint"`.

---

### Task 3: Token endpoint

**Files:**
- Modify: `src/aliasfox/oauth_routes.py` (add `POST /oauth/token`)
- Test: `tests/test_oauth_routes.py`

**Interfaces:**
- Consumes: Task 1 `CodeStore.pop`, `Grant`, `pkce_ok`, `read_client_id`; `ApiKeyStore.create(user_id, name, scopes, preset=None, expires_at=None) -> (key, KeyInfo)`; `TooManyKeys`.
- Produces: `POST /oauth/token` as specified in spec section 6. A test helper `grant_code(env, ...)` in the test file that puts a `Grant` straight into `app.state.oauth_codes` and returns the code (Task 4 replaces the shortcut with the real approve call).

- [ ] **Step 1: Write failing tests**: `test_token_success_mints_a_working_key` (put a Grant with scopes `("read",)` for a signed-in user, POST form-encoded; response has `token_type == "Bearer"`, `scope == "read"`, `Cache-Control: no-store`, and the `access_token` starts `afk_` and works on `POST /mcp tools/list` showing only `list_aliases`; the key appears in `GET /api/keys` named `"<name> (OAuth)"`), `test_token_failures_are_invalid_grant` parametrized: replayed code, unknown code, expired code (fake clock), other `client_id`, other `redirect_uri`, wrong verifier, 42-char verifier, 129-char verifier (all 400 `{"error": "invalid_grant"}`, including the two bad-length verifiers, and the code is consumed in every case), `test_token_malformed_requests` (JSON body -> `invalid_request`, `grant_type=refresh_token` -> `unsupported_grant_type`, missing field -> `invalid_request`, non-UTF-8 body -> `invalid_request`, duplicated `code` field -> `invalid_request`), `test_token_too_many_keys` (fill the per-user key cap, then exchange -> 400 `invalid_request` and the code is consumed), `test_token_failures_rate_limited` (21 bad calls -> 429 with `Retry-After`), `test_token_after_revoking_key_fails_at_mcp` (revoke the key via `DELETE /api/keys/{id}`, `/mcp` -> 401).
- [ ] **Step 2: Run** `pytest tests/test_oauth_routes.py -k token -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the handler: parse with `urllib.parse.parse_qs(body.decode(), strict_parsing=...)` inside try/except (reject any repeated field), check in the spec's order, pop the code before comparing anything else so a bad attempt still burns it, hit a failed-attempt `RateLimiter(20, 60)` only on failures, and name the key `f"{grant.name} (OAuth)"[:80]`.
- [ ] **Step 4: Run** `pytest tests/test_oauth_routes.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: OAuth token endpoint mints a scoped API key"` (add new files first if any).

---

### Task 4: Consent endpoints (cookie only)

**Files:**
- Modify: `src/aliasfox/oauth_routes.py` (add `GET /api/oauth/client`, `POST /api/oauth/approve`)
- Modify: `tests/test_isolation.py` (`PUBLIC`/route tables) and `tests/test_key_matrix.py` as their route sweeps require
- Test: `tests/test_oauth_routes.py`

**Interfaces:**
- Consumes: Task 1; `current_user` dependency from `aliasfox.auth`; `PRESETS`.
- Produces: `GET /api/oauth/client?client_id&redirect_uri` -> `{"name": str, "redirect_host": str}` or 400 `{"detail": "invalid_client"}`; `POST /api/oauth/approve` body model `Approve{client_id: str, redirect_uri: str, state: str | None (max 512), code_challenge: str, code_challenge_method: Literal["S256"], preset: Literal["readonly","assistant","full"], decision: Literal["allow","deny"], resource: str | None}` -> `{"redirect": str}`.

- [ ] **Step 1: Write failing tests**: `test_client_info` (shows name and `redirect_host`; unknown redirect -> 400; tampered client id -> 400; signed out -> 401), `test_approve_allow_then_token_end_to_end` (register -> approve allow with preset `assistant` -> parse `code` and `state` from `redirect` -> token with the right verifier -> `/mcp tools/list` shows the four tools without `delete_alias`), `test_approve_deny` (redirect has `error=access_denied` and the state, no code), `test_approve_rejects` parametrized: redirect_uri not registered, tampered client id, `code_challenge_method: "plain"` (422), challenge of 42 chars, unknown preset (422), `resource` set to another origin's `/mcp`; none of them returns a `redirect` or creates a code, `test_approve_state_is_encoded` (`state="a&b#c?d=e"` round trips through the redirect), `test_approve_is_cookie_only` (a valid API key as `Authorization: Bearer` and as `Authentication` -> 401, with and without scopes), `test_approve_requires_same_origin` (foreign `Origin` header -> rejected like other cookie POSTs), `test_oauth_endpoints_disabled_by_kill_switch`.
- [ ] **Step 2: Run** `pytest tests/test_oauth_routes.py -k "client or approve" -v`. Expected: FAIL.
- [ ] **Step 3: Implement** both routes. Approve re-reads the client id with `read_client_id`, requires exact membership of `redirect_uri` in its list, validates the challenge with `valid_challenge`, checks `resource` (when given) equals `f"{public_origin}/mcp"`, then stores `Grant(user_id, PRESETS[preset], client_id, redirect_uri, code_challenge, name)` and returns `append_query(...)`.
- [ ] **Step 4: Update the route sweeps** in `tests/test_isolation.py` and `tests/test_key_matrix.py` so the new routes are classified (`/oauth/*` and `.well-known` as public key-path routes, `/api/oauth/*` as cookie-only user routes); run them and fix until they pass. Also confirm `GET /oauth/authorize` is served by the SPA fallback (add a case to `tests/test_web.py` asserting 200 and the index HTML) and that `_SpaPathConvertor` in `web.py` does not exclude it.
- [ ] **Step 5: Run** `pytest -q`. Expected: all green (the single test_pages failure noted in the old notes is gone; if any other fails, report it, do not skip).
- [ ] **Step 6: Commit** `git commit -am "feat: OAuth consent endpoints (cookie only) and route-sweep updates"`.

---

### Task 5: Consent screen (SPA)

**Files:**
- Create: `web/src/lib/oauth.ts`, `web/src/components/ConsentDialog.svelte`
- Modify: `web/src/lib/api.ts` (two calls), `web/src/App.svelte` (show the dialog), `web/tests/contract.test.ts` (new routes, if it pins the route list)
- Test: `web/tests/oauth.test.ts`

**Interfaces:**
- Consumes: Task 4 endpoints.
- Produces:
  - `oauth.ts`: `interface GrantRequest { clientId: string; redirectUri: string; state: string | null; challenge: string; method: string }`; `parseGrantRequest(loc: Pick<Location,'pathname'|'search'>): GrantRequest | null` (null unless path is `/oauth/authorize`, `response_type=code`, and the four required params exist); `savePending(r: GrantRequest | null): void` and `loadPending(): GrantRequest | null` using `sessionStorage` inside try/catch.
  - `api.ts`: `oauthClient(clientId: string, redirectUri: string): Promise<{name: string; redirect_host: string}>`, `oauthApprove(body: {...Approve fields}): Promise<{redirect: string}>`.
  - `ConsentDialog.svelte` props `{ request: GrantRequest; onDone: () => void }`; on Allow/Deny it calls `oauthApprove` then `location.assign(redirect)`; if `oauthClient` fails it shows the error text and a Close button (calls `onDone`, never redirects).

- [ ] **Step 1: Write failing tests** in `web/tests/oauth.test.ts`: `parseGrantRequest` returns null for another path, for `response_type=token`, for a missing `code_challenge`; returns the request for a full query including a `state` with `%26`; `savePending/loadPending` round trip and survive `sessionStorage` throwing (returns null); ConsentDialog (render with a mocked `api`): shows the client name and `redirect_host`, defaults to the Assistant preset, Allow posts `{decision: 'allow', preset: 'assistant', ...}` and navigates to the returned redirect (spy on `location.assign`), Deny posts `decision: 'deny'`, the Full access choice shows the warning text, a failing `oauthClient` shows the error and no Allow button, Escape/Close calls `onDone` without navigating.
- [ ] **Step 2: Run** `cd web && npx vitest run tests/oauth.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement** the three files. `App.svelte`: at module init call `parseGrantRequest(location)`, `savePending` it when non-null, keep it in a `$state`; when `sessionState === 'ready'` and a pending request exists render `ConsentDialog` over the palette (reuse `Modal.svelte` the way `ApiKeysDialog.svelte` does and copy its preset wording); `onDone` clears the pending request and the stored copy and `history.replaceState(null, '', '/')`. The existing effect that rewrites the signed-out URL to `/login` stays as is (the pending request lives outside the URL).
- [ ] **Step 4: Add an App-level test** in `web/tests/app-multi.test.ts` style: with `location` at `/oauth/authorize?...` and a signed-out session, the sign-in screen shows; after sign-in the consent dialog appears (this pins the Review Focus item).
- [ ] **Step 5: Run** `cd web && npx vitest run && npx svelte-check && npm run lint`. Expected: all green. Also run `npm run build` once to confirm the bundle builds.
- [ ] **Step 6: Commit** `git add web && git commit -m "feat: OAuth consent screen"`.

---

### Task 6: Docs, live-check list, and final pass

**Files:**
- Modify: `README.md` (OAuth section next to the API keys section; mention `PUBLIC_URL` should be set; the `claude mcp add --transport http aliasfox <origin>/mcp` form with no header), `docs/superpowers/plans/2026-11-17-live-check.md` (append OAuth checks), privacy page template if it lists what is stored (OAuth stores nothing new: say so only if the page enumerates data), `tests/test_api_keys_docs.py` if it pins README wording.
- Modify: `web/src/components/ApiKeysDialog.svelte` only if the dialog should hint that "(OAuth)" rows are connections (one sentence in the intro text; skip if not natural).

- [ ] **Step 1: Write the docs.** README: what OAuth adds, that tokens are ordinary keys named "<client> (OAuth)", revoke in API keys, no refresh tokens, redirects limited to https and loopback, single worker caveat (codes in memory). Live-check items: Claude Code with no header completes the flow and lists aliases; revoking the "(OAuth)" key stops it; claude.ai custom connector adds `<origin>/mcp` and lists aliases (record what it requires, especially `expires_in`/refresh); ChatGPT connector (optional); denied consent returns the client to its callback with `access_denied`; `PUBLIC_URL` unset on Render yields an https origin in the metadata.
- [ ] **Step 2: Run** the full suites: `pytest -q` and `cd web && npx vitest run && npx svelte-check && npm run lint && npm run build`. Expected: all green.
- [ ] **Step 3: Review the branch diff** against the spec sections 3 to 8; confirm no secret, key or code value is logged anywhere (`grep -n "log\." src/aliasfox/oauth*.py`).
- [ ] **Step 4: Commit** `git commit -am "docs: OAuth for MCP, live-check items"`.

## Self-review notes

- Spec coverage: section 4 endpoints -> Tasks 2, 3, 4; section 5 client id -> Task 1 (+ register in Task 2); section 6 codes/approve/token -> Tasks 1, 3, 4; section 7 consent -> Task 5; section 8 threats -> Review Focus tests in Tasks 1 to 5; section 9 testing and live checks -> every task plus Task 6.
- Not in any task on purpose: refresh tokens, revocation endpoint, custom URI schemes, `scope` parameter handling (spec non-goals).
- Known shortcut: authorization codes live in process memory (single worker); a restart makes an in-flight authorization fail and the client retries.
