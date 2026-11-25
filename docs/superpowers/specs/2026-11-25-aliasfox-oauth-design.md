# AliasFox OAuth for MCP: design

Status: DRAFT, 2026-11-25. Nothing built. Extends `2026-11-23-aliasfox-api-keys-design.md` (phase 2 there).

## 1. Intent

Let OAuth-only MCP clients (claude.ai custom connectors, ChatGPT, Claude Code without `--header`, Cursor)
connect to `/mcp`. The user signs in to AliasFox as usual, sees a consent screen, picks what the client may do,
and approves. API keys stay as they are; OAuth is an additional way to get one. (owner, 2026-11-23: "in addition
to keys, never instead of them")

## 2. Core idea: an OAuth grant mints an ordinary API key

The token endpoint returns a freshly created `afk_` key as the `access_token`. Everything after that is the
existing path: `KeyAuth`, scopes, per-key limits, `/mcp`, the cache, revocation, the kill switch, account
deletion. The grant appears in the user's API keys list as `"<client name> (OAuth)"`; revoking that row ends the
connection at once. (owner chose this over a separate "Connected apps" list, 2026-11-25.)

Consequences, accepted:
- The token is valid on `/api/v1` and the Bitwarden endpoint too, within its scopes (it is a key). The consent
  screen says so in one line.
- No refresh tokens and no expiry by default (same as keys). The client keeps the token until the user revokes
  it. If a client sends `refresh_token` grants we answer `unsupported_grant_type`. (my call; alternative is a
  1-year expiry plus refresh tokens, more code and more state)

## 3. Goals and non-goals

Goals
- Works with MCP authorization (2025-06 spec): protected resource metadata, authorization server metadata,
  dynamic client registration (RFC 7591), authorization code + PKCE S256, public clients.
- No new database table and no new dependency.
- Open redirects and code theft are not possible: redirect URIs are bound to a signed client id; PKCE required.
- Single and multi mode both work; the grant belongs to whoever is signed in.

Non-goals
- Refresh tokens, token introspection/revocation endpoints (RFC 7009), client secrets, `plain` PKCE.
- Custom URI schemes as redirects (`cursor://`). Those clients can still use a static key. (my call, YAGNI)
- Honouring the `scope` request parameter. The user always picks a preset on the consent screen. `scope` and
  `resource` are accepted and ignored except that `resource`, when present, must equal `<origin>/mcp`.
- Showing "verified" status. Any client can register; the user decides from the name and redirect host.

## 4. Endpoints

All under the public origin (`PUBLIC_URL`, else derived from the request like other URLs). All answer 503
`api_keys_disabled` when `API_KEYS_ENABLED=false`.

| method, path | auth | purpose |
|---|---|---|
| GET `/.well-known/oauth-protected-resource` and `.../oauth-protected-resource/mcp` | none | `{resource: <origin>/mcp, authorization_servers: [<origin>], bearer_methods_supported: ["header"]}` |
| GET `/.well-known/oauth-authorization-server` | none | issuer, `authorization_endpoint` `<origin>/oauth/authorize`, `token_endpoint`, `registration_endpoint`, `response_types_supported ["code"]`, `grant_types_supported ["authorization_code"]`, `code_challenge_methods_supported ["S256"]`, `token_endpoint_auth_methods_supported ["none"]` |
| POST `/oauth/register` | none, rate limited | dynamic client registration, returns a signed `client_id` |
| GET `/oauth/authorize` | (SPA page) | the consent screen, served by the existing SPA fallback |
| GET `/api/oauth/client` | cookie only | validate `client_id` + `redirect_uri`; returns `{name, redirect_host}` for display |
| POST `/api/oauth/approve` | cookie only | user decision; returns `{redirect}` (code or `access_denied`) |
| POST `/oauth/token` | none, rate limited | exchange code + PKCE verifier for the key |

`/mcp` 401 responses gain `WWW-Authenticate: Bearer resource_metadata="<origin>/.well-known/oauth-protected-resource"`
(keep `Bearer`). This is how clients discover everything.

CORS and the origin guard: `/oauth/register`, `/oauth/token` and the two `.well-known` documents join the key
path set (no cookies, `Access-Control-Allow-Origin: *`, no credentials, origin check skipped). `/api/oauth/*`
stay on the normal guarded cookie path. `/oauth/authorize` is an ordinary page.

## 5. Client id (stateless)

`client_id = "afc1." + b64url(json{"n": name, "r": [redirect_uris]}) + "." + b64url(HMAC-SHA256(k, payload))`
with `k = derive_key(secret, info=b"aliasfox-oauth-client-v1")`. Max length 2048. Registration therefore stores
nothing; verification recomputes the HMAC (constant time) and decodes. Rotating `ALIASFOX_SECRET_KEY`
invalidates client ids (clients re-register on the next 401/invalid_client; documented, same as keys).

Registration limits: at most 5 redirect URIs, each <= 300 chars; name 1..80 chars after stripping control
characters (default `"MCP client"`); per-IP 20 registrations per hour. Redirect URI rule: `https` with a host,
or `http` with host `127.0.0.1`, `localhost` or `[::1]` (any port, for CLI clients); no fragment; no userinfo.
Anything else: 400 `invalid_redirect_uri`. Response is 201 with `client_id`, `client_name`, `redirect_uris`,
`token_endpoint_auth_method: "none"`, `grant_types`, `response_types`.

## 6. Authorization code

In-memory `CodeStore` (single worker, like every limiter here): `code -> {user_id, scopes, client_id,
redirect_uri, challenge, name, expires}`; 32 random bytes urlsafe; TTL 60 s; cap 1000 entries (oldest dropped);
`pop` is atomic so a code works once. A restart drops codes; the client just retries the flow.

Approve (`POST /api/oauth/approve`, body `{client_id, redirect_uri, state?, code_challenge,
code_challenge_method, preset, decision, resource?}`): re-validates the client id and that `redirect_uri` is
one of its URIs (exact string match), requires `code_challenge_method == "S256"` and a 43-char base64url
challenge, `preset` in `readonly|assistant|full`, then returns
`{redirect: "<redirect_uri>?code=...&state=..."}` or, for deny, `?error=access_denied&state=...` (query
appended correctly if the URI already has one; `state` url-encoded and echoed untouched).

Token (`POST /oauth/token`, `application/x-www-form-urlencoded`): `grant_type=authorization_code`, `code`,
`redirect_uri`, `client_id`, `code_verifier` (43..128 chars). Checks in this order: grant type, code exists and
unexpired (popped), client id equals the code's, redirect URI equals the code's, SHA-256 verifier matches the
challenge (constant time). Any failure: 400 `{"error": "invalid_grant"}` (`unsupported_grant_type` /
`invalid_request` for malformed input). Success: `ApiKeyStore.create(user_id, "<name> (OAuth)", scopes,
preset=None)` and `{access_token, token_type: "Bearer", scope: "<space separated scopes>"}` with
`Cache-Control: no-store`. `TooManyKeys` -> 400 `invalid_request` with a description telling the user to
delete an old key. Failed attempts per IP are rate limited (20/min).

## 7. Consent screen (SPA)

`/oauth/authorize?client_id&redirect_uri&state&code_challenge&code_challenge_method&response_type=code[&scope][&resource]`.
The SPA reads and keeps the request in memory and `sessionStorage` (so sign-in, sign-up and the email code
step, which rewrite the URL, do not lose it), calls `GET /api/oauth/client`, and once signed in shows:

- "**<name>** wants to connect to your AliasFox", the redirect host in plain sight ("it will send you back to
  `<host>`").
- Preset choice: Read only, Assistant (default, no delete), Full access (warning). Same wording as API keys.
- One line: "This creates an API key named '<name> (OAuth)' that you can revoke any time under API keys. It also
  works with the REST API within the scopes you pick."
- Allow / Deny. An invalid client or redirect shows an error and never redirects anywhere.

After the decision the page navigates to the returned `redirect`.

## 8. Threat model additions

- Malicious client registers with a name like "Claude": it can only get a code if a signed-in user clicks Allow;
  the redirect host and the preset are shown. Residual risk is the user, same as any consent screen.
- Redirect to attacker: impossible, `redirect_uri` must equal a URI inside the signed client id, and both
  endpoints re-verify the signature. An attacker can register their own URI, which only matters if the user
  approves it.
- Code interception: PKCE S256 required, 60 s, single use, bound to client id and redirect URI.
- CSRF on approve: cookie path, so the normal origin guard applies. Key/OAuth tokens cannot call it (cookie
  only, `/api/oauth/*` is not a key path). A test enforces this.
- Token in URL: never. The token only travels in the token response body.
- Flooding: registration 20/h/IP, token failures 20/min/IP, code store capped.

## 9. Testing

Unit: client id round trip and tamper cases, redirect rule table, PKCE, CodeStore expiry/single use/cap.
Route: metadata documents, registration validation, a full register -> approve -> token -> `/mcp tools/list`
flow, each token failure branch, kill switch, key-cannot-call-approve, CORS preflight on token and register,
`WWW-Authenticate` on `/mcp` 401. Web: request parsing, consent states, approve/deny navigation. Live check
(owner): `claude mcp add --transport http aliasfox <origin>/mcp` with no header (Claude Code runs the OAuth flow
via a loopback redirect), then claude.ai custom connector.

## 10. Unverified

- Exactly what claude.ai and ChatGPT send and require (DCR vs fixed client id, `resource`, response to a
  token with no `expires_in` or refresh token). The design follows the MCP authorization spec; the live check
  decides. If a client insists on `expires_in` or refresh tokens, that becomes a follow-up.
- Whether claude.ai's callback host is accepted by our redirect rule (it is https, so yes by design).
- Render's proxy headers for deriving the origin when `PUBLIC_URL` is unset (set it).
