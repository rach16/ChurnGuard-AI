# 0012 · Salesforce integration architecture: connections, token custody, sync

**Status:** Proposed · **Date:** 2026-10-15 · **Phase:** 1.9, 1.10, 1.11

## Context

Since 2026-10-15 every number on the live site comes from Salesforce (1.8). The
pull that produces it runs from a developer's terminal, through the Salesforce CLI
login, by hand. That is a demo of a connector, not an integration.

An integration a customer's security team would approve has to answer questions
the terminal pull never meets:

- Who authorised access, to which org, with what scope, and how is it revoked?
- Where do credentials live, who can read them, and what does a leak expose?
- What happens when a token expires, is revoked, or the org hits its API limit?
- How does a sync resume after a failure without duplicating or losing records?
- What reaches the site if the new data breaks the contracts?
- What is recorded, so an auditor can reconstruct who did what?

This record decides those answers before any code is written. It is written for
the production design; where a free-tier deployment cannot meet it, the stand-in
is named in section 9 rather than hidden.

## 1. Authorisation flow

**Decision: OAuth 2.0 authorization-code flow with PKCE and `state`, started from
the Integrations page in a popup, completed on the server.**

```
Browser                      ChurnGuard API                 Salesforce
   │ click Connect                 │                             │
   │──POST /connections/start────▶│ create state + PKCE verifier│
   │◀─ authorize URL ──────────────│ (stored server-side, 10 min)│
   │──open popup ─────────────────────────────────────────────▶ │ login + consent
   │◀──────────────────── redirect /auth/salesforce?code&state ──│
   │──POST /connections/callback (code, state)──▶│               │
   │                               │──code + verifier + secret──▶│ token endpoint
   │                               │◀──access, refresh, id, url──│
   │                               │ validate, pin org, encrypt   │
   │◀── connection: active ────────│ audit event                  │
```

- **PKCE (S256)** so an intercepted code is useless without the verifier, which
  never leaves the server. **`state`** is single-use, bound to the operator's
  session, and expires in 10 minutes; a mismatch is rejected and audited.
- The verifier and state live **server-side**, not in the browser. The browser only
  ever holds the one-time code, which cannot be redeemed without the client secret.
- Production orgs authorise at `login.salesforce.com`; sandboxes at
  `test.salesforce.com`. The environment is chosen at connect time and recorded.
  Developer Edition orgs, `churnguard-dev` included, are production-type and use
  `login.salesforce.com`.

**Alternatives considered**

| Flow | Why not the default |
|---|---|
| Username-password | Disabled by default in new orgs, puts a password and security token in our hands, no MFA. Rejected |
| Client-side token storage | Any XSS steals a live CRM token. Rejected |
| JWT bearer (server-to-server, certificate) | The strongest option for unattended sync: no refresh token at rest, only a private key. It needs the customer's admin to pre-authorise the app and the integration user. **Offered as the enterprise option** once a customer asks; the connection model below supports it as a second `auth_method` |

## 2. App registration and least privilege

- Registered as a Salesforce **External Client App** with PKCE required, callback
  URLs restricted to the exact staging and production origins, and IP relaxation
  left at the default.
- **Scopes: `api refresh_token` only.** No `full`, no `web`.
- The customer connects as a **dedicated integration user** with a permission set
  granting **read-only** access to exactly Account, Opportunity, Case and Task, and
  the fields the connector selects. A leaked token can read those objects and
  nothing else, and can write nothing. Write-back (9.2, when agreed) adds create on
  Task only, as a separate permission set.
- Refresh-token policy is set on the app: valid until revoked, with **refresh-token
  rotation enabled** where the org supports it. Rotation means every refresh issues
  a new refresh token and the old one stops working, so a copied token has a short
  useful life.

## 3. Connection model

One row per tenant per Salesforce org.

| Field | Purpose |
|---|---|
| `id`, `tenant_id` | Every query is scoped by tenant |
| `provider`, `auth_method` | `salesforce`; `web_server_pkce` or `jwt_bearer` |
| `org_id` | From the token response's identity URL. **Pinned**: a reconnect that returns a different org is refused and audited |
| `instance_url` | **Validated** before first use: parsed as a URL (not string-matched), scheme `https`, default port, no user info, and the host ends in `.my.salesforce.com`. A suffix match, not a one-label wildcard, because real hosts carry extra labels: Developer Edition is `<name>.develop.my.salesforce.com` (`churnguard-dev` is `orgfarm-e065461ef9-dev-ed.develop.my.salesforce.com`), sandboxes `<name>.sandbox.my.salesforce.com`. My Domain is mandatory for every org, so no legacy `*.salesforce.com` instance host is accepted. **Pinned** with `org_id`: after the first connect, API calls go only to that exact host, and a changed host is accepted only on a reconnect that passes the org pin and is audited. Prevents a tampered response turning the sync into a request to an arbitrary host |
| `environment` | `production` or `sandbox` |
| `scopes`, `connected_by`, `connected_at` | Who granted what, when |
| `status` | `active`, `needs_reauth`, `revoked`, `disabled` |
| `last_sync_run_id`, `last_success_at` | Health |

