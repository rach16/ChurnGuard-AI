-- 0002: sync runs, raw landings, watermarks (ADR-0012, section 5).
--
-- Forward-only, like 0001. Nothing here references a connection by foreign
-- key on purpose: a run and its landings outlive a disconnect for the retention
-- window in section 7, and are purged by the retention job (1.10.4), not by a
-- cascade nobody audited.

-- One row per sync. The row is also the lock: the partial unique index below
-- lets a connection have at most one unfinished run, so a second trigger finds
-- it and joins instead of starting another. A run left 'running' by a process
-- that died is swept to 'failed' ("abandoned") by the next trigger.
create table sync_runs (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           text not null default 'default',
    connection_id       uuid not null,
    org_id              text not null,
    trigger             text not null check (trigger in ('manual', 'schedule', 'wake')),
    status              text not null default 'pending'
                        check (status in ('pending', 'running', 'succeeded', 'failed', 'degraded')),
    -- Anchor date for the transform (1.10.2): tenure, "already closed".
    as_of               date not null,
    -- Per object: mode, records fetched, tombstones, requests, watermarks.
    objects             jsonb not null default '{}'::jsonb,
    api_requests        integer not null default 0,
    -- The last Sforce-Limit-Info seen: {"used": n, "max": n}.
    api_usage           jsonb,
    error               text,
    created_at          timestamptz not null default now(),
    started_at          timestamptz,
    finished_at         timestamptz
);

create index sync_runs_connection_created on sync_runs (connection_id, created_at desc);
create unique index sync_runs_one_unfinished on sync_runs (connection_id)
    where status in ('pending', 'running');

-- Where each object's incremental read starts from. Written in the same
-- transaction as the records it covers, so a crash between the two is not
-- possible: either the records and the watermark both landed, or neither did.
create table sync_watermarks (
    connection_id       uuid not null,
    sobject             text not null,
    watermark           timestamptz not null,
    run_id              uuid not null,
    updated_at          timestamptz not null default now(),
    primary key (connection_id, sobject)
);

-- The org as last seen, one row per Salesforce record, upserted by Id so a
-- re-run never duplicates. A record deleted in Salesforce stays as a tombstone
-- (deleted = true) so it leaves the site at the next transform. The record is
-- stored as Salesforce returned it, minus the "attributes" envelope; no
-- contact PII is among the fields read (section 7).
create table raw_records (
    connection_id       uuid not null,
    sobject             text not null,
    sf_id               text not null,
    record              jsonb not null,
    system_modstamp     timestamptz,
    deleted             boolean not null default false,
    first_run_id        uuid not null,
    last_run_id         uuid not null,
    first_seen_at       timestamptz not null default now(),
    last_seen_at        timestamptz not null default now(),
    primary key (connection_id, sobject, sf_id)
);

create index raw_records_live on raw_records (connection_id, sobject) where not deleted;
