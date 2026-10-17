-- 0005: operator sign-in sessions (ADR-0013, section 2).
--
-- Forward-only, like 0001-0004.

-- The sign-in flow reuses the single-use state table the Salesforce connect
-- uses (provider = 'cognito'). Two columns it needs that Salesforce does not:
-- the OIDC nonce the ID token must carry back, and the page to return to.
alter table oauth_states
    add column nonce text,
    add column return_to text;

-- One row per signed-in browser. The cookie holds 32 random bytes; this table
-- holds only their SHA-256, so a copy of it opens no session. The Cognito
-- refresh token is sealed like the Salesforce one (envelope encryption, KMS),
-- bound to this row's id and to its own purpose, and is used only to re-check
-- the person with Cognito before an action and to revoke at sign-out.
create table sessions (
    id                  uuid primary key default gen_random_uuid(),
    id_hash             bytea not null unique,
    tenant_id           text not null,
    user_sub            text not null,
    email               text not null,
    groups              text[] not null default '{}',

    refresh_ciphertext  bytea not null,
    refresh_nonce       bytea not null,
    wrapped_data_key    bytea not null,
    master_key_version  text not null,

    created_at          timestamptz not null default now(),
    last_seen_at        timestamptz not null default now(),
    checked_at          timestamptz not null default now(),
    expires_at          timestamptz not null
);

create index sessions_user on sessions (user_sub);
create index sessions_expires on sessions (expires_at);