Unique on `(tenant_id, org_id)`: the same org cannot be connected twice into one tenant.

## 4. Token custody

| Token | Where it lives | Lifetime |
|---|---|---|
| **Refresh token** | Postgres, **envelope-encrypted** (below) | Until revoked or rotated |
| **Access token** | **Never persisted.** Process memory, cached per connection until it expires | Org session timeout (default 2 h) |
| Authorization code, PKCE verifier, `state` | Server-side, single-use, 10-minute expiry | Minutes |
| Client secret, master key | Secrets manager (section 9) | Rotated by procedure |

**Envelope encryption.** Each connection gets its own random 256-bit data key. The
refresh token is encrypted with AES-256-GCM under that data key, with the
connection id as associated data, so a ciphertext copied onto another connection
fails to decrypt. The data key is itself encrypted ("wrapped") by a master key held
outside the database. Stored per connection: `ciphertext`, `nonce`,
`wrapped_data_key`, `master_key_version`.

- A database dump alone yields nothing usable; the master key alone yields nothing.
- **Key rotation** re-wraps data keys under the new master key version without
  touching the tokens; old versions stay readable until the rewrap completes.
- Decryption happens only inside the sync worker, only for the connection being
  synced, and the plaintext never leaves that call.

**Refresh.** On `INVALID_SESSION` or within five minutes of expiry, the worker
refreshes. A per-connection **advisory lock** makes refresh single-flight, so two
jobs cannot race and, with rotation on, invalidate each other's token. A rotated
refresh token is re-encrypted and stored in the same transaction.

**Revocation and loss.** `invalid_grant` on refresh moves the connection to
`needs_reauth`, pauses its syncs, and surfaces on the Integrations page. Disconnect
calls Salesforce's revoke endpoint, deletes the encrypted token, and applies the
retention policy to the data (section 7).

**Never logged.** Tokens, codes, verifiers and secrets are redacted by a logging
filter, and a test fails if any appears in captured logs, error bodies or audit rows.

## 5. Sync engine

```
trigger (schedule · Sync now · on wake)
   └─▶ sync_run (pending → running → succeeded | failed | degraded)
         ├─ lock connection (single-flight)
         ├─ for each object: query changes since watermark ──▶ land raw (immutable, run_id)
         ├─ deletions since watermark ──▶ tombstones
         ├─ transform (existing connector rules) ──▶ candidate snapshot
         ├─ contracts + dbt tests on candidate
         └─ pass: promote atomically · fail: keep last good, mark degraded
```

| Concern | Decision |
|---|---|
| **First sync** | Full read. REST at current volume; Bulk API 2.0 above ~10k records per object. Same interface |
| **Incremental** | Per-object watermark on `SystemModstamp` (indexed, and updated by system changes `LastModifiedDate` misses). Watermark advances only after a run's records are landed, with a small overlap window; upsert by Salesforce Id makes the overlap harmless |
| **Deletes** | `queryAll` with `IsDeleted`, written as tombstones, so a record deleted in Salesforce leaves the site |
| **Idempotency** | Raw records are landed per run and upserted by `(connection_id, sobject, sf_id)`. Re-running a run never duplicates |
| **Resumability** | A failed run resumes from the last committed watermark per object, not from zero |
| **API limits** | Every response's `Sforce-Limit-Info` is recorded. Below 20% of the daily allowance remaining, scheduled syncs defer; `REQUEST_LIMIT_EXCEEDED` backs off with jitter. Usage is shown per connection |
| **Concurrency** | One run per connection at a time (advisory lock); a second trigger joins the running one |
| **Schema drift** | A field removed or renamed in Salesforce fails the query or the contracts; the run is `degraded`, the site keeps the last good snapshot, and the failure names the field |

## 6. Promotion: what the site serves

The site never serves a half-finished or contract-failing sync.

- Each successful run produces a complete **snapshot** of the canonical tables and
  its warehouse build. Promotion flips one pointer, `serving_snapshot_id`, in a
  single transaction.
- A run that fails the 22 data contracts or the dbt tests is kept for diagnosis and
  **not promoted**. The Integrations page shows "serving the run of <date>; latest
  run failed: <reason>".
- The last N snapshots are retained, so a bad promotion rolls back by moving the
  pointer.

## 7. Data lifecycle

| Event | Effect |
|---|---|
| Field selection | The connector reads only the fields it uses. No contact PII is read at all today; adding any requires a recorded decision |
| Retention | Raw landings kept 30 days; snapshots, last 10 |
| Disconnect | Token revoked and deleted immediately. Data retained for 30 days for recovery, then purged, unless the tenant requests immediate purge |
| Tenant deletion | All rows under the tenant purged, verified by a count of zero |

