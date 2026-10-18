# 0014 · Write-back: the site's risk reading on the Salesforce Account

**Status:** Accepted 2026-10-18 (the owner approved decisions 1–3 as proposed and took no extras from 4) · **Date:** 2026-10-18 · **Phase:** 9.2

## Context

The site reads a Salesforce org and shows, for every active account, a risk
score, a band and the one reason behind it. None of that reaches the people who
work in Salesforce. A CSM opening an Account there sees the fields the org
already had and nothing of what this product concluded; the reading lives only
on a site they have to open separately. ADR-0012 §2 named write-back as a later
step ("9.2, when agreed") and reserved the permission for it.

This record designs the write: **three fields on the Salesforce Account,
written after each change of what the site serves, holding exactly what the
site shows.** The owner approves the fields before anything is written
(decision 1). It is the last item of the MVP as agreed 2026-10-18.

What it must answer:

- What is written, where, and can a reader in Salesforce tell where it came from?
- When is it written, and what happens when the write fails?
- Can a write ever feed back into the score it came from?
- What can a leaked token now do that it could not before?
- How does a customer turn it on, and off?

## Decision in one paragraph

After every promotion and every rollback (the two moments what the site serves
changes), the backend takes the scores the site is now showing and writes three
custom fields on each active account's Salesforce Account record:
`ChurnGuard_Risk_Score__c`, `ChurnGuard_Risk_Band__c` and
`ChurnGuard_Top_Reason__c`. The field definitions live in the code and are
created in the org through its Tooling API at Enable. Write-back is **off until an
admin turns it on for a connection**; turning it on creates the three fields
in the org if they are missing (the owner's choice: the simplest path for a
customer) and checks them. Writes go through the Composite sObject Collections API, at most 200
records a request, only for accounts whose values changed, under the same API
reserve as the sync. A failed write never un-promotes a snapshot: the site
serves, the Integrations page says the write failed and why, and an operator
can retry. The connector never reads the three fields, so a write cannot
influence a score.

## 1. What is written

**Decision: three fields on Account, named for the product, holding what the
site shows for that account and nothing derived differently.**

| Field (API name) | Type | Value | Source on the site |
|---|---|---|---|
| `ChurnGuard_Risk_Score__c` | Number(3, 1) | 0.0–100.0 | `risk_score` on the account card and queue |
| `ChurnGuard_Risk_Band__c` | Picklist: Critical, High, Medium, Low | the band label | `risk_level`, same thresholds (80 / 60 / 40) |
| `ChurnGuard_Top_Reason__c` | Text(80) | one of the five reason labels | `risk_reason`, the dominant weighted factor |

- The prefix `ChurnGuard_` says where the value came from to anyone reading the
  record, and keeps the fields clear of the org's own (`ARR__c`, `Churn_Date__c`).
  The field labels read "ChurnGuard risk score", "ChurnGuard risk band",
  "ChurnGuard top reason"; each carries a description naming this product and
  the ADR.
- **The same number the site shows**, not a rounded, rescaled or recomputed one.
  A CSM comparing the two must find them identical, or the product has two
  opinions.
- **Which accounts:** every account the site scores, which is every customer
  that has not churned (`is_churned = 0` in the served snapshot). Prospects,
  excluded sample records and churned accounts are not written and not cleared:
  a churned account keeps the last reading it had while active, which is a true
  statement about when it was last scored. (A "clear on churn" rule is an easy
  addition if a customer wants it; decision 4.)
- **Not written, deliberately:** the "at risk" flag. On the site that is a
  ranking (the top 20% of the active book, ADR-0012 era decision 2026-10-15),
  so it changes for an account when *other* accounts move. A field that flips
  for reasons invisible on the record confuses more than it helps. The band is
  a property of the account alone. Recorded under decision 4 as an option.
- **Not written:** a timestamp field. Salesforce's own `LastModifiedDate` and
  field history (if the org enables it on these fields) already say when.
  Decision 4 offers `ChurnGuard_Scored_At__c` if the owner wants it explicit.

## 2. Where the account's Salesforce Id comes from

The served snapshot identifies a customer by `customer_id`, which the connector
sets to the account's `AccountNumber`, or `SF-<Id>` when there is none. The
write needs the Salesforce `Id`. **Decision: resolve it from the raw landings
of the connection** (`raw_records`, sobject Account, `record->>'AccountNumber'`
or `sf_id`), not by adding a column to the snapshot. The landings are the org
as last read, by the same connection, in the same database; the snapshot's
shape, its contracts and its CSV copy stay untouched. An account whose Id
cannot be resolved (an `AccountNumber` that two records share, or a landing
that has since been tombstoned) is skipped and counted, never guessed.

## 3. When it runs

**Decision: after a promotion and after a rollback, from the scores the site
is now serving; never on a restart.**

```
sync run ──▶ candidate ──▶ contracts pass ──▶ promote (pointer) ──▶ serve (restore, reload scorer)
                                                                          └─▶ write-back (this ADR)
Roll back ──▶ pointer ──▶ serve ────────────────────────────────────────────┘
restart  ──▶ restore the pointer's snapshot ──▶ no write-back
```

