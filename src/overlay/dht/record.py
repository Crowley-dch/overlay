from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from overlay.identity.node_id import (
    NODE_ID_LENGTH,
    compute_node_id_from_bytes,
    verify_node_id,
)
from overlay.identity.keypair import verify_signature


class RecordError(Exception):
    pass


def record_key_for_node(node_id: bytes) -> bytes:
    import hashlib

    if len(node_id) != NODE_ID_LENGTH:
        raise RecordError(f"node_id должен быть {NODE_ID_LENGTH} байт")
    return hashlib.sha256(b"node:" + node_id).digest()


def record_key_for_alias(alias: str) -> bytes:
    import hashlib

    normalized = alias.strip().lower()
    if not normalized:
        raise RecordError("alias пуст после нормализации")
    return hashlib.sha256(b"alias:" + normalized.encode("utf-8")).digest()


@dataclass(slots=True)
class NodeRecord:
    node_id: bytes
    identity_public_key: bytes
    addresses: list[str] = field(default_factory=list)
    sequence_number: int = 1
    issued_at: int = 0
    expires_at: int = 0
    signature: bytes = b""
    alias: str | None = None

    def __post_init__(self) -> None:
        if len(self.node_id) != NODE_ID_LENGTH:
            raise RecordError(
                f"node_id должен быть {NODE_ID_LENGTH} байт, "
                f"получено {len(self.node_id)}"
            )
        if len(self.identity_public_key) != 32:
            raise RecordError("identity_public_key должен быть 32 байта")
        if self.sequence_number <= 0:
            raise RecordError("sequence_number должен быть > 0")
        if not self.addresses:
            raise RecordError("addresses не может быть пустым")
        if self.alias is not None:
            normalized = self.alias.strip().lower()
            if not normalized:
                raise RecordError("alias пуст после нормализации")
            self.alias = normalized
    def _canonical_dict(self, include_signature: bool) -> dict:
        data: dict = {
            "node_id": self.node_id.hex(),
            "identity_public_key": self.identity_public_key.hex(),
            "addresses": list(self.addresses),
            "sequence_number": self.sequence_number,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }
        if self.alias is not None:
            data["alias"] = self.alias
        if include_signature:
            data["signature"] = self.signature.hex()
        return data
    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self._canonical_dict(include_signature=False),
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def to_dict(self) -> dict:
        return self._canonical_dict(include_signature=True)

    def to_json_bytes(self, max_size: int | None = None) -> bytes:
        raw = json.dumps(
            self._canonical_dict(include_signature=True),
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if max_size is not None and len(raw) > max_size:
            raise RecordError(
                f"Размер записи {len(raw)} превышает лимит {max_size}"
            )
        return raw

    @classmethod
    def from_json_bytes(
        cls,
        raw: bytes,
        max_size: int | None = None,
    ) -> "NodeRecord":
        if max_size is not None and len(raw) > max_size:
            raise RecordError(
                f"Размер записи {len(raw)} превышает лимит {max_size}"
            )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RecordError(f"Некорректный JSON: {e}") from e
        if not isinstance(data, dict):
            raise RecordError("Ожидался объект")
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> "NodeRecord":
        try:
            node_id = bytes.fromhex(data["node_id"])
            pubkey = bytes.fromhex(data["identity_public_key"])
            addresses = list(data["addresses"])
            sequence_number = int(data["sequence_number"])
            issued_at = int(data["issued_at"])
            expires_at = int(data["expires_at"])
            signature = bytes.fromhex(data.get("signature", ""))
        except (KeyError, ValueError, TypeError) as e:
            raise RecordError(f"Некорректное поле записи: {e}") from e
        alias = data.get("alias")
        if alias is not None and not isinstance(alias, str):
            raise RecordError("alias должен быть строкой или отсутствовать")
        if not isinstance(addresses, list) or not all(
            isinstance(a, str) and a for a in addresses
        ):
            raise RecordError("addresses должен быть непустым списком строк")
        return cls(
            node_id=node_id,
            identity_public_key=pubkey,
            addresses=addresses,
            sequence_number=sequence_number,
            issued_at=issued_at,
            expires_at=expires_at,
            signature=signature,
            alias=alias,
        )

    def sign(self, private_key) -> None:
        self.signature = private_key.sign(self.canonical_bytes())

    def verify_signature(self) -> bool:
        if not self.signature:
            return False
        return verify_signature(
            self.identity_public_key,
            self.signature,
            self.canonical_bytes(),
        )

    def verify_node_id(self) -> bool:
        return verify_node_id(self.node_id, self.identity_public_key)

    def is_expired(self, now: float | None = None) -> bool:
        current = int(time.time()) if now is None else int(now)
        return current >= self.expires_at

    def is_not_yet_valid(self, now: float | None = None) -> bool:
        current = int(time.time()) if now is None else int(now)
        return current < self.issued_at

    def validate(
        self,
        *,
        max_size: int | None = None,
        now: float | None = None,
    ) -> None:
        if max_size is not None:
            actual = len(self.to_json_bytes())
            if actual > max_size:
                raise RecordError(
                    f"Размер записи {actual} превышает лимит {max_size}"
                )
        if not self.verify_node_id():
            raise RecordError("NodeID не соответствует открытому ключу")
        if not self.verify_signature():
            raise RecordError("Подпись записи недействительна")
        if self.is_expired(now):
            raise RecordError("Срок действия записи истёк")
        if self.is_not_yet_valid(now):
            raise RecordError("Запись ещё не вступила в силу")
        for addr in self.addresses:
            if not isinstance(addr, str) or not addr:
                raise RecordError("Некорректный адрес в addresses")


def build_record(
    node_id: bytes,
    identity_public_key: bytes,
    addresses: list[str],
    sequence_number: int,
    ttl: int,
    private_key,
    alias: str | None = None,
    issued_at: int | None = None,
) -> NodeRecord:
    now = int(time.time()) if issued_at is None else issued_at
    record = NodeRecord(
        node_id=node_id,
        identity_public_key=identity_public_key,
        addresses=addresses,
        sequence_number=sequence_number,
        issued_at=now,
        expires_at=now + ttl,
        alias=alias,
    )
    record.sign(private_key)
    return record