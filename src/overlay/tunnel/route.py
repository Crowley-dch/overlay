from __future__ import annotations

import time
from dataclasses import dataclass, field

from overlay.dht.contact import Contact
from overlay.tunnel.state import TunnelState

TUNNEL_ID_LEN = 16
DEFAULT_TUNNEL_TTL = 300
MIN_RELAYS = 1


class TunnelRouteError(Exception):
    pass


@dataclass
class TunnelRoute:
    tunnel_id: bytes
    hops: list[Contact]
    state: TunnelState = TunnelState.BUILDING
    max_hops: int = 0
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def __post_init__(self) -> None:
        if len(self.tunnel_id) != TUNNEL_ID_LEN:
            raise TunnelRouteError(
                f"tunnel_id должен быть {TUNNEL_ID_LEN} байт"
            )
        if len(self.hops) < MIN_RELAYS + 2:
            raise TunnelRouteError(
                f"маршрут должен содержать минимум {MIN_RELAYS + 2} узла"
            )
        node_ids = [c.node_id for c in self.hops]
        if len(set(node_ids)) != len(node_ids):
            raise TunnelRouteError("маршрут содержит повторяющиеся узлы")
        if self.max_hops == 0:
            self.max_hops = len(self.hops)
        if len(self.hops) > self.max_hops:
            raise TunnelRouteError("длина маршрута превышает max_hops")
        if self.expires_at == 0.0:
            self.expires_at = self.created_at + DEFAULT_TUNNEL_TTL

    @property
    def initiator(self) -> Contact:
        return self.hops[0]

    @property
    def destination(self) -> Contact:
        return self.hops[-1]

    @property
    def relays(self) -> list[Contact]:
        return self.hops[1:-1]

    @property
    def relay_count(self) -> int:
        return len(self.hops) - 2

    def is_expired(self, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        return current >= self.expires_at

    def describe(self) -> dict:
        return {
            "tunnel_id": self.tunnel_id.hex(),
            "state": self.state.value,
            "relay_count": self.relay_count,
            "max_hops": self.max_hops,
            "hops": [
                {"node_id": c.node_id_hex, "host": c.host, "port": c.port}
                for c in self.hops
            ],
        }


@dataclass
class RelayState:
    tunnel_id: bytes
    prev_hop: Contact | None
    next_hop: Contact | None
    max_hops: int
    expires_at: float

    def is_expired(self, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        return current >= self.expires_at

    @property
    def is_destination(self) -> bool:
        return self.next_hop is None