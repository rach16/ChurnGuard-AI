# Runbook: the Salesforce connection

For whoever operates ChurnGuard. Covers the connection built in 1.9 (ADR-0012):
what is stored where, and what to do when something expires, leaks or rotates.
Everything here was done at least once on staging on 2026-10-16 unless marked
*untested*.

## What exists

| Thing | Where | Notes |
|---|---|---|
| The External Client App `ChurnGuard` | Salesforce org, Setup → External Client App Manager | Scopes `api`, `refresh_token`. PKCE, refresh-token rotation and a 30-day idle limit are enforced by the org |
| Consumer key and secret | Render → service → Environment: `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET` | Never in the repo, never in a log |
| The connection row | Neon, branch `staging`, table `connections` | Org, host, user, status, and the refresh token **sealed** |
| The master key | AWS KMS `alias/churnguard-staging-master`, us-west-2 | Reached as IAM user `churnguard-staging-kms`, whose only rights are GenerateDataKey, Decrypt, ReEncrypt and DescribeKey on that key |
| Sign-in | Cognito user pool `us-east-1_sFnyqOnla` (staging); Render `COGNITO_*` | Who may act: admins connect and disconnect, operators sync, test and roll back (ADR-0013). Accounts are created by an admin |
| The scheduler's identity | GitHub Actions OIDC, no secret | The daily workflow's token, accepted on `sync-due` only, pinned to this repository's `sync.yml` on `main` |
| The audit log | Neon, table `audit_events` | Append-only. Never holds a token |

Each environment gets its own row set, key, IAM user and user pool:

| | Staging | Production (2.6.4) |
|---|---|---|
| Neon branch | `staging` | `production` |
| KMS key | `alias/churnguard-staging-master` (us-west-2) | `alias/churnguard-production-master` (us-west-2), yearly rotation |
| IAM user | `churnguard-staging-kms` | `churnguard-production-kms`, the same five actions on its own key only |
| Cognito pool | `us-east-1_sFnyqOnla` | `us-west-2_B8jnztWlJ` (domain `churnguard-production`) |
| Site origin (callbacks, scheduler audience) | the staging preview | `https://churnguard.banik.dev` |

Production's IAM user was tested on 2026-10-18: it wraps and unwraps under its own
key, a wrapped key presented with another encryption context is refused, and the
staging key answers AccessDenied to it.

## Reading the state

- **The page.** Integrations → the Connection block: Connected / Needs a fresh
  login / Not connected, with org id, host and timestamps.
- **The API.** `GET /connections` on the backend (public, no username).
- **The audit log.** From a laptop with `DATABASE_URL` in `.env`:

  ```bash
  uv run python - <<'EOF'
  import os, sys; sys.path.insert(0, "src")
  from dotenv import load_dotenv; load_dotenv(".env")
  from integrations.db import Database
  db = Database(os.environ["DATABASE_URL"]).open()
  with db.connection() as c:
      for r in c.execute("select at, event, actor, org_id, detail from audit_events order by id desc limit 20"):
          print(r["at"], r["event"], r["actor"], r["org_id"], r["detail"])
  db.close()
  EOF
  ```

- **The backend log.** Render → service → Logs. Tokens, codes and the Neon
  password are redacted before they are written; a line that shows one is a bug.

## Needs a fresh login (`needs_reauth`)

**Cause.** Salesforce refused the stored refresh token: an admin revoked it, the
org's 30-day idle limit passed, the user was deactivated, or the app's secret
changed. The audit log has a `needs_reauth` row with the error code.

**Fix.** Integrations → **Reconnect**, log in, Allow. The row is reactivated in
place and audited as `reconnect`. Nothing else to do.

**Prevent.** Any use of the connection resets the 30-day timer: **Test
connection** on the page does, and the daily sync (1.11) will. Until 1.11
exists, click Test connection at least monthly.

## Disconnect, and when Salesforce does not confirm

**Disconnect** revokes the refresh token at the org's own host, deletes the row,
and audits `disconnect` with `revoke_status`. `200` means Salesforce killed the
token (verified 17:46 on 2026-10-16).

Any other status means the row is gone here but the token may live on at
Salesforce until its idle limit. Revoke it by hand: Salesforce → Setup → Users →
the connecting user → **OAuth Connected Apps** → Revoke on the ChurnGuard entry.
Three such tokens were orphaned on 2026-10-16 by the first Disconnect code, which
posted to `login.salesforce.com` instead of the org's host.

## Rotate the consumer secret

1. Salesforce → External Client App Manager → ChurnGuard → Settings → OAuth
   Settings → **Consumer Key and Secret** → rotate (Salesforce emails a code).
