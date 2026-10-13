from __future__ import annotations

import enum

from overlay.dht.contact import Contact


class AddResult(enum.Enum):
    ADDED = "added"
    UPDATED = "updated"
    FULL = "full"


class KBucket:
    def __init__(self, capacity: int, index: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity должен быть > 0")
        self._capacity = capacity
        self._index = index
        self._contacts: list[Contact] = []

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def index(self) -> int:
        return self._index

    def __len__(self) -> int:
        return len(self._contacts)

    def __iter__(self):
        return iter(self._contacts)

    def __contains__(self, node_id: bytes) -> bool:
        return any(c.node_id == node_id for c in self._contacts)

    def is_full(self) -> bool:
        return len(self._contacts) >= self._capacity

    def get(self, node_id: bytes) -> Contact | None:
        for c in self._contacts:
            if c.node_id == node_id:
                return c
        return None

    def most_recent(self) -> Contact | None:
        return self._contacts[0] if self._contacts else None

    def least_recent(self) -> Contact | None:
        return self._contacts[-1] if self._contacts else None

    def add_or_update(self, contact: Contact) -> AddResult:
        existing_index = next(
            (
                i
                for i, c in enumerate(self._contacts)
                if c.node_id == contact.node_id
            ),
            None,
        )
        if existing_index is not None:
            self._contacts.pop(existing_index)
            contact.touch()
            self._contacts.insert(0, contact)
            return AddResult.UPDATED
        if self.is_full():
            return AddResult.FULL
        contact.touch()
        self._contacts.insert(0, contact)
        return AddResult.ADDED

    def replace_lru(self, contact: Contact) -> Contact:
        if not self._contacts:
            raise RuntimeError("bucket пуст, replace_lru неприменим")
        removed = self._contacts.pop()
        contact.touch()
        self._contacts.insert(0, contact)
        return removed

    def touch(self, node_id: bytes) -> bool:
        existing = self.get(node_id)
        if existing is None:
            return False
        self._contacts.remove(existing)
        existing.touch()
        self._contacts.insert(0, existing)
        return True

    def remove(self, node_id: bytes) -> bool:
        existing = self.get(node_id)
        if existing is None:
            return False
        self._contacts.remove(existing)
        return True

    def contacts(self) -> list[Contact]:
        return list(self._contacts)