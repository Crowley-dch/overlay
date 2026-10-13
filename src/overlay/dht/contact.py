from __future__ import annotations

import time
from dataclasses import dataclass

from overlay.identity.node_id import NODE_ID_LENGTH


@dataclass(slots=True)
class Contact:
    node_id: bytes
    host: str
    port: int
    last_seen: float = 0.0

    def __post_init__(self) -> None:
        if len(self.node_id) != NODE_ID_LENGTH:
            raise ValueError(
                f"node_id должен быть {NODE_ID_LENGTH} байт, "
                f"получено {len(self.node_id)}"
            )

    @property
    def node_id_hex(self) -> str:
        return self.node_id.hex()

    def touch(self, when: float | None = None) -> None:
        self.last_seen = when if when is not None else time.monotonic()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Contact):
            return NotImplemented
        return self.node_id == other.node_id

    def __hash__(self) -> int:
        return hash(self.node_id)

    def __repr__(self) -> str:
        return f"<Contact {self.node_id.hex()[:12]} {self.host}:{self.port}>"