2. Render → Environment → update `SALESFORCE_CLIENT_SECRET` → Save, rebuild and
   deploy. The old secret stops working for exchange and refresh; stored refresh
   tokens are unaffected.
3. Integrations → Test connection, to prove a refresh still works.

*Untested as a sequence; each step has been done individually.*

## Rotate the master key

The refresh tokens are sealed under per-connection data keys; the master key only
wraps those. Rotating it is a **re-wrap**, and the tokens are never read.

**KMS, automatic (yearly rotation is on).** KMS keeps every backing key version
and decrypts with whichever wrapped a given row, so nothing is required. Running
the re-wrap anyway moves rows to the newest backing key:

```bash
uv run python scripts/rewrap_tokens.py          # dry run: reports each row's key version
uv run python scripts/rewrap_tokens.py --apply
```

**KMS, a new key** (compromise, or moving accounts). Create the key and give the
IAM user the same five permissions on it, set `CHURNGUARD_KMS_KEY_ID` to the new
alias, then run the script with `--apply` while the old key is still enabled (the
IAM user needs Decrypt/ReEncryptFrom on it until the re-wrap is done). Each row
is audited as `rewrap` with the old and new key ARNs. Then disable the old key;
if a row was missed it fails to open with the old ARN named, not silently.

**Local key.** `CHURNGUARD_MASTER_KEY=v1:<old>,v2:<new>`, deploy, run the script
with `--apply`, confirm every row reports `v2`, then drop `v1:` from the value.

Verified by `tests/test_token_refresh.py::test_rewrap_script_moves_every_row_to_the_current_key`
against the local provider and by a real KMS re-wrap from a laptop on 2026-10-16;
*the script has not yet been run against the staging database*.

## Who may act, and removing someone

There is no shared key to rotate (deleted 2.6.3). People act through their own
Cognito accounts; the scheduler through GitHub's per-run token.

- **Invite, change a role, remove:** the **Team** page (left menu, admins only).
  Removing someone disables the account, signs them out everywhere and deletes
  their sessions at once. The last admin cannot be demoted or removed, and
  nobody removes themselves. Every change is in the audit log as
  `member_invited`, `member_role_changed`, `member_removed`.
- **Testing an invite yourself: use a private window** for the invitee's first
  sign-in. In a browser that already holds your own Cognito session, Cognito's
  sign-in page fails the new person's "choose a password" step with
  `Invalid input: Invalid challenge transition`. Seen on staging 2026-10-18;
  the same steps succeed through Cognito's API and in a private window, so the
  account and settings are fine. A real invitee on their own browser never
  meets it.
- **A role change reaches the person within two minutes**, at their next page
  load: reads re-confirm the role with Cognito every two minutes, and every
  action re-confirms it on the spot. No sign-out needed. If Cognito itself is
  unreachable, reads keep the last confirmed role and actions answer 503 "try
  again in a moment" rather than signing anyone out.
- **The first admin of a new environment** is the one account made by hand:
  Cognito → Users → Create user (email invitation, mark verified), add to
  `admin`, set `custom:tenant`. Everyone after that comes from the Team page.
- **If the Team page answers AccessDenied:** the environment's IAM user lacks
  its `churnguard-<env>-team` policy (nine `cognito-idp` admin actions on that
  pool only).
- **Rotate the app client secret:** Cognito → App clients → `churnguard-staging`
  → Client secrets → add a new one; Render `COGNITO_CLIENT_SECRET` → Save and
  deploy; then delete the old secret in Cognito.

## If a token leaked

A refresh token appearing anywhere outside the sealed column is an incident.

1. **Disconnect** on the page: revokes it at Salesforce (check `revoke_status`
   is 200; if not, revoke by hand as above).
2. Reconnect to issue a fresh one.
3. Find the leak: the redaction filter (`src/integrations/redaction.py`) and
   `tests/test_token_custody.py` are where the patterns live; add the shape that
   got through.

If the **master key** leaked: alone it opens nothing (the ciphertext is in the
database), but rotate it as above and, with KMS, check CloudTrail for Decrypt
calls on the key you did not make.

If the **database** leaked: the sealed tokens cannot be opened without the master
key. Disconnect and reconnect anyway, so the leaked ciphertexts refer to tokens
that no longer exist.

## Neon suspends after five idle minutes

The free tier suspends the compute and cuts every connection. The pool closes
idle connections at three minutes and checks each one before use, so a request
after a quiet spell reconnects instead of failing. The first such request can
take a second or two while the compute wakes. If `/connections` ever answers 500
with `AdminShutdown` in the log, that guard has regressed.

## Sync runs (1.10)

**Sync now** on the Integrations page starts a run through the stored
connection; the run list under the connection block shows each run's outcome.
From a laptop with `DATABASE_URL` in `.env`, the same from the database:

