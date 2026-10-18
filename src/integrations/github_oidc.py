"""
The scheduler's identity: GitHub Actions' OIDC token (2.6.3, ADR-0013 section 5).

The daily sync workflow is a machine and cannot sign in. Instead of a shared key
in a GitHub secret, each run asks GitHub for a short-lived token, signed by
GitHub, saying which repository, workflow, branch and event it is; the backend
verifies the signature against GitHub's published keys and every one of those
claims. Nothing long-lived exists to leak: a token expires minutes after it is
issued and opens one route, ``POST /connections/sync-due``.

Accepted only when all hold: RS256, signed by a key in GitHub's set; issuer is
GitHub Actions; audience is this deployment's site origin (so a token minted
for staging does not work on production, and the reverse); repository name,
repository id and owner id are this repository's (a rename, a fork, or a new
repository that takes the old name all fail the id checks); the workflow is
``.github/workflows/sync.yml`` on ``refs/heads/main``; the event is a schedule
or a manual dispatch. Each check refused is named; the token is never logged.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = f"{ISSUER}/.well-known/jwks"
JWKS_TTL_SECONDS = 3600
ALGORITHMS = ["RS256"]
EVENTS = frozenset({"schedule", "workflow_dispatch"})

# This repository. Overridable by environment for a deployment of a fork, which
# must then pin its own; the defaults are what this code is deployed from.
REPOSITORY = "rach16/ChurnGuard-AI"
REPOSITORY_ID = "1093215607"
OWNER_ID = "7829431"
WORKFLOW = ".github/workflows/sync.yml"
REF = "refs/heads/main"


class SchedulerRefused(Exception):
    def __init__(self, check: str):
        super().__init__(f"scheduler token refused: {check}")
        self.check = check


@dataclass(frozen=True)
class SchedulerRun:
    """Who called: the workflow and its run, for the audit."""
    workflow: str
    run_id: Optional[str]
    event: str

    @property
    def actor(self) -> str:
        return f"service:github-actions/{self.workflow.rsplit('/', 1)[-1]}"


@dataclass(frozen=True)
class Pins:
    audience: str
    repository: str = REPOSITORY
    repository_id: str = REPOSITORY_ID
    owner_id: str = OWNER_ID
    workflow: str = WORKFLOW
    ref: str = REF

    @property
    def workflow_ref(self) -> str:
        return f"{self.repository}/{self.workflow}@{self.ref}"

    @classmethod
    def from_env(cls) -> Optional["Pins"]:
        audience = os.getenv("PUBLIC_SITE_ORIGIN", "").strip().rstrip("/")
        if not audience:
            return None
        return cls(audience=audience,
                   repository=os.getenv("SCHEDULER_REPOSITORY", REPOSITORY),
                   repository_id=os.getenv("SCHEDULER_REPOSITORY_ID", REPOSITORY_ID),
                   owner_id=os.getenv("SCHEDULER_OWNER_ID", OWNER_ID),
                   workflow=os.getenv("SCHEDULER_WORKFLOW", WORKFLOW),
                   ref=os.getenv("SCHEDULER_REF", REF))


class GitHubOidcVerifier:
    def __init__(self, pins: Pins, http: Optional[httpx.Client] = None, clock=time.time):
        self.pins = pins
        self.http = http or httpx.Client(timeout=10.0)
        self.clock = clock
        self._keys: dict[str, Any] = {}
        self._keys_at = 0.0
        self._lock = threading.Lock()

    def _jwks(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            if force or not self._keys or self.clock() - self._keys_at > JWKS_TTL_SECONDS:
                r = self.http.get(JWKS_URL)
                r.raise_for_status()
                self._keys = {k["kid"]: k for k in r.json().get("keys", []) if "kid" in k}
                self._keys_at = self.clock()
            return self._keys

    def verify(self, token: str) -> SchedulerRun:
        import jwt

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise SchedulerRefused("malformed") from None
        if header.get("alg") not in ALGORITHMS:
            raise SchedulerRefused("algorithm")
        kid = header.get("kid")
        keys = self._jwks()
        if kid not in keys:
            keys = self._jwks(force=True)
            if kid not in keys:
                raise SchedulerRefused("unknown signing key")
        try:
            claims = jwt.decode(token, jwt.PyJWK(keys[kid]).key, algorithms=ALGORITHMS,
                                audience=self.pins.audience, issuer=ISSUER, leeway=30,
                                options={"require": ["exp", "iat", "iss", "aud", "sub"]})
        except jwt.ExpiredSignatureError:
            raise SchedulerRefused("expired") from None
        except jwt.InvalidAudienceError:
            raise SchedulerRefused("audience") from None
        except jwt.InvalidIssuerError:
            raise SchedulerRefused("issuer") from None
        except jwt.PyJWTError:
            raise SchedulerRefused("signature or claims") from None

        p = self.pins
        for claim, expected in (("repository", p.repository), ("repository_id", p.repository_id),
                                ("repository_owner_id", p.owner_id), ("ref", p.ref),
                                ("workflow_ref", p.workflow_ref)):
            if str(claims.get(claim, "")) != expected:
                raise SchedulerRefused(claim)
        if claims.get("event_name") not in EVENTS:
            raise SchedulerRefused("event_name")
        return SchedulerRun(workflow=p.workflow, run_id=str(claims.get("run_id") or "") or None,
                            event=claims["event_name"])
