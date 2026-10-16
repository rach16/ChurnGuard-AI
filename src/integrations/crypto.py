"""
Token custody: envelope encryption behind a KeyProvider (ADR-0012, section 4).

A refresh token is a standing credential to a customer's CRM, so it is never stored
as itself. Each connection gets its own random 256-bit data key; the token is
sealed with AES-256-GCM under that key, with the connection id as associated data,
so a ciphertext copied onto another connection fails to open. The data key is then
wrapped by a master key that the database never holds.

The master key lives behind ``KeyProvider``:

- ``KmsKeyProvider``    AWS KMS. The key never leaves KMS: ``GenerateDataKey`` hands
                        back a plaintext data key and its wrapped form, ``Decrypt``
                        unwraps, ``ReEncrypt`` re-wraps under the current key version
                        without exposing the data key at all. Every call is logged
                        by CloudTrail and bound to an encryption context.
- ``LocalKeyProvider``  A 32-byte key from the environment, the $0 stand-in. Same
                        interface, weaker custody: the key sits in the app's
                        environment, so a process-level breach reaches it.

Neither provider is told what it is wrapping. The plaintext token exists only in
the caller's frame, for the duration of one call. Python cannot promise to zero
memory, so this module does not claim to; what it promises is that the token is
never written, logged or returned anywhere but to the sync that needs it.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

APP = "churnguard"
PURPOSE = "salesforce_refresh_token"
NONCE_BYTES = 12
DATA_KEY_BYTES = 32


class CustodyError(Exception):
    """Base for every failure to seal, open or re-wrap."""


class DecryptionFailed(CustodyError):
    """Wrong key, wrong connection, or tampered bytes. Deliberately says no more."""


class KeyVersionUnknown(CustodyError):
    """The data key was wrapped by a master key version this provider does not hold."""


def encryption_context(connection_id: str) -> dict[str, str]:
    """Binds a wrapped data key to one connection and one purpose.

    Sent to KMS as the EncryptionContext, so a wrapped key presented with any other
    context is refused by KMS itself; used as associated data by the local provider
    for the same effect.
    """
    return {"app": APP, "purpose": PURPOSE, "connection_id": str(connection_id)}


def _aad(context: Mapping[str, str]) -> bytes:
    return json.dumps(dict(sorted(context.items())), separators=(",", ":")).encode()


class KeyProvider(Protocol):
    name: str
    version: str

    def generate_data_key(self, context: Mapping[str, str]) -> tuple[bytes, bytes, str]:
        """(plaintext data key, wrapped data key, master key version)."""

    def unwrap_data_key(self, wrapped: bytes, context: Mapping[str, str], version: str) -> bytes: ...

    def rewrap_data_key(self, wrapped: bytes, context: Mapping[str, str],
                        version: str) -> tuple[bytes, str]:
        """(wrapped under the current master key, its version). Never exposes the key."""

    def check(self) -> dict[str, Any]:
        """Prove the master key is reachable and usable at startup, not on first connect."""


class KmsKeyProvider:
    name = "kms"

    def __init__(self, key_id: str, client: Any = None, region: Optional[str] = None):
        if client is None:
            import boto3

            client = boto3.client("kms", region_name=region or os.getenv("AWS_REGION"))
        self.client = client
        self.key_id = key_id
        # Resolved by check(): the key ARN, which is what gets stored as the version
        # so a row says exactly which key wrapped it.
        self.version = key_id

    def generate_data_key(self, context: Mapping[str, str]) -> tuple[bytes, bytes, str]:
        r = self.client.generate_data_key(KeyId=self.key_id, KeySpec="AES_256",
                                          EncryptionContext=dict(context))
        return r["Plaintext"], r["CiphertextBlob"], r["KeyId"]

    def unwrap_data_key(self, wrapped: bytes, context: Mapping[str, str], version: str) -> bytes:
        try:
            # KeyId is optional for a symmetric key but passed on purpose: a
            # ciphertext from any other key is refused instead of quietly decrypted.
            r = self.client.decrypt(CiphertextBlob=wrapped, EncryptionContext=dict(context),
                                    KeyId=version)
        except Exception as e:  # botocore raises generated classes; name only
            raise DecryptionFailed(type(e).__name__) from None
        return r["Plaintext"]

    def rewrap_data_key(self, wrapped: bytes, context: Mapping[str, str],
                        version: str) -> tuple[bytes, str]:
        try:
            r = self.client.re_encrypt(CiphertextBlob=wrapped,
                                       SourceEncryptionContext=dict(context),
                                       SourceKeyId=version,
                                       DestinationKeyId=self.key_id,
                                       DestinationEncryptionContext=dict(context))
        except Exception as e:
            raise DecryptionFailed(type(e).__name__) from None
        return r["CiphertextBlob"], r["KeyId"]

    def check(self) -> dict[str, Any]:
        meta = self.client.describe_key(KeyId=self.key_id)["KeyMetadata"]
        if meta.get("KeyState") != "Enabled":
            raise CustodyError(f"KMS key {self.key_id} is {meta.get('KeyState')}, not Enabled")
        self.version = meta["Arn"]
        return {"provider": self.name, "key": meta["Arn"], "state": meta["KeyState"],
                "spec": meta.get("KeySpec")}


class LocalKeyProvider:
    """Master keys from the environment, by version, the newest current.

    Holding several versions is what makes rotation possible without downtime: seal
    under the current one, still open rows wrapped by an older one, re-wrap them at
    leisure, then drop the old key.
    """

    name = "local"

    def __init__(self, keys: Mapping[str, bytes], current: str):
        for version, key in keys.items():
            if len(key) != DATA_KEY_BYTES:
                raise CustodyError(f"master key {version} must be {DATA_KEY_BYTES} bytes")
        if current not in keys:
            raise CustodyError(f"current master key version {current!r} is not among {sorted(keys)}")
        self.keys = dict(keys)
        self.version = current

    @classmethod
    def from_env(cls) -> "LocalKeyProvider":
        """``CHURNGUARD_MASTER_KEY``: ``<version>:<base64 32 bytes>``, comma-separated,
        oldest first; the last one is current. A bare base64 value is version ``v1``."""
        raw = os.environ["CHURNGUARD_MASTER_KEY"]
        keys: dict[str, bytes] = {}
        for i, item in enumerate(s.strip() for s in raw.split(",") if s.strip()):
            version, sep, b64 = item.partition(":")
            if not sep:
                version, b64 = f"v{i + 1}", item
            keys[version] = base64.b64decode(b64)
        if not keys:
            raise CustodyError("CHURNGUARD_MASTER_KEY is empty")
        return cls(keys, current=list(keys)[-1])

    def _wrap(self, data_key: bytes, context: Mapping[str, str]) -> bytes:
        nonce = secrets.token_bytes(NONCE_BYTES)
        return nonce + AESGCM(self.keys[self.version]).encrypt(nonce, data_key, _aad(context))

    def generate_data_key(self, context: Mapping[str, str]) -> tuple[bytes, bytes, str]:
        data_key = secrets.token_bytes(DATA_KEY_BYTES)
        return data_key, self._wrap(data_key, context), self.version

    def unwrap_data_key(self, wrapped: bytes, context: Mapping[str, str], version: str) -> bytes:
        if version not in self.keys:
            raise KeyVersionUnknown(version)
        try:
            return AESGCM(self.keys[version]).decrypt(
                wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], _aad(context))
        except InvalidTag:
            raise DecryptionFailed("wrapped data key") from None

    def rewrap_data_key(self, wrapped: bytes, context: Mapping[str, str],
                        version: str) -> tuple[bytes, str]:
        return self._wrap(self.unwrap_data_key(wrapped, context, version), context), self.version

    def check(self) -> dict[str, Any]:
        return {"provider": self.name, "versions": sorted(self.keys), "current": self.version}


def provider_from_env() -> Optional[KeyProvider]:
    """KMS when ``CHURNGUARD_KMS_KEY_ID`` is set, else the local key, else None."""
    key_id = os.getenv("CHURNGUARD_KMS_KEY_ID", "").strip()
    if key_id:
        return KmsKeyProvider(key_id)
    if os.getenv("CHURNGUARD_MASTER_KEY", "").strip():
        return LocalKeyProvider.from_env()
    return None


@dataclass(frozen=True)
class Sealed:
    """What the database stores for one secret. None of it is useful alone."""

    ciphertext: bytes
    nonce: bytes
    wrapped_data_key: bytes
    master_key_version: str


def seal(provider: KeyProvider, connection_id: str, plaintext: str) -> Sealed:
    context = encryption_context(connection_id)
    data_key, wrapped, version = provider.generate_data_key(context)
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(data_key).encrypt(nonce, plaintext.encode("utf-8"), _aad(context))
    return Sealed(ciphertext, nonce, wrapped, version)


def unseal(provider: KeyProvider, connection_id: str, sealed: Sealed) -> str:
    context = encryption_context(connection_id)
    data_key = provider.unwrap_data_key(sealed.wrapped_data_key, context, sealed.master_key_version)
    try:
        return AESGCM(data_key).decrypt(sealed.nonce, sealed.ciphertext, _aad(context)).decode("utf-8")
    except InvalidTag:
        raise DecryptionFailed("token") from None


def rewrap(provider: KeyProvider, connection_id: str, sealed: Sealed) -> Sealed:
    """The same token under the provider's current master key. The ciphertext is untouched."""
    context = encryption_context(connection_id)
    wrapped, version = provider.rewrap_data_key(sealed.wrapped_data_key, context,
                                                sealed.master_key_version)
    return Sealed(sealed.ciphertext, sealed.nonce, wrapped, version)


def new_local_master_key() -> str:
    """A fresh master key, base64, for ``CHURNGUARD_MASTER_KEY``. Printed once, never logged."""
    return base64.b64encode(secrets.token_bytes(DATA_KEY_BYTES)).decode("ascii")
