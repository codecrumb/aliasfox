# AliasFox: design

Status: DRAFT for the owner's review. Nothing is built. Items marked (my call) are the
designer's choices, not the owner's decisions. Items marked (unverified) come from reading
other projects' code and have not been tested against Apple.

## 1. Intent

Apple's Hide My Email (HME) management is slow: several screens, slow loads. AliasFox is a
fast, keyboard-first, minimal, dark web UI for generating, finding and managing HME aliases.
Speed of search and of generating an alias is the product. Think Raycast, as a web app.

The owner will use it personally and wants it publishable on GitHub so others can run it
(self-hosted). A public multi-user service is a possible later step, not the starting point.

## 2. Goals, non-goals, anti-goals

Goals
- Generate and reserve an alias with one shortcut; label pre-filled where possible.
- Instant search over label, address, note, domain, forward-to, origin.
- View details; edit label and note; activate/deactivate; delete (confirmed); change forward-to.
- Same codebase runs as a one-user Docker container or as a multi-user service, chosen by env vars.
- Runs on a small Render free instance (512 MB) with Neon Postgres.
- Handles users with thousands of aliases (design target: 10,000 without lag).

Non-goals
- Official Apple API support (none exists; this uses the iCloud web API, which Apple can change).
- Mobile apps, browser-extension autofill (possible later), inbox reading, scheduled/batch creation.
- Light mode (dark native only for now).

Anti-goals (would be a failure even if it technically works)
- Storing an Apple password anywhere, ever.
- Any Apple session or cookie stored unencrypted, or logged.
- Being slower than icloud.com for the common path (open, search, copy).

## 3. Decided by the owner

- Python backend, PostgreSQL (Neon, IPv4/IPv6), Docker image, deployable on Render free tier.
- Keep the instance awake with an uptime pinger hitting a `/healthz` endpoint.
- Modes chosen by environment variables: one-user (auth optional, can be toggled) vs multi-user
  (auth always on). Storage: Postgres when `DATABASE_URL` is set, otherwise files under `/data`.
- Apple connection: primary is Apple ID + password + 2FA; optional alternative is pasting an
  existing iCloud session cookie. Password is used once in memory and discarded, never written.
- Apple sessions are encrypted at rest; the app never stores them in plaintext.
- App passwords hashed with Argon2; secrets encrypted with authenticated encryption.
- Signup requires password rules; login endpoints are rate-limited per IP.
- Self-hosters can supply a session via environment variable so a restarted container need not
  re-login.
- Minimal, dark, fast UI; client-side indexed search cache in the browser.
- A Terms of Service / disclaimer (no liability for leaks or Apple account actions).

## 4. Recommended approach (my call unless noted)

### 4.1 Public vs just-me
Build multi-user-capable, run it single-user for the owner first. Reasons: a public instance
holds strangers' iCloud sessions (high-value target, ToS and ban exposure, Apple may treat
datacenter IPs differently (unverified)). The self-hosted Docker image is the main way other
people use it. A public instance is a later switch (`ALIASFOX_MODE=multi`, signups flag) after
the owner has run it for a while.

### 4.2 Libraries
- iCloud communication: `pyicloud` (timlaing fork, MIT, actively maintained, 2FA by trusted device,
  SMS, security key; trust-token reuse). Verified by reading code; not run.
