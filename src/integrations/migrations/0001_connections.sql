-- 0001: connections, OAuth state, audit (ADR-0012, sections 3, 4 and 8).
--
-- Forward-only. A change to any of these tables is a new file, never an edit
-- here: this file has already run against the staging database.

-- One row per tenant per Salesforce org. The refresh token is the only secret
-- and it is stored sealed: AES-256-GCM under a per-row data key, and that data
-- key wrapped by a master key held outside this database (KMS). A copy of this
-- table decrypts nothing.
create table connections (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           text not null default 'default',
    provider            text not null default 'salesforce',
    auth_method         text not null default 'web_server_pkce'
                        check (auth_method in ('web_server_pkce', 'jwt_bearer')),

    -- From the token response's identity URL, then pinned: a reconnect that
    -- returns a different org is refused (unique below, checked in code too).
    org_id              text not null,
    -- Validated before first use (host ends in .my.salesforce.com, https, default
    -- port) and then the only host API calls go to.
    instance_url        text not null,
    -- login.salesforce.com or test.salesforce.com: which environment authorised.
    login_host          text not null,
    user_id             text not null,
    username            text,
    scopes              text not null,

    status              text not null default 'active'
                        check (status in ('active', 'needs_reauth', 'revoked', 'disabled')),
    status_reason       text,

    -- The sealed refresh token. Associated data binds the ciphertext to this
    -- row's id, so a ciphertext moved onto another row fails to open.
    token_ciphertext    bytea not null,
    token_nonce         bytea not null,
    wrapped_data_key    bytea not null,
    master_key_version  text not null,

    token_issued_at     timestamptz not null,
    last_refreshed_at   timestamptz,
    last_used_at        timestamptz,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),

    unique (tenant_id, provider, org_id)
);

-- The server-side half of an authorisation in flight: the PKCE verifier and the
-- state the callback must present. Single use, ten minutes. The browser holds
-- neither; it only carries the one-time code back.
create table oauth_states (
    state               text primary key,
    tenant_id           text not null default 'default',
    provider            text not null default 'salesforce',
    code_verifier       text not null,
    login_host          text not null,
    redirect_uri        text not null,
    -- Set when a connection already exists: the callback must return this org.
    expected_org_id     text,
    created_at          timestamptz not null default now(),
    expires_at          timestamptz not null,
    consumed_at         timestamptz
);

create index oauth_states_expires_at on oauth_states (expires_at);

-- Append-only. Nothing updates or deletes a row here; purging a tenant is the one
-- exception and is itself the last event written for it.
create table audit_events (
    id                  bigserial primary key,
    at                  timestamptz not null default now(),
    event               text not null,
    actor               text not null,
    tenant_id           text not null default 'default',
    connection_id       uuid,
    org_id              text,
    detail              jsonb not null default '{}'::jsonb
);

create index audit_events_tenant_at on audit_events (tenant_id, at desc);