- The write reads the scorer the API has just reloaded, so it writes exactly
  what the pages will return. A write computed from the candidate before
  serving could differ from the site if serving failed.
- A rollback changes what the site shows, so Salesforce follows it; otherwise
  the org would hold readings from a snapshot the operator just withdrew.
- A restart serves the same snapshot it served before it slept; nothing changed,
  nothing is written. (Render sleeps several times a day; writing on every wake
  would be dozens of pointless updates and modified-dates.)
- **Only changes are written.** One SOQL reads the three fields for the
  accounts in question first; a record whose stored values already equal what
  the site shows is skipped. Rewriting an unchanged value would still bump
  `LastModifiedDate`, fire the org's triggers and flows, and show up in field
  history as a non-change. On an unchanged org a sync therefore writes nothing.

## 4. How it writes

| Concern | Decision |
|---|---|
| **API** | `PATCH /services/data/v59.0/composite/sobjects` with `allOrNone: false`, up to 200 records per request, each `{"attributes": {"type": "Account"}, "Id": …, three fields}`. 187 customers today is one request after the read; the cap is Salesforce's |
| **Partial failure** | `allOrNone: false` so one record the org refuses (validation rule, a locked record, a field-level security change) fails alone; its Id and the org's error code are recorded, the rest land |
| **Authentication** | The stored connection, through `TokenService` extended from GET-only to GET and PATCH; same refresh-once-on-401, same `Sforce-Limit-Info` reading |
| **API reserve** | Same rule as the sync (ADR-0012 §5): under 20% of the daily allowance remaining, the write is deferred and reported, not attempted. Cost: one read plus one write per 200 accounts |
| **Feedback** | The connector's `FIELDS` do not include the three fields and will not. A write can change `SystemModstamp`, so the next incremental sync re-reads the written accounts (one extra query page at today's size): harmless, and it cannot change a score |
| **Concurrency** | A write-back runs inside the serving step, which runs inside the single-flight sync run or the rollback action; two cannot overlap on one connection |
| **Ordering** | The site is updated first, then Salesforce. If the process dies between the two, the site is right and the org is one sync behind, which the next promotion corrects. The other order would put a reading in the org that the site never showed |

## 5. Turning it on, and off

**Decision: off by default, per connection, switched by an admin on the
Integrations page; switching on verifies the fields.**

- `connections.writeback_enabled` (migration 0006, default false). A customer
  connecting their org gets reads only until an admin opts in to writes, which
  is the posture a CRM owner expects.
- **Enable** (`POST /connections/{id}/writeback`, admin, same-origin, Cognito
  re-check like every action): the backend creates whichever of the three
  fields the org lacks, grants itself read and edit on them, confirms all
  three are present and updateable, and only then flips the flag (§6). A
  failure names the step and the org's reason. Audited `writeback_enabled`.
- **Disable** (`DELETE`, admin): flips the flag; nothing is cleared in the org.
  Audited `writeback_disabled`.
- **Write back now** (`POST /connections/{id}/writeback/run`, operator): the
  retry after a failed write, or the first write after enabling without waiting
  for a sync. Audited like an automatic one.
- The page shows, per connection: off / on, the last write (when, how many
  written, skipped as unchanged, failed with the first error), and the button.
  `attention()` (ADR-0012 §8) gains "last write-back failed" so the banner and
  `/health` say so without changing the health status.

## 6. The field definitions

**Decision (owner, 2026-10-18, "the simplest way"): the backend creates the
three fields itself when an admin enables write-back, through the connected
org's Tooling API. No CLI, no package, nothing for the customer's admin to
install.**

Enable does, in order, and stops at the first failure with the reason:

1. `describe Account`: which of the three fields already exist, with the right
   type. Existing ones are left exactly as they are (never altered, never
   re-typed).
2. For each missing field, `POST /tooling/sobjects/CustomField` with the
   definition from §1 (label, API name, type, length or precision, picklist
   values, a description naming this product and this record).
3. Field-level security: a field created through the API is visible to no
   profile until it is granted, so Enable inserts `FieldPermissions` (read and
   edit) for the connected user's own profile, the only user that writes. The
   customer's admin extends visibility to CSMs in Setup like any other field;
   the Enable result says so.
4. `describe Account` again: all three present and updateable for the
   connected user. Only then is the flag flipped. Each step's outcome is in the
   audit event.

What it needs and costs:

