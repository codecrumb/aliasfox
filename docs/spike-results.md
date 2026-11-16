# Live spike results

Fill in after running `scripts/spike.py` and `scripts/spike_pyicloud.py`.
Record names, counts, booleans and timings only. No addresses, labels, notes, cookies or tokens.

## Cookie spike (`python scripts/spike.py [env-file] [--no-dsid]`)

- [x] Works (validate succeeds, list succeeds): yes (2026-11-16, cookie pasted from icloud.com)
- [x] Alias count / field names / origin values / isActive counts: 411 aliases; fields anonymousId, appBundleId, createTimestamp, domain, forwardToEmail, hme, isActive, label, note, origin, originAppName, recipientMailId; origins IN_APP 34, MAIL 21, ON_DEMAND 188, SAFARI 168; active 399, inactive 12
- [x] Validate and list timings: validate 0.64-0.70s; list 1.16-2.39s for 411 aliases (Apple-side latency: UI must show cached list first)
- [x] Set-Cookie rotation changes cookies: yes (write-back of rotated cookies is required, not optional)
- [ ] Session still valid after 1 hour: 
- [ ] Session still valid after 1 day: 
- [x] `dsid` query param required (`--no-dsid`): no, list works without it
- [x] `clientBuildNumber` / `clientId` params required (try without first): no, validate and list work without them
- [x] List paginated (top-level result key names, any cursor/next marker): no; result keys forwardToEmails, hmeEmails, selectedForwardTo; all 411 returned in one response
- [ ] Advanced Data Protection on for this account: 

## pyicloud spike (`.venv/bin/python scripts/spike_pyicloud.py`)

Note: if the script is killed (terminal closed / SIGKILL), delete any leftover `$TMPDIR/aliasfox-spike-*` directories because they may contain session tokens.

- [ ] Login works: 
- [ ] 2FA required, and kind (trusted device / SMS / security key): 
- [ ] Cookie names pyicloud exposes (names only): 
- [ ] `premiummailsettings` web-service URL available (host name only): 
- [ ] `api.hidemyemail` lists (count only): 
- [ ] What `login.py` can extract from the session for cookies and webservices URL: 
- [ ] Apple challenges datacenter IPs (deferred to the Render deploy): 

## Requires a write test — needs explicit owner consent — not part of this spike

- [ ] Generate without reserve consumes quota (record the generate response key names only): 
- [ ] `update_metadata` with `note` omitted keeps or clears the note: 

## Decision

- [ ] Both paths work, proceed to Task 9
- [ ] A path failed, revisit design (fallback: SRP flow from icloud-prime)

Notes: 
