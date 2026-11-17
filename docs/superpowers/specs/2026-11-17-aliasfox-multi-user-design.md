# AliasFox multi-user and public-launch preparation: design

Status: APPROVED by the owner 2026-11-17 (with the decisions in section 10). Items marked (owner) are the owner's decisions; items marked
(my call) are the designer's; (unverified) items must be checked before relying on them.
Builds on `2026-11-16-aliasfox-design.md`. Where this document conflicts with its sections 4.4 and 8
(password-derived key, "locked" state), this document wins.

## 1. Intent

Single-user mode works end to end. This project makes the same codebase a multi-user service that
strangers can sign up to, and prepares (does not perform) a public launch: a landing page, privacy
policy and terms, an open-source repository with a licence and security policy. Public sign-ups stay
closed until the owner says otherwise. The owner's deployment (Render + Neon) switches to multi mode and
the owner becomes its first user; no data migration is written (owner: sign out and reconnect Apple).

## 2. Goals, non-goals, anti-goals

Goals
- Sign-up and login through Neon Auth (Managed Better Auth). (owner)
- A user id that comes only from AliasFox's own signed session cookie, never from request input. (owner)
- Per-user random data keys, wrapped by a server master key (`ALIASFOX_SECRET_KEY`); no second
  "encryption password" for users. (owner)
- Per-user Apple sessions, locks, caches, login flows and rate limits; global caps toward Apple, because
  all users share the server's IP.
- `SIGNUPS_ENABLED`, default off.
- Tests proving user A cannot read or modify user B's Apple session, aliases, wrapped key or login flow.
- Landing page, privacy policy, terms (drafts, not legal advice), linking to the source. The site says
  there is no warranty and no guarantee of security or availability, and that AliasFox is unofficial and
  not affiliated with Apple. (owner)
- A user can disconnect Apple and delete everything, in the UI.
- Repository ready to be made public: history and docs scanned, licence, SECURITY.md, accurate README.

Non-goals
- Opening sign-ups; announcing or marketing the service.
- Data migration from single mode (owner). Reconnecting Apple once is the migration.
- TOTP and passkeys now (section 8). Multiple Apple accounts per user. Admin UI, billing, email
  notifications from AliasFox itself. Multi-worker or multi-instance operation. Master-key rotation.
- Any background use of a user's Apple session while the user is away (nothing runs without a request).

Anti-goals (a failure even if it technically works)
- Any route where a request body, query, header or path value selects the user.
- Storing an Apple password; logging a token, cookie, JWT or session blob.
- Storing anything about aliases on the server beyond the 60-second in-memory list cache.
- A policy page that promises more than the code does.

## 3. Constraints and honest limits

- Custody (owner accepts): the server holds other people's Apple web sessions, encrypted at rest. With
  server-held master key, a leak of the database AND `ALIASFOX_SECRET_KEY` exposes every stored session;
  a leak of the database alone does not. The server process also holds decrypted sessions in memory while
  serving a request. The policy states this plainly.
- Apple sees traffic from a datacenter IP (already true). Many users share one outbound IP; Apple may
  throttle or challenge it. Outside our control; the policy says so.
- Single worker only: limiters, login flows and per-user locks are in process memory. Startup in multi mode
  must refuse an obvious multi-worker configuration (e.g. `WEB_CONCURRENCY` > 1). A restart drops in-flight
  Apple logins (users retry) and resets limiters.
- Neon Auth Managed requires the Neon project on AWS and no IP-allow/private networking (unverified for
  this project: check region). Shared SMTP is development-only; a public launch needs the owner's own SMTP
  provider configured in Neon Auth (owner task, not code).
- Neon Auth JWTs: EdDSA, 15-minute expiry, no custom claims, not revoked by sign-out.
- `TRUSTED_PROXY_HOPS` on Render is unverified; per-IP limits are only as good as that value.

## 4. Approaches considered (how Neon Auth meets our requests)

