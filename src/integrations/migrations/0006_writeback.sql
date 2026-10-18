-- 0006: write-back to Salesforce (ADR-0014, sections 5 and 7).
--
-- Forward-only and additive, like the five before it.

-- Off until an admin enables it for the connection (section 5). Enabling
-- creates the three ChurnGuard fields in the org if they are missing and
-- verifies them; the flag is flipped only after that.
alter table connections add column writeback_enabled boolean not null default false;

-- One row per attempt to write the site's readings to the org: after a
-- promotion, after a rollback, or by hand. Counts, never values: the org's own
-- field history is the per-record record. Kept with the snapshots (last 10
-- per connection) by the retention job.
create table writebacks (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           text not null default 'default',
    connection_id       uuid not null,
    snapshot_id         uuid,
    trigger             text not null check (trigger in ('promotion', 'rollback', 'manual')),
    status              text not null
                        check (status in ('succeeded', 'partial', 'failed', 'deferred', 'skipped')),
    -- candidates: accounts the site scores; resolved: with a Salesforce Id;
    -- unchanged: already holding these values; written; failed.
    counts              jsonb not null default '{}'::jsonb,
    -- The first refusal from the org, or why nothing was attempted.
    error               text,
    api_requests        integer not null default 0,
    api_usage           jsonb,
    actor               text not null,
    started_at          timestamptz not null default now(),
    finished_at         timestamptz
);

create index writebacks_connection_started on writebacks (connection_id, started_at desc);