```bash
uv run python - <<'PY'
import os, sys; sys.path.insert(0, "src")
from dotenv import load_dotenv; load_dotenv(".env")
from integrations.db import Database
db = Database(os.environ["DATABASE_URL"]).open()
with db.connection() as c:
    for r in c.execute("select status, trigger, api_requests, error, contracts, started_at from sync_runs order by created_at desc limit 5"):
        print(r)
    for r in c.execute("select sobject, count(*) filter (where not deleted) live, count(*) filter (where deleted) tombstones from raw_records group by 1"):
        print(r)
db.close()
PY
```

| Run says | Meaning | What to do |
|---|---|---|
| **Succeeded** | Every object landed and every data contract passed; the snapshot is the newest good one and the Integrations report reads from it | Nothing |
| **Contracts failed** (`degraded`) | The org's data landed but the candidate broke a contract, named on the run. The snapshot is kept for diagnosis and nothing serves it; the previous good one still does | Fix the data in Salesforce (the failing contract names the rule and the count) and Sync now again |
| **Failed** with `<Object>: INVALID_FIELD …` | A field the connector reads was renamed or removed in the org. Objects before it landed and kept their watermarks | Restore the field, or change the connector's field list, then Sync now: the run resumes from where it stopped |
| **Failed** with `REQUEST_LIMIT_EXCEEDED` | The org's daily API allowance is spent | Wait for the window to roll; a sync costs about 5 calls |
| **Failed** with `abandoned` | The process stopped mid-run (deploy, restart). Swept by the next trigger or startup | Sync now again |
| **Failed** with `Salesforce refused the stored token` | The connection is `needs_reauth` | Reconnect, then Sync now |
| Button says **Syncing…** for more than a minute | A run is unfinished; a second click joins it | Wait; after 30 minutes it is swept as abandoned |

A first run reads every object in full. Every later run asks only for records
with `SystemModstamp` since that object's watermark, less five minutes, so a
sync of an unchanged org is five calls and under a second. Deletes arrive as
tombstones and leave the candidate at the next transform.

## The schedule, and the morning email

