"""
The connection row and the OAuth state behind it (ADR-0012, sections 1 and 3).

A store, not a service: it reads and writes rows and nothing else. Deciding whether
an org may connect, exchanging codes for tokens and talking to Salesforce belong to
the flow module (1.9 step 2), which calls this with the result.

One tenant today (``default``), and one Salesforce org per tenant: ``get`` returns
the connection for a tenant, and the unique constraint on (tenant, provider, org)
is what makes a reconnect an update of the same row rather than a second one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from integrations.crypto import Sealed
from integrations.db import Database

DEFAULT_TENANT = "default"
PROVIDER = "salesforce"
STATE_TTL_SECONDS = 600


@dataclass(frozen=True)
class Connection:
    id: str
    tenant_id: str
    provider: str
    auth_method: str
    org_id: str
    instance_url: str
    login_host: str
    user_id: str
    username: Optional[str]
    scopes: str
    status: str
    status_reason: Optional[str]
    sealed: Sealed
    token_issued_at: datetime
    last_refreshed_at: Optional[datetime]
    last_used_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    def public(self) -> dict[str, Any]:
        """What an endpoint may return: everything but the sealed token."""
        return {
            "id": self.id, "provider": self.provider, "auth_method": self.auth_method,
            "org_id": self.org_id, "instance_url": self.instance_url,
            "login_host": self.login_host, "user_id": self.user_id, "username": self.username,
            "scopes": self.scopes, "status": self.status, "status_reason": self.status_reason,
            "token_issued_at": self.token_issued_at, "last_refreshed_at": self.last_refreshed_at,
            "last_used_at": self.last_used_at, "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _row_to_connection(r: dict[str, Any]) -> Connection:
    return Connection(
        id=str(r["id"]), tenant_id=r["tenant_id"], provider=r["provider"],
        auth_method=r["auth_method"], org_id=r["org_id"], instance_url=r["instance_url"],
        login_host=r["login_host"], user_id=r["user_id"], username=r["username"],
        scopes=r["scopes"], status=r["status"], status_reason=r["status_reason"],
        sealed=Sealed(bytes(r["token_ciphertext"]), bytes(r["token_nonce"]),
                      bytes(r["wrapped_data_key"]), r["master_key_version"]),
        token_issued_at=r["token_issued_at"], last_refreshed_at=r["last_refreshed_at"],
        last_used_at=r["last_used_at"], created_at=r["created_at"], updated_at=r["updated_at"],
    )


_COLUMNS = ("id, tenant_id, provider, auth_method, org_id, instance_url, login_host, user_id,"
            " username, scopes, status, status_reason, token_ciphertext, token_nonce,"
            " wrapped_data_key, master_key_version, token_issued_at, last_refreshed_at,"
            " last_used_at, created_at, updated_at")


class ConnectionStore:
    def __init__(self, db: Database):
        self.db = db

    # ----------------------------------------------------------------- connections

    def get(self, tenant_id: str = DEFAULT_TENANT, provider: str = PROVIDER,
            conn=None) -> Optional[Connection]:
        sql = (f"select {_COLUMNS} from connections where tenant_id = %s and provider = %s"
               " order by created_at limit 1")
        if conn is not None:
            row = conn.execute(sql, (tenant_id, provider)).fetchone()
        else:
            with self.db.connection() as c:
                row = c.execute(sql, (tenant_id, provider)).fetchone()
        return _row_to_connection(row) if row else None

    def all(self, conn) -> list[Connection]:
        """Every connection, oldest first. For operations that walk the table."""
        rows = conn.execute(f"select {_COLUMNS} from connections order by created_at").fetchall()
        return [_row_to_connection(r) for r in rows]

    def get_by_id(self, connection_id: str, conn=None) -> Optional[Connection]:
        sql = f"select {_COLUMNS} from connections where id = %s"
        if conn is not None:
            row = conn.execute(sql, (connection_id,)).fetchone()
        else:
            with self.db.connection() as c:
                row = c.execute(sql, (connection_id,)).fetchone()
        return _row_to_connection(row) if row else None

    def new_id(self, conn) -> str:
        """An id before the row exists, because the token is sealed to it."""
        return str(conn.execute("select gen_random_uuid() as id").fetchone()["id"])

    def upsert(self, conn, *, id: str, tenant_id: str, org_id: str, instance_url: str,
               login_host: str, user_id: str, username: Optional[str], scopes: str,
               sealed: Sealed, token_issued_at: datetime,
               auth_method: str = "web_server_pkce") -> Connection:
        """Insert, or on a reconnect to the same org replace the token and reactivate.

        On conflict the existing row's id is kept, which is why a reconnect must seal
        the new token to the *existing* id: callers look the connection up first and
        pass its id. A fresh id only reaches the insert branch.
        """
        row = conn.execute(
            f"""
            insert into connections (id, tenant_id, provider, auth_method, org_id, instance_url,
                login_host, user_id, username, scopes, status, status_reason,
                token_ciphertext, token_nonce, wrapped_data_key, master_key_version,
                token_issued_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', null,
                %s, %s, %s, %s, %s)
            on conflict (tenant_id, provider, org_id) do update set
                instance_url = excluded.instance_url,
                login_host = excluded.login_host,
                user_id = excluded.user_id,
                username = excluded.username,
                scopes = excluded.scopes,
                status = 'active',
                status_reason = null,
                token_ciphertext = excluded.token_ciphertext,
                token_nonce = excluded.token_nonce,
                wrapped_data_key = excluded.wrapped_data_key,
                master_key_version = excluded.master_key_version,
                token_issued_at = excluded.token_issued_at,
                last_refreshed_at = null,
                updated_at = now()
            returning {_COLUMNS}
            """,
            (id, tenant_id, PROVIDER, auth_method, org_id, instance_url, login_host, user_id,
             username, scopes, sealed.ciphertext, sealed.nonce, sealed.wrapped_data_key,
             sealed.master_key_version, token_issued_at),
        ).fetchone()
        return _row_to_connection(row)

    def update_token(self, conn, connection_id: str, sealed: Sealed,
                     refreshed_at: datetime) -> None:
        """After a refresh with rotation: the new token replaces the old in place."""
        conn.execute(
            "update connections set token_ciphertext = %s, token_nonce = %s,"
            " wrapped_data_key = %s, master_key_version = %s, last_refreshed_at = %s,"
            " status = 'active', status_reason = null, updated_at = now() where id = %s",
            (sealed.ciphertext, sealed.nonce, sealed.wrapped_data_key,
             sealed.master_key_version, refreshed_at, connection_id),
        )

    def rewrap_token(self, conn, connection_id: str, sealed: Sealed) -> None:
        """After a master-key rotation: only the wrapped data key and its version change."""
        conn.execute(
            "update connections set wrapped_data_key = %s, master_key_version = %s,"
            " updated_at = now() where id = %s",
            (sealed.wrapped_data_key, sealed.master_key_version, connection_id),
        )

    def set_status(self, conn, connection_id: str, status: str,
                   reason: Optional[str] = None) -> None:
        conn.execute(
            "update connections set status = %s, status_reason = %s, updated_at = now()"
            " where id = %s",
            (status, reason, connection_id),
        )

    def touch(self, conn, connection_id: str, used_at: datetime) -> None:
        conn.execute("update connections set last_used_at = %s where id = %s",
                     (used_at, connection_id))

    def delete(self, conn, connection_id: str) -> None:
        conn.execute("delete from connections where id = %s", (connection_id,))

    def mark_refreshed(self, conn, connection_id: str, refreshed_at: datetime) -> None:
        """A refresh that rotated nothing: the token stays, the timestamp moves."""
        conn.execute(
            "update connections set last_refreshed_at = %s, last_used_at = %s, status = 'active',"
            " status_reason = null, updated_at = now() where id = %s",
            (refreshed_at, refreshed_at, connection_id),
        )

    def lock(self, conn, connection_id: str) -> None:
        """Block until this transaction holds the connection's advisory lock.

        The refresh path takes it, re-reads the row, and only then decides whether
        a refresh is still needed: a worker that waited here finds the winner's
        new token in the row and uses that instead of asking Salesforce again.
        """
        conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (connection_id,))

    def try_lock(self, conn, connection_id: str) -> bool:
        """A transaction-scoped advisory lock on one connection, for single-flight refresh.

        Two workers refreshing the same token at once would, with rotation on,
        invalidate each other's result. The loser sees False and re-reads the row
        the winner wrote.
        """
        return conn.execute(
            "select pg_try_advisory_xact_lock(hashtext(%s)) as locked", (connection_id,)
        ).fetchone()["locked"]

    # ----------------------------------------------------------------- OAuth state

    def create_state(self, conn, *, state: str, code_verifier: str, login_host: str,
                     redirect_uri: str, tenant_id: str = DEFAULT_TENANT,
                     expected_org_id: Optional[str] = None,
                     ttl_seconds: int = STATE_TTL_SECONDS) -> None:
        conn.execute(
            "insert into oauth_states (state, tenant_id, provider, code_verifier, login_host,"
            " redirect_uri, expected_org_id, expires_at)"
            " values (%s, %s, %s, %s, %s, %s, %s, now() + make_interval(secs => %s))",
            (state, tenant_id, PROVIDER, code_verifier, login_host, redirect_uri,
             expected_org_id, ttl_seconds),
        )

    def consume_state(self, conn, state: str) -> Optional[dict[str, Any]]:
        """The state's row, exactly once, while it is still valid; else None.

        One statement, so two callbacks racing on the same state cannot both win:
        the update's own predicate is the check. Only a Salesforce state: a
        sign-in state (ADR-0013) presented here is not consumed, so this flow
        never sends its client secret to the host a sign-in state names.
        """
        row = conn.execute(
            "update oauth_states set consumed_at = now()"
            " where state = %s and provider = %s and consumed_at is null and expires_at > now()"
            " returning state, tenant_id, code_verifier, login_host, redirect_uri,"
            " expected_org_id, created_at, expires_at",
            (state, PROVIDER),
        ).fetchone()
        return dict(row) if row else None

    def purge_states(self, conn, older_than_seconds: int = 3600) -> int:
        """Housekeeping: consumed or expired states are useless after an hour."""
        cur = conn.execute(
            "delete from oauth_states where expires_at < now() - make_interval(secs => %s)",
            (older_than_seconds,),
        )
        return cur.rowcount
