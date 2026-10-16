-- 0003: candidate snapshots and their contract results (ADR-0012, sections 5 and 6).
--
-- A snapshot is what a run's raw landings become after the connector's
-- transform: the canonical tables in CSV shape, the ingest report, and the
-- result of every data contract run on them. Kept whether or not the
-- contracts passed, so a failing sync can be diagnosed; only a passing one
-- can be served (the serving pointer arrives in 1.10.3). Retention keeps the
-- last 10 (1.10.4). At ~300 KB each this lives comfortably in Postgres today
-- and would be object storage for a large tenant.
create table snapshots (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           text not null default 'default',
    connection_id       uuid not null,
    run_id              uuid not null,
    as_of               date not null,
    -- {"customers": [...], "support_tickets": [...], ...} as the CSV files
    -- would hold them: every value a string, canonical column order.
    tables              jsonb not null,
    -- IngestReport.to_dict(): the funnel and every decision the transform made.
    report              jsonb not null,
    -- {"passed": n, "failed": n, "skipped": n, "failures": ["<label>: <detail>", ...]}
    contracts           jsonb not null,
    contracts_passed    boolean not null,
    row_counts          jsonb not null,
    created_at          timestamptz not null default now()
);

create index snapshots_connection_created on snapshots (connection_id, created_at desc);

alter table sync_runs
    add column snapshot_id uuid,
    add column contracts jsonb;
