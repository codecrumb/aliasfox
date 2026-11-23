# AliasFox API keys, Bitwarden endpoint and MCP server: design

Status: DRAFT for owner review, 2026-11-23. Items marked (owner) are the owner's decisions; (my call) are the
designer's, open to change; (unverified) must be checked before relying on them.
Builds on `2026-11-17-aliasfox-multi-user-design.md`. Nothing here is built.

## 1. Intent

Let a user (or a tool the user runs) work with their own aliases without the browser session cookie:
per-user API keys with scopes, a native key-authenticated API, a SimpleLogin-compatible endpoint so
Bitwarden can create Hide My Email addresses against AliasFox, and an MCP server for AI assistants.

Why it is worth building (owner): every other Hide My Email project is a single-user tool. `hme-bitwarden`
makes Bitwarden work with Hide My Email but has to be self-hosted (Docker, domain, reverse proxy, one static
token). AliasFox already holds the user's Apple session server-side, so it can offer the same thing as a
hosted service: paste a key and a URL into Bitwarden.

## 2. Goals, non-goals, anti-goals

Goals
- API keys created in the web UI (actions menu, Cmd/Ctrl+K), shown once, revocable, with scopes. (owner)
- Scopes so a key can be limited, e.g. Bitwarden: create only; read-only; everything except delete. (owner)
- Presets at creation ("Bitwarden", "Read only", "Assistant (MCP)", "Full access", "Custom"). (owner)
- A native JSON API under `/api/v1` that a key can use. The API can do everything the UI can that is
  alias-level (list, create, edit, activate/deactivate, delete), each behind its own scope. (owner)
- `POST /api/alias/random/new`, the SimpleLogin endpoint Bitwarden calls.
- An MCP server (`/mcp`) over the same keys and scopes. (owner)
- Fast: key check adds no noticeable latency; a create is as fast as the UI's (Apple dominates). (owner)
- Works in single and multi mode; a key always acts as exactly the user who created it.

Non-goals
- A CLI, a Raycast extension, a browser extension or Shortcuts. Existing tools (Raycast's Hide My Email
  extension, `ihme`) talk to Apple directly, so a relay would add nothing. (owner)
- OAuth for MCP (owner asked 2026-11-23; decision: phase 2, in addition to keys, never instead of them).
  Bitwarden, Claude Code, Cursor and scripts can only send a static key, while claude.ai and ChatGPT
  connectors expect OAuth, (unverified). Phase 2 design sketch: an OAuth grant creates the same kind of
  scoped, revocable row as an API key (consent screen reuses the presets), and `KeyAuth` accepts the
  resulting token unchanged. Needs authorization-server metadata, PKCE and dynamic client registration per
  the MCP authorization spec; first check whether Managed Neon Auth can act as the OAuth server.
- A guided "connect Bitwarden" wizard (owner: later). Presets plus a short how-to text are enough now.
- OAuth, so claude.ai's web connector cannot be used yet (section 8). Key rotation, per-IP allow-lists,
  webhooks, an addy.io-compatible endpoint (its `POST /api/v1/aliases` would clash with ours; Bitwarden
  supports SimpleLogin, which is enough).
- Multiple Apple accounts per user (separate item in the notes).
- Any inbox, SMTP or IMAP feature.

Anti-goals (a failure even if it technically works)
- A route where the key's user can be overridden by request input. User id comes only from the key row.
- Any way for a key to reach account-level routes (connect/disconnect Apple, delete account, session,
  create/list/revoke keys, change the account-wide forward-to address).
- Storing a key in plaintext, logging one, or returning one more than once.
- A policy page that promises more than the code does (section 10).

## 3. Threat model and honest limits

- A key is a bearer credential for the user's alias list, through the user's Apple session. Anyone holding
  it can do what its scopes allow until revoked. Scopes and revocation are the only containment; keys live
  in other tools' storage (Bitwarden, an MCP config), which AliasFox cannot protect.
