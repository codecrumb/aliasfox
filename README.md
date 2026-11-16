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
python -m aliasfox gen-secret      # or: docker run --rm python:3.13-slim python -c "import secrets;print(secrets.token_urlsafe(48))"
# put the value in .env as ALIASFOX_SECRET_KEY
docker compose up -d --build
curl -s localhost:8080/healthz     # {"status":"ok"}
```

The port is published on `127.0.0.1` only. Data lives in `./data`.

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
| `ALIASFOX_SECRET_KEY` | none, required | Encrypts stored Apple sessions and signs login cookies. The app will not start without it. |
| `AUTH_ENABLED` | `false` | Require an AliasFox password. Needed for any non-loopback bind. |
| `ALIASFOX_PASSWORD_HASH` | none | Argon2id hash, required when auth is on. Make one with `python -m aliasfox hash-password`. |
| `DATABASE_URL` | SQLite in `DATA_DIR` | Postgres URL. `postgres://` and `postgresql://` are normalised. |
| `DATA_DIR` | `/data` | Where the SQLite file lives. |
| `ICLOUD_COOKIE` | none | Local testing only: seeds a session at startup if none valid is stored. |
| `TRUSTED_PROXY` | `false` | Use the rightmost `X-Forwarded-For` entry for rate limits. Only behind a proxy you control. |
| `HOST` / `PORT` | `127.0.0.1` / `8080` | Bind address. A non-loopback host without auth is refused. |
| `ALIASFOX_MODE` | `single` | `single` or `multi`. |
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

## Security model

- Stored: only the Apple session blob (cookies and trust token), encrypted with AES-256-GCM using
  a key derived from `ALIASFOX_SECRET_KEY`. The Apple password is never stored. Aliases are never
  persisted server-side; they are fetched from Apple and cached in memory for 60 seconds.
- Secrets (cookies, passwords, tokens, 2FA codes) are not logged; failures log only exception
  class names. Access logging is off. Validation errors do not echo input.
- Login, Apple login, 2FA and cookie-paste attempts are rate limited per client address, with a
  global cap on Apple logins. Pending 2FA flows expire after 10 minutes and are capped.
- `Host` and `Origin` are checked on unsafe requests; strict security headers (CSP, nosniff,
  no-referrer, frame denial) are on every response and `/api/*` is `no-store`.
- No HSTS here: terminate TLS at a reverse proxy and set HSTS there.
- AliasFox session cookies are signed, not stored server-side, so they cannot be revoked
  individually. Rotate `ALIASFOX_SECRET_KEY` to invalidate all of them (this also makes stored
  Apple sessions unreadable, so you reconnect).

## Deployment notes

- Run a single worker. Rate limiters and pending login flows are in memory; `python -m aliasfox`
  already does this.
- Public deployment: set `AUTH_ENABLED=true` and `ALIASFOX_PASSWORD_HASH`, remove
  `ALIASFOX_ALLOW_UNAUTHENTICATED_NETWORK`, and put a TLS-terminating proxy in front.
- Render free tier has no persistent disk: set `DATABASE_URL` to a Postgres instance, and ping
  `/healthz` from an uptime service to limit spin-down.
- Postgres: any `DATABASE_URL` works. With Neon use the pooled or direct connection string
  (`sslmode=require`); tables are created on startup.
- Memory: designed to run in 512 MB (not yet measured; see the verification notes).