## 8. Audit, observability, operations

- **Audit events** (append-only): connect, reconnect, org-mismatch refusal, refresh,
  refresh failure, sync start and finish, promotion, rollback, disconnect, purge.
  Each records actor, tenant, connection, org and time. **Never a token value.**
- **Run metrics**: duration, records read per object, API calls used, retries,
  outcome. Shown per connection on the Integrations page.
- **Alerts**: `needs_reauth`, two consecutive failed runs, contract failures, API
  allowance under 20%.
- **Operator access**: connect, sync and disconnect are operator actions. They
  require an authenticated operator session, not a shared key. Until operator
  login exists (2.6), these controls are enabled on staging only, which Vercel
  already restricts to the project owner.
- **Runbook** in `docs/`: rotate the client secret, rotate the master key, reconnect
  after `needs_reauth`, roll back a promotion, purge a tenant.

## 9. Threat model

| Threat | Mitigation |
|---|---|
| Database stolen | Refresh tokens envelope-encrypted; master key elsewhere |
| Master key leaked | Alone it decrypts nothing; rotate and rewrap |
| Code intercepted on redirect | PKCE |
| Forged callback (CSRF) | `state`, single-use, session-bound, expiring |
| XSS on the site | No token ever reaches the browser |
| Org swap on reconnect | `org_id` pinned |
| SSRF through `instance_url` | Parsed host suffix check before first use, then the exact host pinned |
| Token in logs | Redaction filter plus a failing test |
| Over-broad access | Read-only integration user, two scopes |
| Stale or broken data shown | Contracts gate promotion; last good snapshot kept |
| One tenant reading another | Tenant id on every row and query |

## 10. Production design against this deployment

The design does not change with the hosting tier. What changes is where a few
pieces live, and each stand-in is stated where it is used.

| Production | This deployment | Cost |
|---|---|---|
| KMS-held master key (AWS KMS, envelope via `GenerateDataKey`) | **Decided 2026-10-15: a local master key in the Render secret store**, behind the same `KeyProvider` interface. Weaker than KMS: the key sits in the app's environment, with no hardware boundary and no per-use audit trail from the key service. Moving to KMS is a new `KeyProvider` plus a rewrap, no schema or caller change | $0 (KMS would be ~$1/mo) |
| Managed Postgres | Neon free tier (0.5 GB): connections, secrets, runs, watermarks, audit, raw landings, snapshots | $0 |
| Durable snapshot storage | Postgres. Render's disk is wiped on every sleep, so a waking server restores the serving snapshot from Postgres before it answers | $0 |
| Scheduler | Sync now, plus sync on wake, plus an external scheduler calling a signed, operator-only trigger. The same scheduler doubles as the keep-alive the backend has lacked | $0 |
| Bulk API 2.0 | REST at 294 accounts; Bulk behind the same interface | $0 |
| Operator login (2.6) | Staging only until it exists | $0 |

## 11. Delivery

| Item | Delivers | Effort |
|---|---|---|
| **1.9 Connections and token custody** | Postgres schema and migrations; External Client App; PKCE + `state` flow; connection model with org pinning and `instance_url` allowlist; `KeyProvider` with envelope encryption, rotation, rewrap; refresh with single-flight lock and rotation; revoke and disconnect; audit events; log redaction; staging-only operator gate | ~3 days |
| **1.10 Sync engine and promotion** | Sync runs; raw landing; per-object watermarks; deletes; idempotent upserts; resumability; limit tracking and backoff; contracts-gated atomic promotion; restore on wake; retention | ~3–4 days |
| **1.11 Operations** | Sync now; scheduled and on-wake triggers; health and usage on the Integrations page; alerts; runbook | ~2 days |

**Testing, every item:** a fake Salesforce for unit tests (token exchange, refresh,
rotation, `invalid_grant`, limits, pagination, deletes); property tests on
encryption round-trips, wrong associated data and key versions; failure injection
(token expiry mid-run, crash mid-run then resume, contract failure blocks
promotion); security tests (state mismatch, org swap, off-allowlist
`instance_url`, no secret in any log or response); the allowlist accepts
`churnguard-dev`'s real host and rejects look-alikes such as
`https://my.salesforce.com.evil.com`, `https://evil.com/x.my.salesforce.com`,
`https://x.my.salesforce.com@evil.com`, `http://` and a non-default port; and an end-to-end run against
`churnguard-dev` on staging before anything reaches the live site.

## Consequences

- The terminal pull stops being how data arrives. It stays as a developer tool.
- A Postgres dependency enters the system, with migrations and a backup story.
- The live site's data becomes as fresh as the last promoted sync, and its
  freshness is visible on every page.
- Master key decided 2026-10-15: local key in the Render secret store ($0), KMS named as the production swap.
