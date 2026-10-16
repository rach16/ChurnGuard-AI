"""
Re-wrap every stored token's data key under the current master key (ADR-0012 §4).

What this is for: master-key rotation. Each connection's refresh token is sealed
under its own data key, and that data key is wrapped by the master key in force
at the time. After the master key changes -- a new KMS key, a new local key
version -- rows wrapped by the old one still open as long as the old key is
readable. This script walks every connection, re-wraps its data key under the
current key, and records an audit row. The token ciphertext is never touched and
the plaintext token is never read: with KMS the re-wrap is one ReEncrypt call,
which hands nothing back but the new wrapped key.

Dry run by default. Nothing is written without --apply.

    uv run python scripts/rewrap_tokens.py            # report what would change
    uv run python scripts/rewrap_tokens.py --apply    # do it

Run with the environment of the deployment whose rows you are rotating
(DATABASE_URL, CHURNGUARD_KMS_KEY_ID or CHURNGUARD_MASTER_KEY, AWS credentials).
For a local key, CHURNGUARD_MASTER_KEY must list the old version and the new,
newest last, until every row reports the new version; then the old can go.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from integrations import audit, crypto  # noqa: E402

logger = logging.getLogger("rewrap")
ACTOR = "operator"


def rewrap_all(store, provider: crypto.KeyProvider, *, apply: bool) -> list[dict]:
    """One line per connection: its id, org, old version, new version, and whether
    it changed. Rows already on the current version are reported and left alone."""
    report = []
    current = provider.version
    with store.db.connection() as conn:
        for c in store.all(conn):
            before = c.sealed.master_key_version
            entry = {"connection_id": c.id, "org_id": c.org_id, "from": before, "to": current,
                     "changed": before != current, "applied": False}
            if before != current and apply:
                sealed = crypto.rewrap(provider, c.id, c.sealed)
                store.rewrap_token(conn, c.id, sealed)
                audit.record(conn, "rewrap", actor=ACTOR, tenant_id=c.tenant_id,
                             connection_id=c.id, org_id=c.org_id,
                             detail={"from": before, "to": sealed.master_key_version})
                entry["to"] = sealed.master_key_version
                entry["applied"] = True
            report.append(entry)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="write the re-wrapped keys")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    import os

    from integrations.connections import ConnectionStore
    from integrations.db import Database, redacted_url

    url = os.getenv("DATABASE_URL", "").strip()
    provider = crypto.provider_from_env()
    if not url or provider is None:
        logger.error("DATABASE_URL and a master key (CHURNGUARD_KMS_KEY_ID or "
                     "CHURNGUARD_MASTER_KEY) are required")
        return 2
    key = provider.check()
    logger.info(f"database {redacted_url(url)}")
    logger.info(f"master key {key}")

    db = Database(url).open()
    try:
        report = rewrap_all(ConnectionStore(db), provider, apply=args.apply)
    finally:
        db.close()

    for e in report:
        state = "rewrapped" if e["applied"] else ("would rewrap" if e["changed"] else "current")
        logger.info(f"{e['connection_id']}  org {e['org_id']}  {e['from'][-12:]} -> {e['to'][-12:]}  {state}")
    pending = sum(1 for e in report if e["changed"] and not e["applied"])
    logger.info(f"{len(report)} connection(s), {sum(e['applied'] for e in report)} rewrapped, "
                f"{pending} pending{' (dry run; add --apply)' if pending else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
