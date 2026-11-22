from __future__ import annotations

import time
from dataclasses import dataclass

EMA_ALPHA = 0.3
MIN_SAMPLES_FOR_RATING = 3


@dataclass
class RelayProfile:
    node_id: bytes
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    ema_success: float = 0.5
    ema_latency: float = 0.0
    last_seen: float = 0.0
    subnet: str = ""
    samples: int = 0

    def record_success(self, latency: float) -> None:
        self.samples += 1
        self.successes += 1
        self.consecutive_failures = 0
        alpha = EMA_ALPHA
        self.ema_success = alpha * 1.0 + (1 - alpha) * self.ema_success
        if self.ema_latency == 0.0:
            self.ema_latency = latency
        else:
            self.ema_latency = (
                alpha * latency + (1 - alpha) * self.ema_latency
            )
        self.last_seen = time.time()

    def record_failure(self) -> None:
        self.samples += 1
        self.failures += 1
        self.consecutive_failures += 1
        alpha = EMA_ALPHA
        self.ema_success = alpha * 0.0 + (1 - alpha) * self.ema_success
        self.last_seen = time.time()

    def score(self) -> float:
        if self.samples < MIN_SAMPLES_FOR_RATING:
            return 0.5
        if self.consecutive_failures >= 2:
            return 0.0
        base = self.ema_success
        if self.ema_latency > 0.0:
            latency_penalty = 1.0 / (1.0 + self.ema_latency)
            base = base * 0.7 + latency_penalty * 0.3
        return base

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id.hex(),
            "successes": self.successes,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "ema_success": self.ema_success,
            "ema_latency": self.ema_latency,
            "samples": self.samples,
            "subnet": self.subnet,
            "score": self.score(),
        }


class RelayProfileStore:
    def __init__(self) -> None:
        self._profiles: dict[bytes, RelayProfile] = {}

    def get(self, node_id: bytes) -> RelayProfile:
        p = self._profiles.get(node_id)
        if p is None:
            p = RelayProfile(node_id=node_id)
            self._profiles[node_id] = p
        return p

    def record_success(self, node_id: bytes, latency: float) -> None:
        self.get(node_id).record_success(latency)

    def record_failure(self, node_id: bytes) -> None:
        self.get(node_id).record_failure()

    def score(self, node_id: bytes) -> float:
        p = self._profiles.get(node_id)
        return p.score() if p is not None else 0.5

    def all(self) -> list[RelayProfile]:
        return list(self._profiles.values())

    def snapshot(self) -> dict:
        return {
            nid.hex(): p.to_dict() for nid, p in self._profiles.items()
        }