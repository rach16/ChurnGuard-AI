"""
Keep tokens, codes and secrets out of the logs (ADR-0012, section 4, "never logged").

The dangerous line is not the one a developer writes. It is uvicorn's access log,
which prints the request path with its query string, and the OAuth callback
arrives as ``/auth/salesforce?code=aPrx...&state=...``. One such line in Render's
log viewer is an authorisation code sitting in a third party's storage. So the
filter is attached to every handler the process has, uvicorn's included, and
``find_secret`` is the same check the audit log applies before writing a row.

Patterns are deliberately shaped like the real values (Salesforce access tokens
start with the org id and a ``!``, refresh tokens with ``5Aep``) so ordinary log
text is left alone. A keyed form (``code=…``, ``client_secret: …``, ``"code": "…"`` in JSON) is
redacted whatever the value looks like.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

# (name, pattern, replacement). Order matters only in that the keyed forms keep
# their key, which makes a redacted line still readable.
PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("salesforce_access_token",
     re.compile(r"00D[A-Za-z0-9]{12,15}![A-Za-z0-9_.]{16,}"), "[REDACTED access_token]"),
    ("salesforce_refresh_token",
     re.compile(r"5Aep[A-Za-z0-9_.]{16,}"), "[REDACTED refresh_token]"),
    ("aws_access_key_id",
     re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])"), "[REDACTED aws_key]"),
    ("bearer",
     re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-!]{16,}"), "Bearer [REDACTED]"),
    # ChurnGuard's own access tokens (ADR-0016 §10a). The page's display prefix
    # (cg_pat_ and four characters) is too short to match.
    ("churnguard_access_token",
     re.compile(r"cg_pat_[A-Za-z0-9_\-]{16,}"), "[REDACTED access token]"),
    # code= is the OAuth authorisation code; a short value (status code=200) is
    # not one and is left alone.
    ("oauth_code",
     re.compile(r"(?i)\b(code)(['\"]?\s*[=:]\s*['\"]?)[A-Za-z0-9._%\-!]{16,}"), r"\1\2[REDACTED]"),
    ("keyed_secret",
     re.compile(r"(?i)\b(client_secret|refresh_token|access_token|code_verifier|password|"
                r"aws_secret_access_key|secret_access_key|api_key|x-api-key|token)"
                r"(['\"]?\s*[=:]\s*['\"]?)[^\s'\"&,;]{8,}"), r"\1\2[REDACTED]"),
    ("url_password",
     re.compile(r"(?i)([a-z]+://[^:/@\s]+:)[^@\s]+@"), r"\1***@"),
]


def redact(text: str) -> str:
    for _, pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def find_secret(text: str) -> Optional[str]:
    """The name of the first secret-shaped thing in the text, or None. Never the value."""
    for name, pattern, _ in PATTERNS:
        if pattern.search(text):
            return name
    return None


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return redact(str(value))


class SecretRedactingFilter(logging.Filter):
    """Rewrites a record's message and arguments in place. Attached to handlers,
    so it sees every logger's output, not only records sent to the logger it was
    added to.

    The arguments are cleaned one by one rather than the message being rendered
    and the arguments dropped, because uvicorn's access formatter reads
    ``record.args`` by position (client, method, path, version, status) to build
    its line. Collapsing them broke that formatter on the very request this
    filter exists for, the callback, and logging printed the record raw as part
    of its own error report. Found on the first local run.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if isinstance(record.args, dict):
                record.args = {k: _clean(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_clean(a) for a in record.args)
            elif record.args is not None:
                record.args = _clean(record.args)
            # A value that is only a secret in context ("code=%s" with the code as
            # the argument) survives the per-argument pass. Render the line and,
            # only if something is still there, collapse it: formatters that read
            # args by position lose that record, but nothing leaks.
            rendered = record.getMessage()
            if find_secret(rendered):
                record.msg = redact(rendered)
                record.args = ()
        except Exception:
            pass
        return True


_FILTER = SecretRedactingFilter()


def install() -> int:
    """Attach the filter to every handler the process has. Returns how many.

    Called at startup, after uvicorn has configured its own loggers, because a
    handler created later is not covered: it is only handlers that filter every
    record passing through them.
    """
    handlers: set[logging.Handler] = set(logging.getLogger().handlers)
    for name in list(logging.root.manager.loggerDict):
        logger = logging.getLogger(name)
        handlers.update(logger.handlers)
    for handler in handlers:
        if _FILTER not in handler.filters:
            handler.addFilter(_FILTER)
    return len(handlers)
