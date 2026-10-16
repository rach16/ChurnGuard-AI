"""
Connected integrations: the state behind "Connect Salesforce" (ADR-0012).

What lives here and nowhere else:

- ``db``          Postgres for connections, OAuth state and the audit log, with
                  forward-only migrations applied at startup.
- ``crypto``      Token custody. A refresh token is sealed under a per-connection
                  data key, which is itself wrapped by a master key the database
                  never holds (AWS KMS, or a local key as the $0 stand-in).
- ``audit``       Append-only events: who connected, refreshed, disconnected, and
                  every refusal. Never a token value.
- ``redaction``   A logging filter that strips tokens, codes and secrets from
                  anything the process logs, including uvicorn's access line.
- ``connections`` The connection row and the single-use OAuth state.

The connector in ``src/connectors/salesforce.py`` reads Salesforce; this package
decides whether it may, and with what.
"""
