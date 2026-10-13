from __future__ import annotations

from overlay.dht.contact import Contact
from overlay.dht.kbucket import AddResult, KBucket
from overlay.dht.xor import bucket_index, xor_distance_int
from overlay.identity.node_id import NODE_ID_LENGTH


class RoutingTable:
    def __init__(self, self_id: bytes, k: int) -> None:
        if len(self_id) != NODE_ID_LENGTH:
            raise ValueError(
                f"self_id должен быть {NODE_ID_LENGTH} байт, "
                f"получено {len(self_id)}"
            )
        if k <= 0:
            raise ValueError("k должен быть > 0")
        self._self_id = self_id
        self._k = k
        self._buckets: dict[int, KBucket] = {}

    @property
    def self_id(self) -> bytes:
        return self._self_id

    @property
    def k(self) -> int:
        return self._k

    def _bucket_for(self, node_id: bytes) -> KBucket:
        idx = bucket_index(self._self_id, node_id)
        bucket = self._buckets.get(idx)
        if bucket is None:
            bucket = KBucket(self._k, idx)
            self._buckets[idx] = bucket
        return bucket

    def add(self, contact: Contact) -> AddResult:
        if contact.node_id == self._self_id:
            raise ValueError("Нельзя добавить себя в таблицу маршрутизации")
        return self._bucket_for(contact.node_id).add_or_update(contact)

    def replace_lru(self, contact: Contact) -> Contact:
        if contact.node_id == self._self_id:
            raise ValueError("Нельзя добавить себя в таблицу маршрутизации")
        return self._bucket_for(contact.node_id).replace_lru(contact)

    def remove(self, node_id: bytes) -> bool:
        if node_id == self._self_id:
            return False
        idx = bucket_index(self._self_id, node_id)
        bucket = self._buckets.get(idx)
        if bucket is None:
            return False
        removed = bucket.remove(node_id)
        if removed and len(bucket) == 0:
            del self._buckets[idx]
        return removed

    def get(self, node_id: bytes) -> Contact | None:
        if node_id == self._self_id:
            return None
        bucket = self._buckets.get(bucket_index(self._self_id, node_id))
        if bucket is None:
            return None
        return bucket.get(node_id)

    def touch(self, node_id: bytes) -> bool:
        if node_id == self._self_id:
            return False
        bucket = self._buckets.get(bucket_index(self._self_id, node_id))
        if bucket is None:
            return False
        return bucket.touch(node_id)

    def closest(self, target: bytes, count: int) -> list[Contact]:
        all_contacts = [c for b in self._buckets.values() for c in b]
        all_contacts.sort(key=lambda c: xor_distance_int(c.node_id, target))
        return all_contacts[:count]

    def bucket_by_index(self, idx: int) -> KBucket | None:
        return self._buckets.get(idx)

    def all_contacts(self) -> list[Contact]:
        return [c for b in self._buckets.values() for c in b]

    def __len__(self) -> int:
        return sum(len(b) for b in self._buckets.values())

    def buckets_info(self) -> list[dict]:
        result = []
        for idx in sorted(self._buckets):
            b = self._buckets[idx]
            result.append(
                {
                    "index": idx,
                    "capacity": b.capacity,
                    "size": len(b),
                    "contacts": [c.node_id_hex for c in b],
                }
            )
        return result