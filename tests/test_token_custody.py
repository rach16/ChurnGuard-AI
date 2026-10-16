"""
Token custody and redaction (ADR-0012, section 4). No network, no database.

What these pin: a sealed token opens only for the connection it was sealed to and
only with a master key that wrapped it; a tampered byte anywhere fails closed;
rotation re-wraps without touching the token; KMS is driven through a fake that
enforces the encryption context the way the real service does; and nothing
token-shaped survives the log filter or reaches an audit row.
"""

from __future__ import annotations

import base64
import logging
import secrets
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from integrations import audit, crypto, redaction  # noqa: E402
from integrations.crypto import (  # noqa: E402
    DecryptionFailed, KeyVersionUnknown, KmsKeyProvider, LocalKeyProvider, rewrap, seal, unseal,
)

CID = "6b1a3c0e-9d2f-4e8a-b0c7-1f2e3d4c5b6a"
TOKEN = "5Aep861" + "x" * 60     # the shape of a real refresh token, not one


def local(*versions: str) -> LocalKeyProvider:
    keys = {v: secrets.token_bytes(32) for v in versions}
    return LocalKeyProvider(keys, current=versions[-1])


# ------------------------------------------------------------------ envelope

def test_round_trip_and_stored_parts_reveal_nothing():
    p = local("v1")
    sealed = seal(p, CID, TOKEN)
    assert unseal(p, CID, sealed) == TOKEN
    assert TOKEN.encode() not in sealed.ciphertext + sealed.wrapped_data_key
    assert sealed.master_key_version == "v1"


def test_sealed_to_one_connection_opens_for_no_other():
    p = local("v1")
    sealed = seal(p, CID, TOKEN)
    with pytest.raises(DecryptionFailed):
        unseal(p, "another-connection", sealed)


@pytest.mark.parametrize("field", ["ciphertext", "nonce", "wrapped_data_key"])
def test_any_tampered_byte_fails_closed(field):
    p = local("v1")
    sealed = seal(p, CID, TOKEN)
    value = bytearray(getattr(sealed, field))
    value[-1] ^= 0x01
    with pytest.raises(DecryptionFailed):
        unseal(p, CID, replace(sealed, **{field: bytes(value)}))


def test_a_different_master_key_cannot_open_it():
    sealed = seal(local("v1"), CID, TOKEN)
    with pytest.raises(DecryptionFailed):
        unseal(local("v1"), CID, sealed)      # same version name, different key bytes


def test_rotation_rewraps_the_data_key_and_leaves_the_token_alone():
    k1 = secrets.token_bytes(32)
    old = LocalKeyProvider({"v1": k1}, current="v1")
    sealed = seal(old, CID, TOKEN)

    both = LocalKeyProvider({"v1": k1, "v2": secrets.token_bytes(32)}, current="v2")
    rewrapped = rewrap(both, CID, sealed)
    assert rewrapped.master_key_version == "v2"
    assert rewrapped.ciphertext == sealed.ciphertext and rewrapped.nonce == sealed.nonce
    assert rewrapped.wrapped_data_key != sealed.wrapped_data_key
    assert unseal(both, CID, rewrapped) == TOKEN

    # Once v1 is retired, rows still wrapped by it are refused by name, not by a
    # generic failure, so an incomplete rewrap is diagnosable.
    only_new = LocalKeyProvider({"v2": both.keys["v2"]}, current="v2")
    assert unseal(only_new, CID, rewrapped) == TOKEN
    with pytest.raises(KeyVersionUnknown):
        unseal(only_new, CID, sealed)


