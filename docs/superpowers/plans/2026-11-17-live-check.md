# Multi-user live check (owner, before opening sign-ups)

Nothing below has been run against real Neon, Apple or Render; the code and tests use fakes. Tick each
item and record the result in `~/AI/aliasfox/CONTEXT.md` (verified, or "(unverified)").

## Before deploying
- [ ] Render env (multi): `ALIASFOX_MODE=multi`, `ALIASFOX_SECRET_KEY` (unchanged, or users must reconnect), `DATABASE_URL` (Neon Postgres), `NEON_AUTH_BASE_URL`, `NEON_AUTH_JWKS_URL`, `TRUSTED_PROXY=true`, `CONTACT_EMAIL`, `SIGNUP_ALLOWLIST=<your email>`; `SIGNUPS_ENABLED` unset/false; `PUBLIC_URL` recommended; `WEB_CONCURRENCY` unset or 1.
- [ ] Register the app origin as a Neon Auth trusted domain (`neon neon-auth domain add https://<host>`).
- [ ] Neon project is on AWS; if possible disable public sign-up in Neon Auth while `SIGNUPS_ENABLED` is false.
- [ ] Run `alembic upgrade head` (or just start the app) against a throwaway Neon branch, then against a copy of the current database (legacy adoption). Never run on Postgres in this session.
- [ ] Custom SMTP configured in Neon Auth before real users (shared SMTP is dev-only).
- [ ] Switching deletes the single-mode account and its stored Apple session: you sign up, then reconnect Apple.

## After deploying (in order)
1. [ ] Sign up with an allow-listed email; sign in. If sign-in fails, read the Render log line (class names only) and `neon_jwt` warnings: wrong issuer ("token issuer mismatch") means set `NEON_AUTH_ISSUER` to the real `iss`; JWKS errors mean a wrong `NEON_AUTH_JWKS_URL`.
2. [ ] A second, non-allow-listed email gets "Sign-ups are closed for now".
3. [ ] Reconnect Apple (password + 2FA), list/search/create/deactivate/delete an alias. Also note the earlier open bug: reserve returning 502 `apple_error` -41003.
4. [ ] Log out and back in; Account -> Disconnect Apple; Account -> Delete account (then sign up again with the allow-listed email).
5. [ ] Safari / iOS / Brave (third-party cookies blocked): does `GET {base}/token` still work? If not, plan a Neon Auth custom domain on the same site or a bearer-token approach.
6. [ ] Render proxy: `TRUSTED_PROXY_HOPS` check from the README (5 junk POSTs to `/api/auth/exchange`, expect 429 on the 6th and only your own IP limited).
7. [ ] Wrong-then-right 2FA code; Apple session lifetime; whether Apple challenges the datacenter IP.

## Neon Auth assumptions to confirm (ranked by chance of breaking sign-in)
1. Third-party cookie reaches `/token` (Safari/ITP, Brave block it).
2. JWT `iss` equals scheme://host of the base URL (else set `NEON_AUTH_ISSUER`; the base URL may carry a path such as `/neondb/auth`).
3. CORS with credentials and the Better Auth Origin check accept the app origin once trusted.
4. `NEON_AUTH_JWKS_URL` correct; key is EdDSA/Ed25519.
5. JWT payload carries `email` and `email_verified`/`emailVerified` (else new users are refused with `email_not_verified`).
6. Routes and shapes: `/sign-up/email` (token null when verification is on), `/sign-in/email`, `/email-otp/verify-email`, `/email-otp/send-verification-otp` (email-OTP plugin enabled), `/sign-out`, `/delete-user` (off by default in Better Auth; the app then shows the pending line).
7. Better Auth error codes match `classify` in `web/src/lib/neonAuth.ts` (wrong codes only degrade messages).
8. Whether Neon Auth stores users/sessions (with IP and user agent) in a `neon_auth` schema of the same database as `DATABASE_URL`; if so reword the privacy page ("Neon holds ... not AliasFox").
9. psycopg3 prepared statements over Neon's pooled endpoint (low).

## Before making the repository public
- [ ] gitleaks skipped 4 of 128 commits (3 merges, 1 unexplained); trim `docs/spike-results.md` (alias count, ADP, shard host); check `docs/brand/aliasfox-logo-source.png` metadata (exiftool).
- [ ] Set the log-retention wording and the governing-law placeholder in the terms.
- [ ] `curl -sL https://www.gnu.org/licenses/agpl-3.0.txt | diff - LICENSE` (verified identical 2026-11-17).

## Live checks for API keys
All unverified (to-do) until run against the real deployment.
- [ ] Bitwarden client (Generator > Forwarded email alias > SimpleLogin) creates an address: the label equals the site hostname and the alias appears in the app.
- [ ] Revoke the key in the app: Bitwarden's next create fails within a second.
- [ ] Claude Code connects to `/mcp` with `--header "Authorization: Bearer <key>"`, lists aliases and creates one.
- [ ] Revoke the key: MCP calls fail at once.
- [ ] Bitwarden with a trailing slash in the Server URL: record what happens (works, 404 or other).
- [ ] Apple session expired: record the message text Bitwarden shows when creating.
- [ ] `API_KEYS_ENABLED=false` on Render: `/api/v1`, `/mcp` and the Bitwarden endpoint answer 503; `/api/keys` still works.
- [ ] Confirm Render's real `X-Forwarded-For` hops with the failed-key limiter (`TRUSTED_PROXY_HOPS`): 21 wrong-key calls give 429 and only your own address is limited.
- [ ] Shared-IP limits: three Apple errors on create trigger the cooldown; 11 creates in a minute give 429 shared with the web app.
