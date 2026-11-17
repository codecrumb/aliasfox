# AliasFox

AliasFox is a small self-hosted service for managing your iCloud "Hide My Email" aliases:
list, search, generate, label, deactivate and delete them from your own server. It runs in two
modes: `single` (one owner, the default) and `multi` (many users signing in through Neon Auth).

**Not affiliated with Apple. No warranty, and no guarantee of security or availability.** It is a
personal, best-effort project, licensed under AGPL-3.0-or-later (see [Licence](#licence)). It talks to undocumented iCloud web endpoints that
Apple can change or block at any time. Use it at your own risk; automated access can, in the
worst case, affect your Apple account (for example repeated failed logins can lock it).

## Quick start

Docker Compose:

```sh
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# put the value in .env as ALIASFOX_SECRET_KEY (or use `python -m aliasfox gen-secret` once installed)
docker compose up -d --build
curl -s localhost:8080/healthz     # {"status":"ok"}
```

The port is published on `127.0.0.1` only. Data lives in `./data`. On Linux the container runs as
uid 10001, so create the directory first: `mkdir -p data && sudo chown 10001:10001 data`.

Local with `uv`:

```sh
uv venv && uv pip install -e ".[dev]"
export ALIASFOX_SECRET_KEY="$(.venv/bin/python -m aliasfox gen-secret)" DATA_DIR=./data
.venv/bin/python -m aliasfox       # binds 127.0.0.1:8080
.venv/bin/pytest                   # tests
```

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `ALIASFOX_SECRET_KEY` | none, required | Encrypts stored Apple sessions and signs login cookies. At least 32 characters or the app will not start. |
| `AUTH_ENABLED` | `false` | Require an AliasFox password. Needed for any non-loopback bind. |
| `ALIASFOX_PASSWORD_HASH` | none | Argon2id hash, required when auth is on. Make one with `python -m aliasfox hash-password`. Put it in single quotes in `.env` files, e.g. `ALIASFOX_PASSWORD_HASH='$argon2id$v=19$m=65536,t=3,p=4$...'`, because `$` is interpolated otherwise; an unparsable hash refuses startup. |
| `DATABASE_URL` | SQLite in `DATA_DIR` | Postgres URL. `postgres://` and `postgresql://` are normalised. |
| `DATA_DIR` | `/data` | Where the SQLite file lives. |
| `ICLOUD_COOKIE` | none | Local testing only: seeds a session at startup if none valid is stored. |
| `TRUSTED_PROXY` | `false` | Derive the client address for rate limits from `X-Forwarded-For`. Only behind a proxy you control. |
| `TRUSTED_PROXY_HOPS` | `1` | Number of trusted proxies in front of the app: the client address is the Nth `X-Forwarded-For` entry from the right. Must match your real proxy chain (see Render below). |
| `HOST` / `PORT` | `127.0.0.1` / `8080` | Bind address. A non-loopback host without auth is refused. |
| `ALIASFOX_MODE` | `single` | `single` or `multi`; see [Multi-user mode](#multi-user-mode). |
| `ALIASFOX_ALLOW_UNAUTHENTICATED_NETWORK` | `false` | Lets a container bind `0.0.0.0` with auth off. Only with a loopback-only published port. |

## Multi-user mode

Set `ALIASFOX_MODE=multi` to let several people sign in (Neon Auth identity) and each connect their
own Apple session, stored encrypted per user. `AUTH_ENABLED` and `ALIASFOX_PASSWORD_HASH` are ignored.
Startup refuses to run unless all required settings are present and valid.

| Variable | Required | Meaning |
| --- | --- | --- |
| `ALIASFOX_MODE=multi` | yes | Turns multi-user mode on. |
| `NEON_AUTH_BASE_URL` | yes | Neon Auth base URL; it must be a plain http(s) URL, and its origin is added to the CSP `connect-src`. |
| `NEON_AUTH_JWKS_URL` | yes | JWKS URL used to verify Neon Auth tokens. |
| `NEON_AUTH_ISSUER` | no | Expected token issuer; defaults to scheme://host of `NEON_AUTH_BASE_URL` (unverified against a real token). |
| `DATABASE_URL` | yes | Postgres (for example Neon). |
| `TRUSTED_PROXY=true` | yes | Rate limits need the real client address; set `TRUSTED_PROXY_HOPS` to match your host. |
| `SIGNUPS_ENABLED` | no | `false` by default: only existing users and the allowlist can sign in. |
| `SIGNUP_ALLOWLIST` | no | Comma-separated emails always allowed to sign up. |
| `SUBJECT_DENYLIST` | no | Comma-separated Neon Auth user ids refused at sign-in (suspended accounts; see below). |
| `PUBLIC_URL` | recommended | Public origin of the deployment; https (http only for localhost). Shown on the legal pages. |
| `CONTACT_EMAIL` | yes | Contact address shown on the privacy and terms pages: one plain address (startup refuses an empty value, spaces or `<>"'`). Set by the operator; never committed. |

Before going live:

- Register the app's public origin (scheme included, no trailing slash) as a Neon Auth trusted domain,
  e.g. `neon neon-auth domain add <origin>`. The browser signs in against `NEON_AUTH_BASE_URL` directly,
  so Neon Auth's session cookie is a third-party cookie for the app; browsers that block those may fail
  at the token step (unverified).
- Neon's shared SMTP is for development only. A public launch needs your own SMTP configured in Neon Auth.
- `SIGNUPS_ENABLED=false` closes sign-up to AliasFox only, not at Neon Auth. While it is false, disable
  public sign-up in the Neon Auth settings if Neon offers that (unverified). Otherwise strangers can still
  create Neon Auth accounts and trigger verification emails from your SMTP domain, although they get no
  AliasFox access.
- Run a single worker: `WEB_CONCURRENCY` must be unset or `1` (caches and rate limits are per process;
  startup refuses more).
- Verify `TRUSTED_PROXY_HOPS` on your host (see Deploying to Render for the check).
- The landing, privacy and terms pages are served in multi mode, and users can delete their account from
  the Account view.
- Switching an existing single-mode deployment to multi mode deletes the single-mode account and its
  stored Apple session at the first multi-mode startup (the old owner cookie stops working). Reconnect
  Apple after signing up.

### Suspending a user

1. Find the user's id in Neon Auth: the user list of your project's Auth page in the Neon console
   shows each user's id (the exact place in the console is unverified). This is the token's `sub`, not
   the email address.
2. Add it to `SUBJECT_DENYLIST` (comma-separated) and restart the service to apply it.
3. From then on, that user's sign-in gets 403 `account_disabled` and no session cookie, and no AliasFox
   account is created for an id that has none. When a suspended user with an AliasFox account tries to
   sign in, every session cookie they already hold stops working. Until then a session they already
   have keeps working until it expires (at most 7 days), because session cookies do not carry the Neon
   id.
4. To remove them fully, also delete the user in Neon Auth. Their AliasFox account row and stored Apple
   session stay in the database (the user can no longer sign in to delete them); remove them there if
   needed.

### What is stored

Per user: an opaque sign-in provider user id (not the email), a wrapped data key, one encrypted Apple
session (cookies, service address, Apple account id, and a trust token when issued), timestamps and a
logout counter. Never stored: the Apple password, and aliases, labels or notes (aliases are cached in
server memory for at most about two minutes; IP addresses are in memory only, for rate limiting). Neon
holds your email address and password hash at the sign-in provider, plus a display name: the part of
the email before the @, sent at sign-up because Neon Auth requires a name. Deleting the account asks
Neon to delete that record; if the request does not go through, the app says so and it is removed on
request. The privacy page of the running
service is the authoritative statement.

### Self-hosting vs hosted

Self-hosting (single mode, your server, your key, your data) is the recommended way to use AliasFox.
Anyone running a hosted multi-user instance is a separate operator; the operator of an instance can,
technically, decrypt the stored Apple sessions of its users. Only use an instance you trust.

## Connecting to Apple

Two ways, both via the API (`/api/connect/login` + `/api/connect/verify`, or `/api/connect/cookie`):

1. Apple ID, password and the 2FA code. The password goes straight to Apple's login and is
   never stored. Security-key-only and legacy 2SA accounts are not supported.
2. Paste a cookie. Warning: a pasted cookie is your full iCloud web session, with far broader
   access than Hide My Email. Prefer option 1 where you can.

To copy the cookie: sign in at icloud.com, open DevTools, Network tab, reload, click a request
to `icloud.com`, find the `Cookie` request header and use "Copy value" (not a hand-selected
excerpt). A truncated cookie shows an ellipsis (`…`) in values, or is missing the
`X-APPLE-WEBAUTH-*` / `X-APPLE-DS-WEB-SESSION-TOKEN` cookie; AliasFox rejects both with a
"truncated or corrupted" error. Cookie-Editor JSON and `curl -H 'Cookie: ...'` lines are accepted too.

## API notes

- A 401 is overloaded. Clients must branch on `detail`: `not_authenticated` (no or revoked AliasFox
  cookie, log in again), `session_expired` (the Apple session is dead, reconnect), `apple_login_failed`
  (bad Apple credentials) and `invalid_code` (wrong 2FA code, may retry).
- Apple `success:false` replies map to 502 `apple_error`, never 401, because quota and validation
  errors use the same shape.
- 409 `not_connected` / `session_unreadable` mean reconnect. `/api/status` reports
  `connected:false` (with `reason:"session_unreadable"` when the stored session cannot be read, for
  example after the secret key changed). Reconnecting works after a secret change: the user gets a new
  data key and the unreadable session is deleted.
- 502 `apple_unreachable`: Apple could not be reached while checking a pasted cookie (the cookie
  itself is not blamed, unlike 400 `invalid_cookie`).
- 429 `rate_limited`: per-user limits on `?refresh=true` (at most one real refresh per 5 seconds, sooner
  calls are served from the cache) and on generate/reserve (10 per minute).

## Web UI development

The web app lives in `web/` (Svelte 5 + TypeScript + Vite). Node 22+ is needed.

```bash
cd web
npm ci               # install the pinned dependencies
npm run dev          # dev server; proxies /api and /healthz to http://127.0.0.1:8080
npm run build        # production build into src/aliasfox/static (git-ignored)
npm test             # vitest (jsdom): unit, accessibility (axe), contract and built-bundle tests
npm run lint         # eslint; bans {@html}, innerHTML/outerHTML, eval and friends
npm run check        # svelte-check (strict TypeScript)
```

Run the backend (`python -m aliasfox`) on port 8080 alongside `npm run dev`. Before sending a change,
run all four (`npm run lint && npm run check && npm test && npm run build`) and the backend suite
(`.venv/bin/pytest -q`).

How the bundle is served: the API process hosts the built files itself (one deployment). `/assets/*`
are content-hashed and cached forever (`immutable`); `index.html` is `no-cache`; any other path that is
not `/api/*`, `/healthz` or `/assets/*` falls back to `index.html` so deep links work. If no build
exists the API still starts and `/` is a normal 404.

| Variable | Used by | Meaning |
| --- | --- | --- |
| `WEB_OUT_DIR` | `npm run build` | Where Vite writes the bundle. Default `../src/aliasfox/static`. The Docker build sets `/out`. |
| `WEB_DIST_DIR` | the API at runtime | Where the API reads the bundle from. Default is the package's `static/` directory; the image sets `/app/static`. |

The build must stay CSP-clean (`script-src 'self'; style-src 'self'`): no inline scripts or styles, no
Svelte `transition:`/`animate:` directives. The ESLint bans on HTML-injection sinks are defence in depth
only; the CSP is the real backstop, and `web/tests/bundle.test.ts` checks the built output (no `eval`,
no new `innerHTML`, no source maps, no remote URLs; it builds the bundle first if it is missing).
`tests/test_web_e2e.py` serves that bundle through the real app with auth on. The Docker image builds the
app in a `node:22-slim` stage, so `docker build .` needs no local Node.

## Keyboard shortcuts

The search field always has focus: type to filter. This table is kept in sync with `web/src/lib/keys.ts`
(`KEYMAP`) by `web/tests/readme.test.ts`, which fails if they differ. Press `?` in the app for the same
list.

| Keys | Action |
| --- | --- |
| `Enter` | Copy address |
| `Shift` + `Enter` | Deactivate or reactivate the address |
| `Alt` + `N` | New alias |
| `Alt` + `E` | Edit label and note |
| `Alt` + `Backspace` | Delete alias — when the search box is empty or the caret is at the start |
| `Ctrl` + `K` or `⌘` + `K` | All actions |
| `↑` or `Ctrl` + `K` | Previous alias |
| `↓` or `Ctrl` + `J` | Next alias |
| `Page Up` | Up one page |
| `Page Down` | Down one page |
| `Home` | First alias (empty search) |
| `End` | Last alias (empty search) |
| `Esc` | Clear the search |
| `Alt` + `R` | Refresh from Apple |
| `Alt` + `F` | Cycle All, Active and Inactive |
| `Alt` + `S` | Cycle the sort order |
| `Alt` + `X` | Select or unselect this alias for a bulk action |
| `Alt` + `A` | Select every alias in the list (again to unselect) |
| `Shift` + `↓` | Select this alias and the next one |
| `Shift` + `↑` | Select this alias and the previous one |
| `Esc` | Clear the selection (empty search) |
| `?` | Keyboard shortcuts (empty search) |

`Ctrl` + `K` moves up on a Mac, where `⌘` + `K` opens the actions. Held keys act once, except the
navigation keys.

## Release checklist for the UI

The automated tests run in jsdom, which cannot cover these. Check them by hand in real browsers
before a release:

- [ ] Option+N (new alias) and Option+E (edit) on macOS in Safari and in Chrome: the dialog opens and no
      stray character (a dead-key accent such as `˜` or `´`) lands in the search box.
- [ ] Ctrl+J / Ctrl+K navigation and Ctrl+K actions on Windows (Chrome and Edge); Cmd+K on macOS.
- [ ] iOS Safari and Android Chrome: the one-time-code autofill fills the six code boxes (and a pasted
      six-digit code spreads across them).
- [ ] Reduced-motion: with the OS setting on, the connect steps do not slide and a wrong code does not
      shake (colour change only).
- [ ] A keyboard-only run-through: log in, connect, create, copy, edit, toggle, delete, log out, using
      Tab/Shift+Tab and the shortcuts above, with a visible focus ring everywhere.
- [ ] Open DevTools on the deployed site: no CSP (Content-Security-Policy) violations in the Console while doing
      the run-through above, and no failed requests.
- [ ] Narrow layout at 320 px wide: no horizontal scrolling, dialogs and the detail pane stay usable.
- [ ] Lighthouse accessibility audit on the login, connect and palette screens (this also covers colour
      contrast, which jsdom cannot compute).

## Security model

- Stored: only the Apple session blob (cookies and trust token), encrypted with AES-256-GCM under a
  random per-user data key, which is itself stored wrapped (AES-256-GCM) under a key derived from
  `ALIASFOX_SECRET_KEY`. The Apple password is never stored. Aliases are never
  persisted server-side; they are fetched from Apple and cached in memory for 60 seconds.
- Secrets (cookies, passwords, tokens, 2FA codes) are not logged; failures log only exception
  class names. Access logging is off. Validation errors do not echo input.
- Login, Apple login, 2FA and cookie-paste attempts are rate limited per client address, with a
  global cap on Apple logins. Pending 2FA flows expire after 10 minutes and are capped.
- `Host` is checked only when auth is off (it must be loopback); `Origin` is checked on unsafe
  requests only when the header is present. Strict security headers (CSP, nosniff, no-referrer,
  frame denial) are on every response, including 4xx and unhandled 500s, and `/api/*` is `no-store`.
  HSTS is added when the request arrived over https (directly or via a trusted proxy).
- AliasFox session cookies are signed and carry a session epoch; logging out bumps it, which revokes
  every outstanding cookie ("log out everywhere"). Rotating `ALIASFOX_SECRET_KEY` also invalidates
  them and makes every stored Apple session unreadable. Each user then signs in again and reconnects
  Apple: in single mode the owner's data key is replaced at startup; in multi mode a user's data key is
  replaced, and their unreadable session deleted, when they reconnect. Until then the rows of users who
  have not reconnected stay in the database, unreadable.

## Deployment notes

- Run a single worker. Rate limiters and pending login flows are in memory; `python -m aliasfox`
  already does this.
- Logging out revokes every outstanding session cookie ("log out everywhere"). Schema changes are applied by the Alembic migrations that run at startup.
- Public deployment: set `AUTH_ENABLED=true` and `ALIASFOX_PASSWORD_HASH`, remove
  `ALIASFOX_ALLOW_UNAUTHENTICATED_NETWORK`, and put a TLS-terminating proxy in front.
- Postgres: any `DATABASE_URL` works. With Neon use the pooled or direct connection string
  (`sslmode=require`); the schema is applied by the Alembic migrations that run at startup.
- Memory: designed to run in 512 MB (not yet measured in a container).
- Multi mode: see [Multi-user mode](#multi-user-mode).

## Deploying to Render

- Set `AUTH_ENABLED=true`, `ALIASFOX_PASSWORD_HASH` (single-quoted if it goes through a `.env`
  file), `ALIASFOX_SECRET_KEY` (at least 32 characters) and `DATABASE_URL` (a Neon connection
  string; Render's free tier has no persistent disk).
- Set `TRUSTED_PROXY=true`. `TRUSTED_PROXY_HOPS` defaults to 1 and must be verified on the first
  deploy, because Render's real proxy chain is unconfirmed. In multi mode (there is no password login)
  send 5 POSTs with a junk token from one client, for example
  `curl -si -X POST -H 'Content-Type: application/json' -d '{"token":"junk"}' https://<host>/api/auth/exchange`
  (each gets 401 `invalid_token`), then check that the 6th gets 429 with a `Retry-After` header while
  the same request from another network (for example phone data) still gets 401. In single mode do the
  same with 5 wrong-password logins. If both clients are throttled, or neither is, adjust
  `TRUSTED_PROXY_HOPS`.
- Render injects `PORT` (usually 10000) and AliasFox binds to it automatically (the app reads
  `PORT`; the Dockerfile's 8080 is only the local default and `EXPOSE` is documentation), so no port
  setting is needed. The container `HEALTHCHECK` uses `${PORT:-8080}`.
- Run a single worker (the default), and ping `/healthz` from an uptime service to limit spin-down.

## Security and licence

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

### Licence

AGPL-3.0-or-later. See [LICENSE](LICENSE). If you run a modified version as a network service, the AGPL
requires you to offer its users the corresponding source.
