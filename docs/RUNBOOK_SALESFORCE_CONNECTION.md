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
| The operator key | Render `OPERATOR_KEY` and Vercel `OPERATOR_KEY` (Preview, branch `staging`) | Same value in both. Gates connect, test and disconnect |
| The audit log | Neon, table `audit_events` | Append-only. Never holds a token |

Each environment gets its own row set, key, IAM user and operator key. Production
has none of these yet; its `/connections` reports `enabled: false`.

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

## Rotate the operator key

1. `openssl rand -hex 32`.
2. Render → `OPERATOR_KEY` → Save only. Vercel → `OPERATOR_KEY` (Preview,
   branch `staging`) → Save, then **redeploy** the staging preview: route
   handlers read the value at runtime, but Vercel applies a changed variable on
   the next deployment.
3. Integrations → Test connection must succeed; with the old key on either side
   it answers 403.

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
- **Roll back.** Not yet a button (1.10.4). Until then, from a laptop:
  `update serving set snapshot_id = previous_snapshot_id, promoted_by = 'operator' where tenant_id = 'default'`,
  then restart the service.

The DuckDB warehouse baked into the image is built from the committed pull,
not from the promoted snapshot (dbt is not in the serving image; see
Deferred in STATUS). Nothing on the site reads the Salesforce warehouse today.

## Known gaps carried into 1.10

- The app allows any user in the org to self-authorise, and the connection runs
  as the owner's admin user, not a read-only integration user.
- The operator key is a shared secret, not a login. 2.6 replaces it.
- No scheduled use yet, so the 30-day idle limit is a real risk until 1.11.
