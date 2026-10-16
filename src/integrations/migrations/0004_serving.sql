-- 0004: the serving pointer (ADR-0012, section 6).
--
-- One row per tenant naming the snapshot the site serves. Promotion is an
-- upsert of this row in the same transaction that records the run's result,
-- so the site never points at a snapshot that is not fully stored, and a
-- rollback (1.10.4) is the same upsert pointing at an earlier snapshot. The
-- previous pointer is kept on the row for the audit and for a one-step undo.
create table serving (
    tenant_id               text primary key,
    snapshot_id             uuid not null,
    run_id                  uuid,
    previous_snapshot_id    uuid,
    promoted_at             timestamptz not null default now(),
    promoted_by             text not null
);