def test_local_provider_from_env_parses_versions_oldest_first(monkeypatch):
    k1, k2 = (base64.b64encode(secrets.token_bytes(32)).decode() for _ in range(2))
    monkeypatch.setenv("CHURNGUARD_MASTER_KEY", f"v1:{k1}, v2:{k2}")
    p = LocalKeyProvider.from_env()
    assert sorted(p.keys) == ["v1", "v2"] and p.version == "v2"

    monkeypatch.setenv("CHURNGUARD_MASTER_KEY", k1)       # bare value: version v1
    assert LocalKeyProvider.from_env().version == "v1"

    monkeypatch.setenv("CHURNGUARD_MASTER_KEY", "v1:" + base64.b64encode(b"short").decode())
    with pytest.raises(crypto.CustodyError):
        LocalKeyProvider.from_env()


def test_provider_choice_prefers_kms(monkeypatch):
    monkeypatch.delenv("CHURNGUARD_KMS_KEY_ID", raising=False)
    monkeypatch.delenv("CHURNGUARD_MASTER_KEY", raising=False)
    assert crypto.provider_from_env() is None
    monkeypatch.setenv("CHURNGUARD_MASTER_KEY", crypto.new_local_master_key())
    assert crypto.provider_from_env().name == "local"
    monkeypatch.setenv("CHURNGUARD_KMS_KEY_ID", "alias/x")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    assert crypto.provider_from_env().name == "kms"


# ----------------------------------------------------------------------- KMS

class FakeKms:
    """Enough of the KMS API to prove how it is called: the context is enforced on
    every operation, Decrypt is refused for a foreign key, and ReEncrypt never hands
    the plaintext back."""

    ARN = "arn:aws:kms:us-west-2:000000000000:key/11111111-2222-3333-4444-555555555555"

    def __init__(self, state="Enabled"):
        self.state = state
        self.calls: list[str] = []
        self._keys: dict[bytes, tuple[bytes, dict]] = {}

    def describe_key(self, KeyId):
        self.calls.append("describe_key")
        return {"KeyMetadata": {"Arn": self.ARN, "KeyState": self.state, "KeySpec": "SYMMETRIC_DEFAULT"}}

    def generate_data_key(self, KeyId, KeySpec, EncryptionContext):
        self.calls.append("generate_data_key")
        plaintext, blob = secrets.token_bytes(32), secrets.token_bytes(48)
        self._keys[blob] = (plaintext, dict(EncryptionContext))
        return {"Plaintext": plaintext, "CiphertextBlob": blob, "KeyId": self.ARN}

    def decrypt(self, CiphertextBlob, EncryptionContext, KeyId):
        self.calls.append("decrypt")
        if KeyId != self.ARN or CiphertextBlob not in self._keys:
            raise RuntimeError("IncorrectKeyException")
        plaintext, context = self._keys[CiphertextBlob]
        if context != dict(EncryptionContext):
            raise RuntimeError("InvalidCiphertextException")
        return {"Plaintext": plaintext, "KeyId": self.ARN}

    def re_encrypt(self, CiphertextBlob, SourceEncryptionContext, SourceKeyId,
                   DestinationKeyId, DestinationEncryptionContext):
        self.calls.append("re_encrypt")
        plaintext = self.decrypt(CiphertextBlob, SourceEncryptionContext, SourceKeyId)["Plaintext"]
        blob = secrets.token_bytes(48)
        self._keys[blob] = (plaintext, dict(DestinationEncryptionContext))
        return {"CiphertextBlob": blob, "KeyId": self.ARN}


def test_kms_provider_binds_every_call_to_the_connection():
    fake = FakeKms()
    p = KmsKeyProvider("alias/churnguard-staging-master", client=fake)
    assert p.check()["key"] == FakeKms.ARN and p.version == FakeKms.ARN

    sealed = seal(p, CID, TOKEN)
    assert sealed.master_key_version == FakeKms.ARN
    assert unseal(p, CID, sealed) == TOKEN
    with pytest.raises(DecryptionFailed):
        unseal(p, "another-connection", sealed)     # KMS refuses the context itself

    rewrapped = rewrap(p, CID, sealed)
    assert rewrapped.ciphertext == sealed.ciphertext
    assert unseal(p, CID, rewrapped) == TOKEN
    assert "re_encrypt" in fake.calls and fake.calls.count("generate_data_key") == 1


