# API keys: spike notes (Task 0)

Date 2026-11-23. Research only; no production code, no dependency changes.

## 1. Bitwarden SimpleLogin forwarder (verified against bitwarden/clients, main)

Sources: `libs/tools/generator/core/src/integration/simple-login.ts`,
`libs/tools/generator/core/src/engine/rpc/create-forwarding-address.ts`,
`libs/common/src/tools/integration/rpc/rest-client.ts`,
`libs/common/src/tools/integration/integration-context.ts`, `libs/common/src/tools/url-safety.ts`.

| Item | Bitwarden's own code |
|---|---|
| Method + path | `POST` `<baseUrl>` + `/api/alias/random/new` |
| Server URL | The user's URL is used as-is. `/api` is NOT appended by the URL setting; the client appends the whole `/api/alias/random/new`. So the server URL is the AliasFox origin. A trailing slash is not stripped in what I read (would give `//api/...`) (unverified; the setup text should say "no trailing slash"). |
| Auth header | `Authentication: <token>` (the token verbatim, no `Bearer`). Not `Authorization`. |
| Query | `?hostname=<hostname>` only when the site hostname is non-empty (not URL-encoded by the client); otherwise no query at all. |
| Body | JSON `{"note": "<generated-by text>"}`, `Content-Type: application/json`, `Accept: application/json`. |
| Other request options | `redirect: "manual"`, `cache: "no-store"`. A redirect (http to https, trailing-slash) is therefore never followed: serve the exact path with no redirect. |
| Success | Status 200 OR 201 with a JSON body; reads `json.alias` (string). Anything else (missing `alias`) shows "Unknown SimpleLogin error occurred." |
| Errors | 401: reads error text, shows "Invalid SimpleLogin API key: <msg>" style text (`forwarderInvalidTokenWithMessage`). 403: "Invalid operation" with message (`forwarderInvalidOperationWithMessage`). Any other status >= 400: `forwarderError` = "SimpleLogin error: <msg>". The message is taken from the body: JSON string, or JSON object `error` and/or `message` (both present are joined as `error: message`), else plain text if it has no `<`, else the HTTP status text. Non-JSON bodies are fine. |
| URL safety | The configured server URL must be `https:` and not localhost / private / link-local IP, unless the user explicitly approves an unsafe URL. A self-hosted `http://` AliasFox needs that approval. |
| CORS | The request is a `fetch` with a custom header and JSON content type, so in a browser it triggers a preflight (`OPTIONS`, expecting the `Authentication` and `Content-Type` headers allowed). The browser extension has `host_permissions` for all `https://*/*` and `http://*/*` (manifest.v3.json), so CORS does not bind it. Desktop (Electron) and CLI (Node) are not CORS-bound either. The web vault does run under browser CORS and a CSP: the dev-server CSP in `apps/web/webpack.base.js` lists `connect-src` only for known forwarder hosts (e.g. `https://app.simplelogin.io/api/alias/random/new`), so a custom host is probably blocked by the web vault's own CSP regardless of our CORS (production CSP is in the server repo, unverified). |

Third-party hme-bitwarden claims (path, header, `note` body, 201 + `alias`, `{"error"}`) all match Bitwarden's source.

### Differences from spec

Spec section 7 is correct on path, header, query, body, status, response field and error shape. Differences and refinements to apply:

1. Status: spec says 201; Bitwarden accepts 200 or 201. Keep 201 (compatible). No change needed.
2. Error shape: `{"error": "<message>"}` is right, but Bitwarden also reads `message` and plain text; for 401 and 403 it prefixes its own wording, so our text should not repeat "Invalid API key" redundantly (cosmetic; keep it short).
3. "Whether Bitwarden appends `/api`" is now verified: it does NOT append `/api` to the URL setting; it appends `/api/alias/random/new`. Server URL = AliasFox origin with no trailing path or slash. Replace "(unverified)" in sections 7 and 14.
4. Add: respond to that exact path with no redirect (client uses `redirect: "manual"`).
5. Add: the server URL must be https and public, otherwise the user must approve an unsafe URL in Bitwarden. Put in the setup text.
6. CORS (section 8): needed only for browser contexts (web vault), where Bitwarden's own CSP probably blocks custom hosts anyway (unverified). Keep the cheap preflight handling; extension, desktop and CLI do not need it. `Allow-Headers` must include `Authentication` and `Content-Type` (already in spec).
7. Hostname arrives unencoded and may be empty; label fallback "Bitwarden" in spec is right.

No other differences; spec section 7 contract values stay as written.

## 2. MCP SDK spike (scratch dir outside the repo)

Built a stateless, JSON-response server: `FastMCP("t", stateless_http=True, json_response=True)`, `streamable_http_app()`, run with `uv run --python 3.13 --with mcp` (resolved `mcp 1.28.0` then). Called over plain HTTP with curl:

- `initialize` returns 200 `application/json` (protocolVersion echoed, serverInfo), no session id needed.
- `tools/list` returns the tool with generated `inputSchema` and `outputSchema`.
- `tools/call` returns `content` + `structuredContent`, `isError: false`.
- Client `Accept: application/json` alone also worked in JSON mode.

Per-request auth: possible, two ways, both tested.
1. A Starlette/ASGI middleware on the app that checks `Authorization: Bearer ...`, rejects with 401 itself, and sets a `ContextVar` (user); the tool read it (`cv=user42`).
2. Inside a tool, `ctx.request_context.request.headers["authorization"]` exposes the raw Starlette request (worked).
Mounting under FastAPI additionally needs the SDK's session manager lifespan wired in (not tried) (unverified).

Dependencies the `mcp` package would add to this repo's environment (checked with `uv pip install --dry-run` on the repo venv, Python 3.14):
- `mcp<2` (1.30.0): 9 packages: attrs, httpx-sse, jsonschema, jsonschema-specifications, mcp, python-multipart, referencing, rpds-py (Rust wheel), sse-starlette.
- latest (`mcp 2.3.0`): 12 packages, adds mcp-types, httpx2, httpcore2, truststore; it moves to a different HTTP stack (`httpx2`), so the major line is still moving.
Already present and reused: starlette, pydantic, pydantic-settings, anyio, httpx, uvicorn.

### Decision: hand-written handler (no `mcp` dependency)

Reasons:
- We need only `initialize`, `tools/list`, `tools/call`, `notifications/*` (202) and `ping`, with JSON responses and no sessions: about 60 lines of FastAPI.
- The SDK adds 9 to 12 packages including a Rust-built wheel (`rpds-py`), `jsonschema`, `sse-starlette` (SSE we do not use), and a major version that already swapped its HTTP client; each is supply-chain surface in a security-sensitive service.
- Auth, scope checks per tool and the "user comes from the key" rule are simpler as plain dependency code (`api_user`) than as ContextVar plumbing through an SDK app mounted under FastAPI.
- SDK would give schema generation for free, but our five tools have fixed small schemas written by hand.
Cost: we must follow the spec ourselves: JSON-RPC error codes (-32601 unknown method, -32602 bad params), tool errors as `isError: true` results, echo a supported `protocolVersion`, return 405 for GET on `/mcp`. Test these against the real SDK client or curl before release.