`.github/workflows/sync.yml` runs once a day (best effort, see the heartbeat's
note on GitHub's cron) and calls `POST /connections/sync-due?wait=true` with
the OIDC token GitHub issues for that run (audience: the staging site's origin);
the backend accepts it only from this repository's `sync.yml` on `main`, for a
schedule or a manual run. No secret is involved. The same
check runs on every start of the backend, because the free tier sleeps and a
timer inside the process would not fire. Either way a connection is synced
when its last success is older than `SYNC_INTERVAL_HOURS` (24). A scheduled
run is a use, so the org's 30-day idle limit never trips while it runs.

Before starting, the schedule reads the org's daily API allowance (one call)
and defers while less than `SYNC_API_RESERVE` (20%) is left, audited as
`sync_deferred`. After `REQUEST_LIMIT_EXCEEDED` it backs off: an hour,
doubling per consecutive strike, capped at twelve, plus up to ten minutes of
jitter.

**The workflow fails, and GitHub emails you, when** a connection needs a
login, or a run failed, or a run broke a contract. The email links to the run
log, which prints one line per connection:

| Line says | Do |
|---|---|
| `skipped (Salesforce refused the stored token …)` | Integrations → **Reconnect** |
| `started -> failed, …: Account: INVALID_FIELD …` | See "Sync runs" above; restore the field, Sync now |
| `started -> degraded …` | A contract failed; the page names it; fix the data, Sync now. The previous snapshot still serves |
| `deferred (org API allowance low)` | Nothing; tomorrow's run will go unless something else is spending the org's calls |
| `the backend did not answer 200` | The service is down or waking slowly: check Render's logs; the heartbeat will say if it is production |
| `could not get an OIDC token from GitHub` | The workflow lost `permissions: id-token: write`; restore it in `sync.yml` |
| `Scheduler token refused (<check>)` | The token was not this repository's `sync.yml` on `main` (the check names which claim). A run from another branch is refused on purpose |

Run it by hand: GitHub → Actions → **Daily sync** → Run workflow.

The same two conditions (a connection needing a login; two runs in a row
failed or degraded) appear as `errors.sync` on `GET /health`, as `attention`
on `GET /connections`, and as the amber banner at the top of the connection
block on the Integrations page. `GET /connections/{id}/metrics` is the last 30
days in numbers (runs, success rate, records changed, API calls per day,
median duration, last success); the page shows it under the run list.

## What the site serves (promotion)

The `serving` table has one row per tenant naming the snapshot every page
shows. A run whose contracts all pass flips it in the same transaction that
records the run (audited as `promotion`, with the previous snapshot id). The
API then writes the snapshot to `data/serving/<snapshot id>/` as the canonical
CSV files and reloads the scorer, playbook and evidence from that folder.

**On every start** the API restores whatever the pointer names before loading
anything, because Render's disk does not survive a restart. The log line is
`✓ serving snapshot <id> (run <id>, as of <date>)`. With no pointer, or no
database, it logs `serving the data folder` and the committed pull serves.

- **Which snapshot is live?** `GET /source` → `served_from`; `GET /connections`
  → `serving`; the Integrations page: "Serving the run of <time>", and the
  run carries a `serving` badge.
- **A promotion that did not take.** The run says `promoted, but serving it
  failed: …`. The pointer is right and the disk is not; restart the service
  and the start-up restore puts it right. The audit row for the run has
  `promoted: true, served: false`.
- **Roll back.** Integrations → **Roll back** (shown once a previous snapshot
  exists). It moves the pointer to the snapshot served before this one, audits
  `rollback` with both ids, restores it to disk and reloads the loaders; every
  page shows it within a second. The pair is a toggle: Roll back again returns
  to the newer one. A snapshot that failed its contracts, or that retention has
  removed, is refused with the reason. `POST /connections/{id}/rollback` with
  `{"snapshot_id": "…"}` serves a specific earlier snapshot.
- **Retention.** After every successful sync and at start: snapshots beyond the
  newest 10 (never the serving one or its predecessor), tombstones older than
  30 days, and every row of a disconnected connection idle for 30 days. Audited
  as `retention` when anything was deleted. Live landings are never pruned:
  they are the baseline the incremental read builds on.

The DuckDB warehouse baked into the image is built from the committed pull,
not from the promoted snapshot (dbt is not in the serving image; see
Deferred in STATUS). Nothing on the site reads the Salesforce warehouse today.

## Write-back to Salesforce (9.2, ADR-0014)

After every promotion and every roll back, the site's reading for each active
account goes onto three fields of its Account record in the org:
`ChurnGuard_Risk_Score__c` (Number 3,1), `ChurnGuard_Risk_Band__c` (picklist
Critical / High / Medium / Low) and `ChurnGuard_Top_Reason__c` (Text 80). Equal
to what the site shows; only records whose values changed are written; churned
accounts, prospects and sample records are never written. Nothing is written on
a restart.

- **Turn on** (admin, Integrations page, or `POST /connections/{id}/writeback`).
  Creates whichever of the three fields the org lacks through the Tooling API,
  grants the connected user's profile read and edit on them, describes Account
  again to confirm all three are present and updateable, then flips
  `connections.writeback_enabled`. Needs a connection made by a user with
  *Customize Application* (any System Administrator). A refusal names the step:
  "cannot create fields" means the connected user lacks that right; "exists with
  another type" means someone made a field of that name by hand. Audited
  `writeback_enabled` with the fields created.
- **The fields are on no page layout** after creation. To see them: a list view
  with the three columns (filter *ChurnGuard risk score not equal to blank*,
  sort descending), or add them to the Account layout in Setup. Field-level
  security is granted to the connected user's profile only; widen it in Setup
  for CSMs.
- **Turn off** (admin, `DELETE`). Stops the writes; the org keeps its last
  values. Audited `writeback_disabled`.
- **Write back now** (operator, `POST …/writeback/run`). The retry after a
  failure, or the first write after turning on without waiting for a sync.
- **Reading the state.** `GET /connections/{id}/writeback`: on or off and the
  last attempt (status `succeeded` / `partial` / `failed` / `deferred`, counts
  of candidates, resolved, unchanged, written, failed, the org's first refusal,
  API calls). The `writebacks` table keeps the last 10 per connection; the audit
  has one `writeback` event per attempt with the same counts and never a value.
- **A failed or partial write** shows as `writeback_failed` in `attention()`:
  the banner on the page and `errors.sync` on `/health`. The site is unaffected;
  fix the cause (a validation rule or flow on Account, a removed field
  permission) and press Write back now. `deferred` means the org's daily API
  allowance was under 20% and the next promotion will try again.
- **Both deployments connect the same org** (`churnguard-dev`). The switch is
  per connection, so turn it on where the values should come from (production)
  and off on staging, or the two will take turns writing the same fields.
- **Cost.** One read and one write per 200 accounts per changed sync; an
  unchanged org costs one read. 2 API calls for 132 accounts on staging
  2026-10-18.

## Known gaps carried into 1.11

- The app allows any user in the org to self-authorise, and the connection runs
  as the owner's admin user, not a read-only integration user.
- No scheduled use yet, so the 30-day idle limit is a real risk until 1.11.
- The DuckDB warehouse in the image is built from the committed pull, not the
  promoted snapshot (dbt is not in the serving image). Nothing on the site reads
  the Salesforce warehouse today.