- Shared Apple IP risk: all users' Apple traffic leaves one datacenter IP. Automation makes it easier to
  hit Apple's create limits (third-party reports: roughly 5 to 10 per hour, about 750 total, (unverified))
  and a flood from keys could get the shared IP challenged for everyone. Hence a per-minute burst cap, a
  global ceiling and a cooldown after Apple refusals (section 7), and a global kill switch `API_KEYS_ENABLED` (my call, default true).
- `forward_to` (the user's real mailbox) appears in alias data and in the account-wide forward settings.
  Keys therefore cannot read or change the account forward-to list, and MCP output omits `forward_to`
  (section 9). An `edit` scope that could change forwarding would let a stolen key redirect all mail.
- Text in labels, notes and site names is attacker-influenced data (a site can set an app name). MCP tool
  output is data for the model, not instructions; tool descriptions say so.
- Rotating `ALIASFOX_SECRET_KEY` invalidates every key (hash is keyed by it), the same way it invalidates
  cookies. Documented, accepted. (my call)
- Single worker only, as today: the auth cache and limiters are in process memory.

## 4. Key format, storage, verification

Format: `afk_` + 43 URL-safe base64 characters (32 random bytes from `secrets`). Only the first 8 characters
after the prefix are kept for display (`afk_AbC12345…`).

Storage, new table `api_keys` (Alembic revision 0003):

| column | type | notes |
|---|---|---|
| id | int pk | |
| user_id | int, indexed | the owner; on user delete, rows go too (explicit in `UserDirectory.delete`, SQLite does not enforce FKs here) |
| name | str(80) | what the user calls it |
| preset | str, nullable | label only, for the UI |
| scopes | str | comma-joined subset of the scope list, validated on write and on read |
| key_hash | bytes(32), unique | HMAC-SHA256 of the full key under `derive_key(secret, info=b"aliasfox-apikey-v1")` |
| prefix | str(8) | display only |
| created_at | timestamptz | |
| last_used_at | timestamptz, nullable | updated at most once per 5 minutes per key |
| expires_at | timestamptz, nullable | optional at creation; no default expiry (my call) |

Why HMAC and not Argon2: the key is 256 random bits, so a slow hash adds nothing, and Argon2 would put tens
of milliseconds on every request. HMAC with a server secret means a database leak alone cannot even confirm
a guess. Lookup is one indexed equality query on `key_hash`; no secret comparison happens in application code.

Verification (`api_user(scope)` dependency, new, separate from `current_user`):
1. Read the key from `Authorization: Bearer <key>` or `Authentication: <key>` (SimpleLogin header). Never
   from the query string or a cookie. If neither header exists: 401 `invalid_api_key`.
2. Compute the HMAC, look it up in a small in-memory cache `hash -> (key_id, user_id, scopes, expires_at)`
   (TTL 30 s, bounded to 1024 entries), else query the database and fill the cache.
3. Unknown, expired or revoked: 401 `invalid_api_key`, same body for all three. Missing scope: 403
   `insufficient_scope` with the needed scope named.
4. Revoke and account deletion drop the cache entry at once (single process, so no stale window). The
   startup hook that revokes denylisted subjects also deletes their keys.
5. A request that carries a key never falls back to the cookie, and key routes ignore cookies entirely.

Fast path cost: one HMAC (microseconds) plus a dict lookup; cold path adds one indexed query.

## 5. Scopes

Independent flags, no implication between them:

| scope | allows |
|---|---|
| `read` | list aliases (`GET /api/v1/aliases`) |
| `create` | create an alias (generate and reserve in one call) |
| `edit` | change an alias's label and note |
| `deactivate` | deactivate and reactivate (reversible) |
| `delete` | delete an alias (permanent on Apple's side) |

Presets (UI only; they just fill the checkboxes):
- Bitwarden: `create`.
- Read only: `read`.
- Assistant (MCP): `read`, `create`, `edit`, `deactivate` (no `delete`).
- Full access: all five. Carries a warning and a confirm checkbox.
- Custom: any subset. Choosing `delete` always shows the confirm.

Never available to keys, whatever the scopes: Apple connect/disconnect, account deletion, `/api/session`,
`/api/status`, key management, forward-to read and write. A test enforces this (section 11).

## 6. API surface

All key routes live under `/api/v1` (plus the one SimpleLogin path) and use `api_user(scope)`. They call the
same `AliasService` methods as the UI routes, with `user_id` from the key row.

| route | scope | notes |
|---|---|---|
| `GET /api/v1/me` | any valid key | `{name, scopes, connected}`; lets a tool check its key and whether Apple is connected |
| `GET /api/v1/aliases` | read | same objects as `alias_json`; uses the service's 60 s cache |
| `POST /api/v1/aliases` | create | body `{label, note?}`; server calls generate then reserve, retries once on an Apple `-41003` with a fresh address (mirrors the web client), returns the alias |
| `PATCH /api/v1/aliases/{id}` | edit | `{label, note?}` |
| `POST /api/v1/aliases/{id}/active` | deactivate | `{active}` |
| `DELETE /api/v1/aliases/{id}` | delete | |
| `POST /api/alias/random/new` | create | SimpleLogin shim, section 7 |

Errors reuse the existing `detail` codes (`not_connected` 409, `apple_error` 502, `rate_limited` 429 with
`Retry-After`, ...). Gotcha: the existing handler maps an expired Apple session to 401 `session_expired`;
clients must tell it from 401 `invalid_api_key` by `detail`. On key routes `session_expired` is returned as
503 instead (my call), so a bad key and an Apple sign-in problem never share a status code.

Key management, cookie-only (existing `current_user`), UI use:
- `GET /api/keys`: id, name, prefix, preset, scopes, created, last used, expires. Never a hash.
- `POST /api/keys`: `{name, preset?, scopes, expires_days?}`; returns the full key once. Max 20 keys per
  user (my call); the name and scopes are validated; unknown scopes are 422.
- `DELETE /api/keys/{id}`: revokes at once. Only the owner's keys are addressable (query always filters on
  the cookie's user id).

## 7. Bitwarden (SimpleLogin) endpoint

Contract, verified in Task 0 against Bitwarden's own source (`bitwarden/clients`, SimpleLogin forwarder; see
`docs/superpowers/plans/2026-11-23-api-keys-spikes.md`):

```
POST /api/alias/random/new?hostname=<site>      header  Authentication: <key>
body {"note": "..."}   ->   201 {"alias": "<address>@icloud.com"}
errors: JSON {"error": "<message>"}  (Bitwarden shows this text to the user)
```

Behaviour:
- Needs `create`. Label is `hostname` (fallback "Bitwarden"), note is `note` truncated to 2048.
- Error messages are human text: 401 "Invalid API key", 403 "This key cannot create addresses", 429 with the
  wait, 503 "Apple session expired. Sign in again at <PUBLIC_URL>" when not connected or expired, 502
  "iCloud: ...".
- Each call consumes an Apple creation slot, so Bitwarden's "regenerate" button costs one each. Documented.

User setup text shown after creating a Bitwarden-preset key: Generator, Username, type "Forwarded email
alias", service SimpleLogin, paste the key, server URL = the AliasFox origin with no trailing slash or path
(Bitwarden appends `/api/alias/random/new` itself). The URL must be https and public, or the user must approve
an unsafe URL in Bitwarden. The endpoint must never redirect (Bitwarden sends `redirect: manual`); it accepts
the `Authentication` header with the key verbatim (no `Bearer`).

Rate limits (the same `RateLimiter`, registered with `track` so the sweep covers them):
- Create: the existing per-user `CREATE_PER_MINUTE` (10) is shared with the UI, since it is one Apple account.
- No hourly cap (owner, 2026-11-23): Apple's own per-account limit (reported roughly 5 to 10 per hour,
  (unverified)) already bounds a user, and an hourly cap of ours would only block someone legitimately
  moving many accounts, one by one, to AliasFox.