def test_kms_provider_refuses_to_start_on_a_disabled_key():
    p = KmsKeyProvider("alias/x", client=FakeKms(state="PendingDeletion"))
    with pytest.raises(crypto.CustodyError, match="PendingDeletion"):
        p.check()


# ------------------------------------------------------------------ redaction

ACCESS = "00Dg700000J62qbEAB!AQEAQ" + "a" * 40
CODE = "aPrxGt" + "b" * 30

@pytest.mark.parametrize("line, must_vanish", [
    (f"token response: refresh_token={TOKEN} access_token={ACCESS}", [TOKEN, ACCESS]),
    (f'GET /auth/salesforce?code={CODE}&state=abc HTTP/1.1', [CODE]),
    (f"Authorization: Bearer {ACCESS}", [ACCESS]),
    ("client_secret=ABCDEF1234567890ABCDEF", ["ABCDEF1234567890ABCDEF"]),
    ("connecting to postgresql://neondb_owner:npg_secretpw@ep-x.neon.tech/neondb", ["npg_secretpw"]),
    ("creds AKIAIOSFODNN7EXAMPLE used", ["AKIAIOSFODNN7EXAMPLE"]),
    (f"{TOKEN}", [TOKEN]),                                     # bare, no key
])
def test_secrets_do_not_survive_redaction(line, must_vanish):
    out = redaction.redact(line)
    for value in must_vanish:
        assert value not in out, out
    assert redaction.find_secret(line) is not None


def test_ordinary_lines_are_left_alone():
    for line in ["status code=200 in 12ms", "294 accounts read", "GET /health 200",
                 "token budget 1024", "password policy: 12 chars"]:
        assert redaction.redact(line) == line
        assert redaction.find_secret(line) is None


def test_the_filter_rewrites_the_record_in_place():
    record = logging.LogRecord("x", logging.INFO, "f", 1, "exchanged code=%s for %s",
                               (CODE, TOKEN), None)
    assert redaction.SecretRedactingFilter().filter(record) is True
    message = record.getMessage()
    assert CODE not in message and TOKEN not in message
    assert "[REDACTED" in message


def test_uvicorn_style_positional_args_still_format_after_redaction():
    """uvicorn's access line: the path is one positional arg among five, and the
    formatter reads them by position. Redaction must clean the path, not drop
    the tuple."""
    record = logging.LogRecord("uvicorn.access", logging.INFO, "f", 1,
                               '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:1", "GET", f"/integrations/salesforce/callback?code={CODE}&state=s",
                                "1.1", 400), None)
    redaction.SecretRedactingFilter().filter(record)
    assert len(record.args) == 5 and record.args[4] == 400
    line = record.getMessage()
    assert CODE not in line and "code=[REDACTED]&state=s" in line and line.endswith(" 400")


def test_install_covers_every_handler_in_the_process():
    other = logging.getLogger("uvicorn.access.test")
    handler = logging.StreamHandler()
    other.addHandler(handler)
    try:
        assert redaction.install() >= 1
        assert any(isinstance(f, redaction.SecretRedactingFilter) for f in handler.filters)
    finally:
        other.removeHandler(handler)


# ---------------------------------------------------------------------- audit

def test_audit_refuses_a_detail_that_carries_a_secret():
    with pytest.raises(ValueError, match="refresh_token"):
        audit.assert_no_secret({"note": f"got {TOKEN}"})
    with pytest.raises(ValueError, match="oauth_code"):
        audit.assert_no_secret({"callback": {"code": CODE}})
    audit.assert_no_secret({"org_id": "00Dg700000J62qbEAB", "host": "x.develop.my.salesforce.com"})


def test_audit_rejects_an_event_it_does_not_know():
    with pytest.raises(ValueError, match="unknown audit event"):
        audit.record(None, "something_else", actor="test")
