from __future__ import annotations

import enum


class TunnelState(enum.Enum):
    BUILDING = "BUILDING"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    CLOSING = "CLOSING"
    DEAD = "DEAD"