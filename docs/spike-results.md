# Live spike results

Fill in after running `scripts/spike.py` and `scripts/spike_pyicloud.py`.
Record names, counts, booleans and timings only. No addresses, labels, notes, cookies or tokens.

## Cookie spike (`python scripts/spike.py [env-file] [--no-dsid]`)

- [ ] Works (validate succeeds, list succeeds): 
- [ ] Alias count / field names / origin values / isActive counts: 
- [ ] Validate and list timings: 
- [ ] Set-Cookie rotation changes cookies: 
- [ ] Session still valid after 1 hour: 
- [ ] Session still valid after 1 day: 
- [ ] `dsid` query param required (`--no-dsid`): 
- [ ] `clientBuildNumber` / `clientId` params required (try without first): 
- [ ] List paginated (top-level result key names, any cursor/next marker): 
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
