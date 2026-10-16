"""
Postgres for the integration layer, and the migrations that shape it.

Why Postgres at all: Render's free disk is wiped every time the service sleeps, so
anything that must outlive a wake-up -- the connection, its sealed token, the audit
trail -- cannot live in a file next to the code. The managed database is Neon's
free tier here and would be RDS for a customer; the code reads ``DATABASE_URL``
and does not care which.

Migrations are plain SQL files, numbered, applied in order inside one transaction
under an advisory lock. Forward-only: a change is a new file. The runner is a page
of code rather than a framework because the schema is three tables and the
failure mode that matters -- two processes migrating at once -- is one lock.
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Any fixed number; it only has to be the same in every process that migrates.
MIGRATION_LOCK_ID = 7_264_001


def database_url() -> Optional[str]:
    url = os.getenv("DATABASE_URL", "").strip()
    return url or None


def configured() -> bool:
    return database_url() is not None


def redacted_url(url: str) -> str:
    """The URL with its password removed, for logs. ``postgresql://u:***@host/db``."""
    return re.sub(r"^([a-z]+://[^:/@\s]+:)[^@\s]+@", r"\1***@", url)


class Database:
    """A small pool over ``DATABASE_URL``, plus the migration runner.

    ``schema`` sets ``search_path`` for every connection the pool opens. Tests use it
    to run the real migrations in a throwaway schema on the real database and drop
    it afterwards, so they exercise Postgres rather than a stand-in and leave nothing
    behind.
    """

    def __init__(self, url: str, *, min_size: int = 0, max_size: int = 4,
                 schema: Optional[str] = None):
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        kwargs: dict = {"row_factory": dict_row}
        if schema:
            if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
                raise ValueError(f"unsafe schema name: {schema!r}")
            kwargs["options"] = f"-c search_path={schema}"
        self.url = url
        self.schema = schema
        self.pool = ConnectionPool(url, min_size=min_size, max_size=max_size,
                                   kwargs=kwargs, open=False)

    def open(self) -> "Database":
        self.pool.open()
        return self

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def connection(self) -> Iterator:
        """A pooled connection. Commits on clean exit, rolls back on an exception."""
        with self.pool.connection() as conn:
            yield conn

    def ping(self) -> bool:
        with self.connection() as conn:
            return conn.execute("select 1 as ok").fetchone()["ok"] == 1

    def migrate(self) -> list[str]:
        """Apply every migration not yet recorded. Returns the names applied.

        All pending files run in one transaction, so a failure part-way leaves the
        database exactly as it was and the next start retries from the same point.
        The advisory lock is transaction-scoped and released with it.
        """
        files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
        applied: list[str] = []
        with self.connection() as conn:
            with conn.transaction():
                conn.execute("select pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
                conn.execute(
                    "create table if not exists schema_migrations ("
                    " version text primary key,"
                    " name text not null,"
                    " applied_at timestamptz not null default now())"
                )
                done = {r["version"] for r in conn.execute("select version from schema_migrations")}
                for path in files:
                    version, _, name = path.stem.partition("_")
                    if version in done:
                        continue
                    conn.execute(path.read_text())
                    conn.execute("insert into schema_migrations (version, name) values (%s, %s)",
                                 (version, name))
                    applied.append(path.stem)
        if applied:
            logger.info(f"✓ migrations applied: {', '.join(applied)}")
        return applied

    def applied_migrations(self) -> list[str]:
        with self.connection() as conn:
            rows = conn.execute(
                "select version, name from schema_migrations order by version").fetchall()
        return [f"{r['version']}_{r['name']}" for r in rows]