- The connected user must hold **Customize Application** (every System
  Administrator does; the owner's login does). A user without it gets the
  Tooling API's own refusal, named on the page: "the connected user cannot
  create fields; ask a Salesforce admin to connect, or to create these three
  fields". That is the whole fallback, and the field definitions in the error
  are enough for an admin to make them by hand in Setup in five minutes.
- The product changes the customer's schema when they ask it to. That is the
  trade: one click for the admin against change control they may want. It is
  opt-in, done by an admin, only on Enable, only these three fields, never
  deleting or altering anything, and each creation is audited here and in the
  org's own Setup Audit Trail.
- Field creation is a metadata call, not a data row: it does not count against
  the org's full data storage (STATUS, 5 of 5 MB), and a value on an existing
  record adds nothing to that record's allocation. Verified at the first
  Enable on `churnguard-dev`.
- Three Tooling calls plus two describes, once per org. Nothing on later syncs.

The two routes not taken, kept for when they fit: **committed metadata**
deployed by the customer's admin (`sf project deploy`, or the same files as a
change set) for an admin who wants the schema change in their own hands, and a
**managed package** (namespaced fields, AppExchange security review, months)
when selling to many orgs. Both keep the same three API names, so either is a
packaging step later with no change to the write.

## 7. Record keeping

- **Table `writebacks`** (migration 0006): one row per attempt: tenant,
  connection, snapshot id, trigger (`promotion`, `rollback`, `manual`), status
  (`succeeded`, `partial`, `failed`, `deferred`), counts (candidates, resolved,
  unchanged, written, failed), the first error, API requests and usage, started
  and finished. Retained with the snapshots: the last 10 per connection.
- **Audit events:** `writeback` (every attempt, with the counts and status),
  `writeback_enabled`, `writeback_disabled`. Never a field value per account:
  the counts are the record, the org's own field history is the per-record one.
- **Run row:** the sync run's `sync_finished` audit detail gains
  `writeback: <status>` so one event tells the whole story of a run.

## 8. Threat model, what changes

| Threat | Before | Now |
|---|---|---|
| Leaked access token | Reads four objects | Also edits three fields on Account. Still cannot create, delete, or touch any other field, once the permission set exists; today the token is the admin's and could do anything, the ADR-0012 §2 gap unchanged |
| A wrong score written to the org | — | The same wrong score is already on the site; the write adds reach, not a new error. Rollback corrects both within one action |
| A flood of writes | — | One write per promotion or rollback, only changed records, 200 per request, under the API reserve, single-flight |
| Write-back used to probe the org | — | Only the three fields, only on accounts the sync already read, only after an admin enabled it |
| Both deployments writing one org | — | Staging and production both connect `churnguard-dev` today. Enabled per connection, so the owner enables it where she wants the values to come from (production), and proves it on staging first with staging's switch then turned off. Recorded under Known gaps while the two share an org |

## 9. Production design against this deployment

Nothing in this record changes with the hosting tier. The stand-ins are the ones
ADR-0012 §10 and ADR-0013 §9 already name (long-lived IAM key, free Postgres,
GitHub's scheduler). The one new stand-in is the connected user: the owner's
admin login, whose token can edit anything, where production uses the
integration user with edit on three fields. Already a recorded gap (ADR-0012
§2); this record makes closing it worth more.

## 10. Delivery

| Step | Work | Proof |
|---|---|---|
| **9.2.1** | This ADR; the three field definitions in code, created at Enable (Tooling API, field permissions); migration 0006 (`writeback_enabled`, `writebacks`); `TokenService` PATCH; `src/integrations/writeback.py` (resolve Ids, read current, diff, write in batches, record, audit); the hook after serving on promotion and rollback; routes enable / disable / run / status; tests against the fake org (fields created when missing, left alone when present, no Customize Application, unchanged skipped, partial failure, reserve, rollback writes, restart does not) | pytest; first Enable on `churnguard-dev` from staging creates the real fields |
| **9.2.2** | Integrations page: the switch, the last-write line, Write back now, the attention banner | Proven on staging: enable, sync, the three fields visible on an Account in the org and equal to the site; roll back and see them follow; disable |
| **9.2.3** | Record: STATUS, runbook (enable, the shared-org note), ADR-0012 §2 amended to point here; merge to `main`; enable on production; staging's switch off | Production Account records carry the live site's readings |

## Consequences

- A CSM in Salesforce sees the product's reading on the record they already
  work from, and can build their own list views and reports on it. That is the
  last mile the MVP was missing.
- The product now writes to a customer's CRM. Every write is opt-in, diffed,
  counted, audited and reversible by the next promotion or rollback.
- Two more API calls per changed sync, within the reserve.
- One more thing to prove on each environment, and one more switch in the runbook.

## Decisions for the owner (before any write)

1. **The three fields**: `ChurnGuard_Risk_Score__c` (Number 3,1),
   `ChurnGuard_Risk_Band__c` (picklist Critical / High / Medium / Low),
   `ChurnGuard_Top_Reason__c` (Text 80). Approve as named, or rename.
2. **On which accounts**: every active (not churned) customer the site scores;
   churned accounts keep their last reading. Approve, or choose "clear on churn".
3. **Opt-in per connection** by an admin, off by default. Approve.
4. **Optional extras, not built unless chosen**: `ChurnGuard_At_Risk__c`
   (checkbox, the top-20% ranking; changes with the book, see §1) and
   `ChurnGuard_Scored_At__c` (date/time of the write).