- Add ourselves: `updateForwardTo`, `langCode` on generate, `{success,error}` handling, delete
  with deactivate-first/rollback (patterns from Yimikami's project, Raycast, the browser extension).
- Fallback if pyicloud's login breaks: port the SRP/2FA flow from icloud-prime `internal/hme/auth.go`.
- Do not fork any existing application. The Raycast extension (TypeScript, a port of pyicloud's
  logic), icloud-prime (Go, plaintext cookie JSON, no label editing), hidemyemail-generator
  (no password login, no delete) and Yimikami's manager (Node, no tests) are references only.
- pyicloud is synchronous, not thread-safe, and file-backed for sessions with an insecure default
  (`/tmp/pyicloud`, mode 1777). AliasFox must: set its own directory (a per-request tmpfs
  directory or a subclassed session store), serialize calls per Apple account with a lock,
  run calls in a threadpool, never use its default directory.

### 4.3 Backend
FastAPI, SQLAlchemy (Postgres or SQLite via `DATABASE_URL`), one container, no queue or Redis.
In-process rate limiting is acceptable because there is a single instance; behind Render's proxy,
take the client IP only from a trusted-proxy header setting.

### 4.4 Session security model
- Apple session blob (cookies + trust token + scnt etc.) is stored encrypted in the database
  with AES-256-GCM.
- Multi-user mode: the encryption key is derived from the user's AliasFox password (Argon2id-derived
  key wrapping a random data key). The decrypted session lives only in server memory for a short
  TTL while that user is logged in. A database leak, and a leak of the server's secret, do not
  expose sessions. Cost: forgetting the AliasFox password means logging in to Apple again; there
  is no background use of sessions while the user is away.
- One-user mode: the key comes from an environment variable (`ALIASFOX_SECRET_KEY`); the
  app refuses to start without it when storing sessions.
- Env-var session seeding (self-hosted): `ICLOUD_COOKIE` seeds a session at first start. Because
  Apple rotates tokens, the app writes the refreshed session back to encrypted storage when storage is
  available, and uses the env var only if no valid stored session exists. On stateless containers
  with no storage it is a one-shot seed that will go stale (documented).
- Cookie paste: allowed; the UI warns that the pasted cookie is the user's full iCloud web session
  and gives broader access than HME.
- Never log cookies, tokens or passwords; redact them in error reports.
- Server aliases are not persisted: aliases are fetched from Apple and held in memory for a short TTL.
  The database holds only users, encrypted session blobs, and settings.

### 4.5 Client cache and search
The browser keeps the alias list in IndexedDB, encrypted with a key fetched from the server after
login and held only in memory, so the cache is unreadable without an authenticated server
session. Search uses an in-memory index over the decrypted list with a small fuzzy library;
this stays instant at well over 10,000 aliases. A WASM SQLite in the browser is not needed and
adds weight; it is rejected unless a measured benchmark shows the in-memory index failing. On
load, the cached list renders immediately and refreshes from the server in the background.

### 4.6 Frontend
Small static Svelte + Vite app served by FastAPI. Dark only. A command-palette layout: a search
field always focused; arrows to move; Enter copies and optionally closes; Shift+Enter toggles
active; a shortcut to generate; Cmd/Ctrl+K style focus; detail pane showing address, forward-to,
origin (with app icon where available), domain, status, note, created date; optimistic updates;
expired-session state with fast re-login.

### 4.7 Accounts and abuse controls
- Multi-user: email + password (no email verification initially), a `SIGNUPS_ENABLED` flag
  (default off), Argon2id hashing.