A. Exchange (chosen, owner: "up to you"). The browser signs in with Neon Auth, then posts the JWT once to
   `POST /api/auth/exchange`. The server verifies it, maps it to a local user, and sets our existing
   HttpOnly, SameSite=Strict signed cookie (local user id + session epoch). All later requests use only that
   cookie. Logout bumps the epoch, so every cookie dies at once. Reuses tested code; keeps tokens out of
   JavaScript.
B. Verify the Neon JWT on every request. Needs the token in JS memory and refresh every 15 minutes; no
   instant revocation; XSS-stealable. Rejected: contradicts the HttpOnly-cookie rule.
C. Use Neon's own session cookies through our domain. Depends on third-party-cookie and popup behaviour
   in every browser; least control. Rejected.

## 5. Design

### 5.1 Data model (Alembic from here on; baseline migration of the current schema)
- `users`: `id` (int PK, the only identifier in our cookie), `auth_subject` (text, unique, not null: the
  JWT `sub`), `wrapped_key` (bytes), `session_epoch`, `created_at`. No email column (my call: data
  minimisation; Neon Auth already holds it).
- `apple_sessions`: `user_id` PK, `blob`, `updated_at`. Blob = AES-256-GCM under that user's data key,
  AAD = user id (as today).
- Data key: 32 random bytes per user, stored as `wrapped_key` = AES-256-GCM under
  `HKDF(ALIASFOX_SECRET_KEY, info="aliasfox-wrap-v1")`, AAD = `b"wrap|" + user id`. A blob copied between
  users fails to decrypt (wrong key and wrong AAD); a wrapped key copied between users fails (AAD).
- One key path for both modes (my call): single mode also gets one user row with a wrapped data key,
  replacing the global session key, so isolation tests cover both. Existing stored sessions become
  unreadable once; the user reconnects Apple (accepted).
- Data keys are unwrapped per operation and not cached beyond the request/operation (my call; cheap).

### 5.2 Identity and sign-up gate
- `POST /api/auth/exchange {token}`: verify with PyJWT (or equivalent), algorithms pinned to `["EdDSA"]`,
  signature against Neon Auth's JWKS (cached; refetch on unknown `kid` with a small refetch rate limit),
  issuer from the configured base URL, `exp`, `sub` required, `iat` not older than 5 minutes (my call).
  Rate limited per IP; no global failure lockout (as today).
- Known `sub` -> that user. Unknown `sub` -> create the user only if `SIGNUPS_ENABLED=true` OR the token's
  verified email is in `SIGNUP_ALLOWLIST` (comma-separated, my call: lets the owner and invited testers in
  while sign-ups are closed; the email is compared, never stored). Otherwise 403 `signups_closed`. Require a
  verified email to create an account (unverified: whether the JWT carries the claim; if not, ask Neon Auth
  for the session user server-side).
- Neon's sign-up endpoint is public, so someone can create a Neon Auth account directly; it grants nothing
  here. If Neon Auth can disable its own sign-ups, do that as a second lock (unverified).
- `current_user` in multi mode returns the user id from the cookie only after checking signature, max age
  and that the cookie's epoch equals that user's current `session_epoch` in the database.
- `AUTH_ENABLED`/`ALIASFOX_PASSWORD_HASH` are single-mode settings; in multi mode the startup check requires
  the Neon Auth settings (`NEON_AUTH_BASE_URL`, JWKS URL), `DATABASE_URL`, `ALIASFOX_SECRET_KEY`, and
  `TRUSTED_PROXY=true` unless explicitly overridden for local testing.

### 5.3 Per-user isolation
- Every `/api/*` route depends on `current_user`; a test enumerates the app's routes and fails if a route is
  not either behind it or on a short explicit allow-list (`/healthz`, exchange, logout, static/legal pages).
- All server state keyed by user id: `AliasService` locks and caches, `LoginFlow` flows, resend caps, rate
  limiters, cache key (`derive_cache_key` already includes user id and epoch).
- Required tests (user A and B with real cookies): A's cookie never returns B's aliases/status/session
  data; requests carrying `user_id`/`u` in body, query, headers or path do not change the user; a cookie
  signed with a wrong key, with a stale epoch, or for a deleted user is rejected; B's blob placed in A's row
  and A's wrapped key placed in B's row both yield `session_unreadable`, not data; A's pending Apple login
  cannot be verified, resent or cancelled by B; A disconnecting leaves B connected; A logging out does not
  bump B's epoch; the route-coverage test above.

