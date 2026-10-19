-- 0007: personal access tokens for MCP clients (ADR-0016 section 10a).
--
-- Forward-only, like 0001-0006.

-- One row per token. The token is 32 random bytes shown once; this table holds
-- only its SHA-256, so a copy of it reads nothing. A token reads as the person
-- who made it, in their tenant. It ends at its expiry, when its owner revokes
-- it, or when the person is removed or disabled (revoked at its next use).
create table access_tokens (
    id              uuid primary key default gen_random_uuid(),
    token_hash      bytea not null unique,
    prefix          text not null,             -- "cg_pat_ab12", to tell tokens apart on the page
    name            text not null,
    tenant_id       text not null,
    user_sub        text not null,
    email           text not null,

    created_at      timestamptz not null default now(),
    expires_at      timestamptz not null,
    last_used_at    timestamptz,
    checked_at      timestamptz not null default now(),   -- last confirmed with Cognito
    revoked_at      timestamptz,
    revoked_reason  text
);

create index access_tokens_user on access_tokens (user_sub);
