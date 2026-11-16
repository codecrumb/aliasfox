# AliasFox

AliasFox is a small self-hosted service for managing your iCloud "Hide My Email" aliases:
list, search, generate, label, deactivate and delete them from your own server. This
repository is the core service (API, Apple connection, encrypted session storage).

Unofficial and not affiliated with Apple. It talks to undocumented iCloud web endpoints that
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
| `ALIASFOX_MODE` | `single` | Only `single` works; `multi` is refused at startup until plan 3. |
| `ALIASFOX_ALLOW_UNAUTHENTICATED_NETWORK` | `false` | Lets a container bind `0.0.0.0` with auth off. Only with a loopback-only published port. |

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
  example after the secret key changed).
- 502 `apple_unreachable`: Apple could not be reached while checking a pasted cookie (the cookie
  itself is not blamed, unlike 400 `invalid_cookie`).
- 429 `rate_limited`: per-user limits on `?refresh=true` (at most one real refresh per 5 seconds, sooner
  calls are served from the cache) and on generate/reserve (10 per minute).

## Security model

- Stored: only the Apple session blob (cookies and trust token), encrypted with AES-256-GCM using
  a key derived from `ALIASFOX_SECRET_KEY`. The Apple password is never stored. Aliases are never
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
  them (and makes stored Apple sessions unreadable, so you reconnect).

## Deployment notes

- Run a single worker. Rate limiters and pending login flows are in memory; `python -m aliasfox`
  already does this.
- Logging out revokes every outstanding session cookie ("log out everywhere"). Dev databases created before this change get the `users.session_epoch` column added automatically; Alembic migrations arrive with plan 3.
- Public deployment: set `AUTH_ENABLED=true` and `ALIASFOX_PASSWORD_HASH`, remove
  `ALIASFOX_ALLOW_UNAUTHENTICATED_NETWORK`, and put a TLS-terminating proxy in front.
- Postgres: any `DATABASE_URL` works. With Neon use the pooled or direct connection string
  (`sslmode=require`); tables are created on startup.
- Memory: designed to run in 512 MB (not yet measured in a container).

## Deploying to Render

- Set `AUTH_ENABLED=true`, `ALIASFOX_PASSWORD_HASH` (single-quoted if it goes through a `.env`
  file), `ALIASFOX_SECRET_KEY` (at least 32 characters) and `DATABASE_URL` (a Neon connection
  string; Render's free tier has no persistent disk).
- Set `TRUSTED_PROXY=true`. `TRUSTED_PROXY_HOPS` defaults to 1 and must be verified on the first
  deploy, because Render's real proxy chain is unconfirmed: make 5 wrong-password logins from your
  browser, then check that a 6th gets 429 while a login attempt from another network (for example
  phone data) still gets 401. If both are throttled, or neither is, adjust `TRUSTED_PROXY_HOPS`.
- Render injects `PORT` (usually 10000) and AliasFox binds to it automatically (the app reads
  `PORT`; the Dockerfile's 8080 is only the local default and `EXPOSE` is documentation), so no port
  setting is needed. The container `HEALTHCHECK` uses `${PORT:-8080}`.
- Run a single worker (the default), and ping `/healthz` from an uptime service to limit spin-down.
