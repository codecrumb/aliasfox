# Repository hygiene scan (2026-11-17)

## Result: nothing real found

No secrets, credentials, cookies, connection strings, tokens or personal identifiers were found in the working tree or in git history. Two low-risk items are listed under "Low-risk notes". No history rewrite is needed.

No secret or personal value is reproduced in this report; locations only.

## What was run

- gitleaks (docker image `zricethezav/gitleaks:latest`, run locally on a read-only mount, `detect --log-opts="--all" --redact`). It scanned 104 commits (1.09 MB); `git rev-list --all` reports 107. The difference was not investigated; the manual greps below covered every commit from `git rev-list --all`. 2 findings, both false positives (below).
- Manual `git grep` over every commit for: `X-APPLE-`, `scnt`, `trust_token`, `postgres(ql)?://`, `npg_`, `ALIASFOX_SECRET_KEY=` with a value, `@gmail`, `@icloud`/`@me`/`@mac`, private key headers, common API key prefixes (`sk_live`, `re_`, `ghp_`, `AKIA`), 40+ character token-like strings at HEAD.
- Path audit of every path ever in history matching `.env*`, `*.pem`, `*.key`, `data/`, `*.db`, `*.sqlite`.
- Manual read of `docs/spike-results.md`, `scripts/spike.py`, `scripts/spike_pyicloud.py`, `neon.ts`, `README.md`, `docs/`.

## Findings

1. Authorship: all 107 commits (committer and author) use one GitHub noreply address, `codecrumb <...@users.noreply.github.com>`. No personal email. Confirmed.
2. Path audit: `.env.example` is the only `.env*` path ever committed. No `.pem`, `.key`, `data/`, `.db`, `.sqlite` path ever committed. Other path matches are source files named `session` (`src/aliasfox/sessions.py`, `tests/test_sessions.py`, `tests/test_session_endpoint.py`, `web/src/lib/session.ts`, `web/tests/session.test.ts`). `.gitignore` covers `.env`, `.env.*` (except example), `data/`, `.neon`, `neon.ts.local`, `.superpowers/`.
3. gitleaks findings (both `generic-api-key`, false positives):
   - `tests/test_connect_api.py` line 20, commit 83b7365.
   - `tests/test_login.py` line 14 (now line 15), commit a22f244.
   Both are the test constant `PASSWORD`, an obviously fake literal (two distinct dummy words). The paired `APPLE_ID` constant is the placeholder `someone@example.com`.
4. Apple identifiers (`X-APPLE-`, `scnt`, `trust_token`): appear only as cookie/field NAMES in source, tests, README line 63 and the design spec (line 81), and in `docs/spike-results.md` lines 25 and 28 (names only). No values.
5. `postgres://` / `postgresql://`: only the scheme-prefix handling in `src/aliasfox/db.py` (line 41 at HEAD), `tests/test_config.py` line 122, `README.md` line 42, `.env.example` line 17 (comment). No real connection string. `DATABASE_URL=` in `.env.example` is empty.
6. `npg_`, `@gmail`, `ALIASFOX_SECRET_KEY=`: matches are limited to `README.md` line 30 (`$(... gen-secret)` command substitution, no value), `tests/test_middleware.py` line 224 (assertion that the example has no value), and `docs/superpowers/plans/2026-11-17-aliasfox-multi-user.md` line 47 (the text of this scan's own checklist). `.env.example` has `ALIASFOX_SECRET_KEY=` empty, `ICLOUD_COOKIE=` empty, and a `REPLACE_ME` Argon2 placeholder.
7. Email-shaped strings: only fixtures (`a@icloud.com`, `x@icloud.com`, `fox@icloud.com`, `fresh@icloud.com`, `example.com`, `x.com`, `x.test` style). No real-looking address. Zero hits for the owner's name or email in any commit. Zero hits for the owner's local home path.
8. Private keys, key-prefix tokens, long token-like strings at HEAD: none (HEAD long-string hits are only prose in plan docs).
9. `neon.ts`: generic Neon config (`auth: true`, branch TTL policy). No project id, no endpoint, no key.
10. `scripts/spike.py`, `scripts/spike_pyicloud.py`: print only booleans, counts, cookie names and a host name; no embedded identifiers or paths.
11. Render/Neon identifiers: the only host strings are `aliasfox.onrender.com` in `tests/test_auth.py` (lines 79, 93, 312, test URLs) and a reference to the default `*.onrender.com` pattern in the multi-user design spec (line 211). These are generic, not a service id. No Render service id (`srv-`), Neon endpoint (`ep-`) or `neon.tech` hostname found.

## Low-risk notes (owner may wish to review before going public)

- `docs/spike-results.md`: lines 9, 10, 16 and 17 disclose the owner's alias count (411), timings and that ADP is off; line 26 records an Apple datacenter shard host for the premium mail service (`pNNN-maildomainws.icloud.com`, shard number only, Apple-wide not account-specific). Mild account fingerprinting only. Consider trimming the count and ADP statement, or keep as a spike record.
- `docs/superpowers/specs/2026-11-17-aliasfox-multi-user-design.md` line 208 names the GitHub handle `codecrumb` as the operator. This is already public in the repo URL and intentional.
- `docs/brand/aliasfox-logo-source.png`: binary not inspected for embedded metadata (EXIF/XMP). Check with `exiftool` before publishing.
- The gitleaks Docker image was pulled from the network; no repository content was sent anywhere (read-only local mount).

## Conclusion

The controller's quick pass is confirmed: single noreply identity, `.env.example` the only env file, no Apple cookie values, and `docs/spike-results.md` is low risk. Nothing here requires a history rewrite. Remaining items: optional trimming of `docs/spike-results.md` and a metadata check of the logo PNG.