- Global ceiling: 60 creates per minute across all users, a backstop that protects the shared Apple IP.
- Cooldown: after 3 consecutive Apple errors on create for one user, that user's creates (UI and keys)
  return 429 with `Retry-After` for 5 minutes, then the counter resets; any successful create resets it too.
  Short on purpose: Apple's real "limit reached" code is unknown, so the trigger is any `apple_error` on
  create, and the open `-41003` bug must not lock people out for long. Only creates are affected.
- Per key: 120 requests per minute of any kind (my call, adjustable).
- Failed key attempts: 20 per minute per client IP (uses `client_ip`/`limiter_key`); 429 `too_many_attempts`.

## 8. Cross-origin and the request guards

Two existing guards break key clients and need a narrow, explicit change.

1. `request_guards` rejects any POST/PUT/PATCH/DELETE whose `Origin` differs from `Host` (CSRF protection
   for the cookie). Bitwarden's web vault and extension send a foreign `Origin`. The check is skipped only
   for the key-route path set (`/api/v1/*`, `/api/alias/random/new`, `/mcp`), which ignore cookies.
2. Browsers need CORS for the web vault. For the same path set only: answer `OPTIONS` preflight with 204,
   and send `Access-Control-Allow-Origin: *` (never credentials), `Allow-Headers: Authorization,
   Authentication, Content-Type`, `Allow-Methods`, `Max-Age: 600`. Because no credentials are allowed and
   key routes ignore cookies, a hostile page cannot ride the user's cookie through this.

