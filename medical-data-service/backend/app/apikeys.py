"""API keys for the MCP endpoint, stored in AWS Secrets Manager.

One secret holds a JSON list of key records. Only the SHA-256 hash of each key
is stored; the plaintext is shown once at creation and never again.

Record shape:
  {id, name, prefix, key_hash, created_at, created_by, revoked_at}

Validation is called on every MCP request, so reads are cached briefly.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

_KEY_PREFIX = "mk_live_"
_CACHE_TTL = 30.0


def _secret_id() -> str:
    return os.getenv("MCP_API_KEYS_SECRET", "medical/dev/mcp-api-keys")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


class ApiKeyStore:
    def __init__(self) -> None:
        self._client = boto3.client(
            "secretsmanager", region_name=os.getenv("AWS_REGION", "us-east-1")
        )
        self._cache: list[dict[str, Any]] | None = None
        self._cache_at = 0.0

    # -- persistence ---------------------------------------------------------

    def _read(self, use_cache: bool = False) -> list[dict[str, Any]]:
        if use_cache and self._cache is not None and time.monotonic() - self._cache_at < _CACHE_TTL:
            return self._cache
        try:
            raw = self._client.get_secret_value(SecretId=_secret_id())["SecretString"]
            records = json.loads(raw)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundError":
                records = []
            else:
                raise
        self._cache = records
        self._cache_at = time.monotonic()
        return records

    def _write(self, records: list[dict[str, Any]]) -> None:
        body = json.dumps(records)
        try:
            self._client.put_secret_value(SecretId=_secret_id(), SecretString=body)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundError":
                self._client.create_secret(Name=_secret_id(), SecretString=body)
            else:
                raise
        self._cache = records
        self._cache_at = time.monotonic()

    # -- operations ----------------------------------------------------------

    @staticmethod
    def _mask(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": record["id"],
            "name": record["name"],
            "masked_key": f"{record['prefix']}…{record['id'][-4:]}",
            "created_at": record["created_at"],
            "created_by": record["created_by"],
            "revoked_at": record.get("revoked_at"),
            "active": record.get("revoked_at") is None,
        }

    def list(self) -> list[dict[str, Any]]:
        return [self._mask(r) for r in self._read()]

    def create(self, name: str, created_by: str) -> dict[str, Any]:
        plaintext = _KEY_PREFIX + secrets.token_hex(24)
        record = {
            "id": secrets.token_hex(8),
            "name": name or "unnamed",
            "prefix": plaintext[: len(_KEY_PREFIX) + 4],
            "key_hash": _hash(plaintext),
            "created_at": _now(),
            "created_by": created_by,
            "revoked_at": None,
        }
        records = self._read()
        records.append(record)
        self._write(records)
        # The plaintext is returned exactly once.
        return {"api_key": plaintext, **self._mask(record)}

    def revoke(self, key_id: str) -> bool:
        records = self._read()
        found = False
        for record in records:
            if record["id"] == key_id and record.get("revoked_at") is None:
                record["revoked_at"] = _now()
                found = True
        if found:
            self._write(records)
        return found

    def resolve(self, plaintext: str) -> dict[str, Any] | None:
        """Return the matching, non-revoked key record, or None.

        The record carries `created_by` (the Cognito email of the key's owner),
        so callers can attribute an MCP request to a real identity.
        """
        if not plaintext:
            return None
        target = _hash(plaintext)
        try:
            records = self._read(use_cache=True)
        except Exception:
            # Fail closed: if the key store is unreachable, deny access.
            return None
        for record in records:
            if record["key_hash"] == target and record.get("revoked_at") is None:
                return record
        return None

    def validate(self, plaintext: str) -> bool:
        return self.resolve(plaintext) is not None


_store: ApiKeyStore | None = None


def api_key_store() -> ApiKeyStore:
    global _store
    if _store is None:
        _store = ApiKeyStore()
    return _store
