"""
A GitHub Actions OIDC issuer for the scheduler tests (2.6.3): its published key
set over an httpx mock transport, and real RS256 tokens carrying the claims a
workflow run gets, so the verifier's checks run exactly as in production.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from integrations.github_oidc import ISSUER, JWKS_URL, GitHubOidcVerifier, Pins

REPO = "rach16/ChurnGuard-AI"


class FakeGitHub:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwks_fetches = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == JWKS_URL:
            self.jwks_fetches += 1
            public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
            public.update({"kid": "gh1", "alg": "RS256", "use": "sig"})
            return httpx.Response(200, json={"keys": [public]})
        return httpx.Response(404)

    def verifier(self, audience: str) -> GitHubOidcVerifier:
        return GitHubOidcVerifier(Pins(audience=audience),
                                  http=httpx.Client(transport=httpx.MockTransport(self.handler)))

    def token(self, audience: str, *, signed_by_other: bool = False, kid: str = "gh1",
              **overrides: Any) -> str:
        """A token as GitHub mints it for a scheduled run of sync.yml on main."""
        now = int(time.time())
        claims = {
            "iss": ISSUER, "aud": audience, "iat": now, "nbf": now, "exp": now + 300,
            "sub": f"repo:{REPO}:ref:refs/heads/main",
            "repository": REPO, "repository_id": "1093215607",
            "repository_owner": "rach16", "repository_owner_id": "7829431",
            "ref": "refs/heads/main", "ref_type": "branch",
            "workflow": "Daily sync",
            "workflow_ref": f"{REPO}/.github/workflows/sync.yml@refs/heads/main",
            "job_workflow_ref": f"{REPO}/.github/workflows/sync.yml@refs/heads/main",
            "event_name": "schedule", "run_id": "18200000001", "actor": "rach16",
        }
        claims.update(overrides)
        return jwt.encode(claims, self.other if signed_by_other else self.key,
                          algorithm="RS256", headers={"kid": kid})
