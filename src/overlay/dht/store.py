from __future__ import annotations
import time
from overlay.dht.record import NodeRecord, RecordError

class RecordConflict(Exception):
    pass


class RecordStore:
    def __init__(
        self,
        *,
        max_record_size: int = 4096,
        default_ttl: int = 180,
    ) -> None:
        self._records: dict[bytes, NodeRecord] = {}
        self._max_size = max_record_size
        self._default_ttl = default_ttl

    def __len__(self) -> int:
        return len(self._records)

    def get(self, key: bytes, now: float | None = None) -> NodeRecord | None:
        record = self._records.get(key)
        if record is None:
            return None
        if record.is_expired(now):
            del self._records[key]
            return None
        return record

    def put(
        self,
        key: bytes,
        record: NodeRecord,
        now: float | None = None,
    ) -> None:
        try:
            record.validate(max_size=self._max_size, now=now)
        except RecordError:
            raise

        existing = self._records.get(key)
        if existing is not None and not existing.is_expired(now):
            if existing.node_id != record.node_id:
                raise RecordConflict(
                    "Запись с этим ключом принадлежит другому node_id"
                )
            if record.sequence_number < existing.sequence_number:
                raise RecordConflict(
                    f"Откат версии: {record.sequence_number} < "
                    f"{existing.sequence_number}"
                )
            if record.sequence_number == existing.sequence_number:
                if record.signature == existing.signature:
                    return
                raise RecordConflict(
                    "Конфликт версий с одинаковым sequence_number"
                )
        self._records[key] = record

    def remove(self, key: bytes) -> bool:
        return self._records.pop(key, None) is not None

    def cleanup(self, now: float | None = None) -> int:
        expired_keys = [
            k for k, r in self._records.items() if r.is_expired(now)
        ]
        for k in expired_keys:
            del self._records[k]
        return len(expired_keys)

    def keys(self) -> list[bytes]:
        return list(self._records.keys())

    def items(self) -> list[tuple[bytes, NodeRecord]]:
        return list(self._records.items())

    def snapshot(self, now: float | None = None) -> dict[str, dict]:
        current = int(time.time()) if now is None else int(now)
        result = {}
        for key, record in self._records.items():
            remaining = max(0, record.expires_at - current)
            result[key.hex()] = {
                "node_id": record.node_id.hex(),
                "sequence_number": record.sequence_number,
                "expires_in": remaining,
                "size": len(record.to_json_bytes()),
            }
        return result