### 5.4 Limits toward Apple (shared outbound IP)
- Keep existing per-user limits (generate/reserve, refresh). Add: per-user cap on Apple login attempts per
  hour (protects the user's Apple account from lockout), a global cap on Apple login starts per minute and a
  global bound on concurrent Apple logins (so a few users cannot get the server's IP flagged for everyone),
  and a per-IP limit on `/api/connect/*` and the exchange. Numbers are the builder's, documented, and
  exposed as settings with safe defaults.

### 5.5 Account lifecycle
- UI "Disconnect Apple": deletes the stored session (exists today).
- UI "Delete account" (new, confirmed): deletes the Apple session, `wrapped_key`, `users` row and bumps the
  epoch; also deletes the Neon Auth user (unverified: whether the managed API allows self-deletion; if not,
  the policy says how to request it and the UI says it is pending). After this nothing identifying the user
  remains on our side.
- Logout also calls Neon Auth sign-out from the browser.

### 5.6 Frontend
- New signed-out views in the Svelte app: sign up, sign in (email + password through the Neon Auth client),
  email verification code if enabled, then `exchange`, then the existing app. CSP stays strict: no
  third-party scripts and no inline scripts; `connect-src` allows only the Neon Auth host. Whether the
  Managed SDK works in Svelte or plain `fetch` against Better Auth routes is simpler is the builder's call
  after a short spike (unverified). The app origin must be registered as a Neon Auth trusted domain.
- Signed-in state remains "the HttpOnly cookie works" (`/api/session`), as today.

### 5.7 Landing, privacy, terms
- Plain static HTML/CSS, no JavaScript, no external fonts or trackers, same dark visual identity and fox
  logo; mobile friendly. At `/` for signed-out visitors in multi mode (signed-in users go to the app);
  `/privacy`, `/terms`. Single mode behaviour is unchanged (my call).
- Landing copy: "AliasFox manages your iCloud Hide My Email addresses"; what it does; sign up / log in;
  link to the source repository; footer links to privacy and terms; plain statement that it is unofficial,
  not affiliated with or endorsed by Apple, and provided without warranty, with no guarantee of security or
  availability. While sign-ups are closed the page says so instead of offering sign-up (driven by the flag).
- Privacy policy states exactly: what is stored (an identifier from Neon Auth, a wrapped data key, one
  encrypted Apple session per user, timestamps); what Neon Auth stores (email, password hash, held by
  Neon, a processor); what is not stored (Apple password; aliases and labels on the server beyond a
  60-second in-memory cache; the browser keeps an encrypted alias cache in IndexedDB, cleared on logout);
  that the server holds the key to decrypt sessions, so a combined database and server-secret compromise
  exposes sessions; the Apple session grants broader iCloud web access than Hide My Email; operational logs
  (IP and request metadata, retention to be stated by the owner); hosting (Render, Neon); how to disconnect
  and delete everything; how to contact. Terms: unofficial/unaffiliated, no warranty, no availability or
  security guarantee, risk of Apple account actions, acceptable use, termination, no legal advice.
- Both documents are marked draft until the owner reviews them.

### 5.8 Constraints and operations to revisit (carried from the notes)
Single worker; `TRUSTED_PROXY_HOPS` on Render; wrong-then-right 2FA code; session lifetime; Apple
datacenter-IP challenge; Postgres via Neon; the real shape of Apple's reserve reply. Each is a live check
with the owner; results go into `~/AI/aliasfox/CONTEXT.md` as verified or "(unverified)".

### 5.9 Pre-public-repository checklist
Scan the whole git history and all docs for secrets and personal data with a real scanner (gitleaks or
trufflehog, run without installing into the project) plus targeted greps (Apple cookie names, Neon URLs,
the owner's identifiers); review `docs/spike-results.md`, `scripts/spike*.py` and `neon.ts` for personal
data; confirm `.env.local` was never committed (first pass: not committed; only `.env.example`); review
README for accuracy against multi mode; add `LICENSE` and `SECURITY.md` (private reporting channel, no
promises of response time, fix or bounty); add the no-warranty and unofficial notices to the README. If
anything is found, stop and report before rewriting history. Making the repository public is a separate
step the owner triggers.

## 6. Second factor (item 5): deferred (my call)
Managed Neon Auth lists MFA as roadmap and does not expose passkeys. Getting them means leaving Managed
Auth for self-managed Better Auth, which reopens the owner's identity decision. They do not block a launch
with sign-ups closed. Revisit when Neon ships MFA, or add an app-level TOTP step after the exchange (this
adds one more secret to our custody, encrypted under the user's data key).

## 7. Licence (proposal, owner to confirm)
AGPL-3.0-or-later: a service that holds other people's sessions should not be re-hosted with silent
modifications; the network clause makes hosted forks publish theirs. MIT is the simpler, more permissive
alternative and is friendlier to contributors and companies. Recommendation: AGPL-3.0-or-later.

## 8. Order of work (my call)
1. Repository hygiene scan (read-only; stops for a decision if it finds anything).
2. Backend: Alembic baseline and new schema; wrapped keys and one key path; exchange and cookie changes;
   `SIGNUPS_ENABLED` and allow-list; account deletion; Apple-facing limits; isolation tests; multi-mode
   startup checks. Test-first.
3. Frontend sign-up/sign-in views after a short Neon Auth client spike.
4. Landing, privacy, terms.
5. LICENSE, SECURITY.md, README; live checks; update `~/AI/aliasfox/CONTEXT.md`.
Commit and push only when the owner asks. No co-author lines in commits.

## 9. Left to the builder
Library choices (JWT, Alembic setup), exact limit numbers, route layout for static pages, the Neon Auth
client approach (SDK versus plain fetch), test layout, CSS and copy within the constraints above.

## 10. Owner decisions after review (2026-11-17)
- Licence: AGPL-3.0-or-later (owner: do it).
- Contact address: a Hide My Email alias made by the owner, read from the `CONTACT_EMAIL` environment
  variable (never committed; forks do not inherit it). Used in the policy, terms and SECURITY.md; the
  pages say where to write; if unset the pages say contact is by GitHub issue / private security advisory.
- Operator named in policy and terms: "AliasFox" (the project); `codecrumb` is the GitHub owner where a
  person-level name is unavoidable.
- Public address not decided: the app takes a configurable `PUBLIC_URL` (cookie origin, trusted domain,
  CSP, links). Default `*.onrender.com`.
- Allow-list and no local email column: confirmed.
- Second factor: not in this session. We rely on Neon shipping MFA; Managed Auth has no passkeys. Login
  code is kept behind one function so a second step can be added later.
- Backlog (not this session, own brainstorm later): offline queue with sync-when-online, bottom-right
  status toast, "pending delete" state with undo. Notes for it: store the queue in IndexedDB encrypted with
  the per-session cache key, not localStorage; scope per user and drop or warn on logout; make deletes
  idempotent (already-gone counts as success); detect an alias changed elsewhere before applying; never
  drop a failed operation silently, because believing an alias is deleted when it is not is the worst
  outcome for a privacy tool.
- Backlog, owner idea (2026-11-17): several open tabs each talk to the server. Consider one "leader" tab
  per browser that holds the server connection (refresh, polling), others follow via BroadcastChannel or
  a Web Lock; the lock is released when the leader closes and another tab takes over (re-election). Also
  let hidden tabs pause polling. Discuss and design later; interacts with the offline queue above.
- Open bug, owner report (2026-11-17, not investigated): reserving a new alias returns 502 `apple_error`
  with Apple code -41003 ("invalid private email request"), repeatedly; the generated address shows in the
  dialog, and reopening the dialog generates a new one with the same result. Several tabs were open.
  Unknown whether it is an Apple limit, a rejected request shape, or a stale generated address (all
  (unverified)). Next: read the server's full error message and Render logs, retry after a redeploy.