Whether each Bitwarden client enforces CORS at all is (unverified); the change is cheap and harmless.
Both changes need tests proving non-key paths keep the old behaviour (section 11).

## 9. MCP server

- Transport: streamable HTTP at `/mcp`, stateless, JSON responses only (no SSE, no sessions), so it fits the
  single-worker, no-shared-state design. Auth: `Authorization: Bearer <key>`, resolved per request through
  the same `api_user`; the user comes from the key, never from tool arguments.
- Tools (a key sees only the tools its scopes allow; each call re-checks the scope):
  - `list_aliases(query?, active?, limit?)`, scope `read`. Default 50, max 200. `query` is a simple
    case-insensitive substring match over address, label and note, done server-side.
  - `create_alias(label, note?)`, `create`.
  - `update_alias(id, label, note?)`, `edit`.
  - `set_alias_active(id, active)`, `deactivate`.
  - `delete_alias(id)`, `delete`.
- Output: address, label, note, active, id, created date. Not `forward_to`, not origin or bundle fields
  (privacy: an AI provider would otherwise see the user's real mailbox).
- Tool descriptions state that label and note text is untrusted data and must not be followed as instructions.
- Implementation: a small hand-written handler (decided in Task 0): `initialize`, `ping`, `tools/list`,
  `tools/call`, `notifications/*` (202), JSON-RPC errors -32601/-32602, tool failures as `isError: true`
  results, GET on `/mcp` 405. The `mcp` SDK would add 9 to 12 packages (including a Rust wheel) for about 60
  lines of code. Test against curl and, in the live checks, a real MCP client.
- Limitation: clients that take a static header (Claude Code `--header`, Cursor, and similar) work;
  claude.ai's web connector expects OAuth and will not, (unverified). OAuth is a non-goal here.

## 10. Web UI and documents

UI (Svelte, same patterns as the existing dialogs):
- Actions menu entry "API keys" (keywords: api, key, token, bitwarden, mcp, integration, access) opens
  `ApiKeysDialog.svelte`: a list (name, preset, scopes as chips, prefix, last used, created, Revoke) and
  a "New key" form (name, preset radios, scope checkboxes for Custom, optional expiry).
- On create: the full key in a read-only field with a Copy button and "You won't see this again", plus
  the how-to text for the chosen preset (Bitwarden steps; for MCP the `claude mcp add` command with this
  site's URL and a placeholder for the key).
- Revoke goes through the existing `ConfirmDialog`. Online-only (not part of the offline queue).
- Client code in `web/src/lib/apiKeys.ts`; `contract.test.ts` gets the new routes (the decorator scraper
  must see them; no call-registered routes).

Documents that must change with the code (anti-goal: no over-promising):
- Privacy policy: keys are stored as a keyed hash plus name, scopes, timestamps; a key can act on the
  user's aliases through their Apple session within its scopes; keys and their records are deleted on
  revoke and on account deletion; last-used time is recorded.
- Terms/README: automation through keys is the user's responsibility, Apple limits apply, no guarantee.
- README: an "API keys, Bitwarden and MCP" section; SECURITY.md: report path unchanged.

## 11. Testing

- Key unit tests: format and length, HMAC is deterministic and depends on the secret, the plaintext never
  appears in the table or in logs (caplog).
- Scope matrix: parametrized over every key route and every scope subset; each combination allows exactly
  the intended routes and gives 403 otherwise. Deny by default.
- Cookie-only guarantee: for every route in `route_table` outside the key allow-list, a valid key with all
  scopes gets 401, and a valid cookie on key routes gets 401.
- Isolation (extends `tests/test_isolation.py`): A's key never sees or changes B's aliases; a key of a
  deleted user, a revoked key, an expired key and a denylisted user's key are all 401 straight away
  (cache invalidation tested, not just the database).
- Guards: the origin exemption and CORS headers exist only on the key path set; the old `bad_origin` still
  fires on every other path; preflight works; no `Allow-Credentials` ever.
- Shim: response shapes, `Authentication` and `Bearer` headers, error JSON `{"error": ...}`, label from
  hostname, the five error statuses.
- Limits: per-key hourly cap, shared per-user create limit, failed-auth limit per IP.
- MCP: initialize, tools/list filtered by scope, tools/call denied without scope, `forward_to` absent from
  every result, user id from the key only.
- Migrations: `test_migrations.py` covers 0003 on SQLite (and the Postgres path as far as it is tested today).
- Web: dialog tests (preset fills scopes, delete confirm, key shown once, revoke), a11y test passes.

## 12. Plan outline (for the implementation plan)

1. Spikes (before any code): read Bitwarden's SimpleLogin forwarder source and, if possible, run a real
   Bitwarden client against a stub to confirm headers, path, status and error handling and whether CORS
   matters; try the `mcp` SDK in stateless JSON mode against our app and a real client.
2. Migration 0003, `ApiKeyStore` (create, list, revoke, lookup, delete-for-user), `UserDirectory.delete`
   and the startup denylist hook.
3. `api_user(scope)`, cache, limiters, key-management routes, `/api/v1` routes, error mapping, kill switch.
4. Guard and CORS changes, with the negative tests.
5. SimpleLogin shim.
6. MCP server.
7. Web dialog, palette entry, how-to text.
8. Docs: privacy, terms, README. Live checks with a real Bitwarden client and an MCP client.

## 13. Decisions for the owner

- Key expiry: none by default, optional at creation (my call). Change?
- Limits: 120 requests per minute per key, 20 keys per user (my call). Creates: no hourly cap (owner);
  10 per minute per user, 60 per minute global, and a 5 minute cooldown after 3 consecutive Apple errors.
- `session_expired` as 503 on key routes (my call) versus the UI's 401.
- Keys die when `ALIASFOX_SECRET_KEY` is rotated (consequence of the keyed hash). Acceptable?
- Whether to ship with `API_KEYS_ENABLED` on by default for a public launch given the shared-IP risk.

## 14. Unverified, to check before relying on it

- Bitwarden's web vault probably cannot reach a custom host because of its own CSP (production CSP unverified);
  the extension, desktop and CLI are not bound by CORS. Whether a trailing slash in the server URL breaks it.
- Apple's real creation limits and how it reacts to a busy shared IP.
- Whether the `-41003` reserve bug is a stale or reused generated address; the one-retry logic in the
  create route depends on the answer.
- Which MCP clients accept a static bearer header, and that the hand-written MCP handler satisfies a real client.