- Password policy: minimum length 12; the owner asked for symbols/digits too, so require at
  least one digit and one symbol as well (the designer would prefer length-only; owner's rule wins).
- Per-IP rate limits on signup, app login and Apple login; per-account lockout/backoff on repeated
  failures; a global cap on Apple login attempts, since repeated Apple failures can lock the
  user's Apple account.
- Terms of Service and privacy page shown at signup. The designer can draft; it is not legal advice.
- One-user mode: auth optional; if auth is off the server binds `127.0.0.1` only and refuses
  to listen publicly without auth. Check `Host`/`Origin` to block cross-site requests.

### 4.8 Deployment
One Docker image. Environment: `ALIASFOX_MODE=single|multi`, `AUTH_ENABLED`,
`DATABASE_URL`, `ALIASFOX_SECRET_KEY`, `SIGNUPS_ENABLED`, optional `ICLOUD_COOKIE`,
`TRUSTED_PROXY`. `/healthz` returns 200 without touching Apple or the database lock.
Render free instance: filesystem is not durable, so Postgres is required there.

## 5. Unverified facts that need a live test before relying on them
1. That pyicloud's current login works against Apple today for the owner's account.
2. Session lifetime, and whether trust-token reuse avoids repeat 2FA.
3. Whether Advanced Data Protection affects HME.
4. Whether Apple challenges or blocks logins from Render/datacenter IPs.
5. Rate limits and alias caps (other projects' code suggests about 5 new aliases per 30 minutes
   and a cap near 700; unconfirmed).
6. Whether `clientBuildNumber`/`clientId`/`dsid` query parameters are required.

## 6. Roadmap (my call)
0. Spike: read-only login and alias list with the owner's account to resolve section 5. If login
   fails, revisit the library choice before anything else.
1. Core service: pyicloud wrapper, encrypted session store, per-account lock, one-user mode, Docker.
2. UI: command palette, search cache, generate/edit/toggle/delete.
3. Deploy to Render + Neon for the owner only, with the pinger.
4. Multi-user mode, rate limiting, ToS, per-user encryption; then decide on opening signups.

## 8. Calls made after the builder review (my calls, owner to check)

- Unlock model (multi-user): login creates a server-side session that holds the unwrapped data key
  in memory only. Cookie lifetime 7 days, sliding; the in-memory key expires after 30 minutes idle and the
  UI shows a "locked" state asking for the AliasFox password (not Apple). A server restart (frequent on
  Render) also clears keys: every user sees "locked" and re-enters their AliasFox password. No Apple
  re-login is needed, because the encrypted Apple session stays in the database.
- Client cache key: per login session, delivered in the bootstrap response that also confirms auth
  (no extra round trip before first paint beyond the initial load). The goal is protecting a
  stolen laptop or disk image; it does not defend against XSS, which is handled by CSP and no
  third-party scripts.
- Cookie paste and `ICLOUD_COOKIE` are first-class and built in phase 1, not later. If Apple
  blocks Render IPs, the same Docker image runs on the owner's own machine or a VPS.
- Label pre-fill: with no browser extension the app cannot know the current site. The label comes
  from what the user types in the palette. Optional later: a `?label=` URL parameter for a bookmarklet.
- Generate flow: one shortcut opens "new alias": the address is generated and shown, the label
  field is focused, Enter reserves it. Whether a generated-but-unreserved address counts against
  Apple's limit is unverified; the spike checks it.
- Sync: every project we read uses only the full-list endpoint (`GET /v2/hme/list`), with no
  delta endpoint known. So refresh is a full fetch, stale-while-revalidate: the browser renders its
  cache first, then refreshes; the server caches the list for 60 seconds. At 10,000 aliases the payload is
  a few MB uncompressed, so compress responses. Whether Apple paginates the list is unverified.

## 9. Owner's decisions after review (approved 2026-11-16)

- Approved the design. Encryption key for single-user mode: option A, `ALIASFOX_SECRET_KEY` from
  an environment variable (no unlock needed after restarts). Multi-user mode keeps the
  password-derived key from section 4.4. Because the project is meant to become user-facing, the
  key must never have a default value; the app refuses to start without it when storing sessions.
- `ICLOUD_COOKIE` is a local-testing aid only. The owner will copy the cookie from icloud.com
  while testing in local Docker. In deployed/multi-user use, Apple sessions are never in environment
  variables; they live encrypted in the database.
- Optional second factor for AliasFox login (off by default, user can enable): TOTP (6-digit codes
  that change every 30 seconds) and/or passkeys (WebAuthn). Passkey-only login is allowed as an option.
  The owner left the exact combination to the designer (my call: both supported; passkeys can be the
  only login method for an account; TOTP uses recovery codes). This is separate from Apple's own 2FA.
- Client search: the owner prefers browser-side WebAssembly SQLite for speed but left the choice to
  the designer. Decision stays as in 4.5: in-memory index first; add WASM SQLite only if a benchmark at
  10,000 aliases shows the index is too slow. Rationale: a WASM database adds a multi-megabyte download
  and startup cost that would make first paint slower, not faster.
- Local testing uses Docker Desktop; the owner will start it when needed.

## 7. Left to the builder
Project layout, exact libraries for fuzzy search and Argon2, migration tooling, test strategy,
CSS and typography within "minimal, dark, fast", Docker base image and multi-stage build.
