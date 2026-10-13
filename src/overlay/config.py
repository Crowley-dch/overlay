from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class ProtocolConfig:
    version: int = 1
    max_frame_payload: int = 65536


@dataclass(slots=True)
class TransportConfig:
    connect_timeout: float = 5.0
    write_timeout: float = 5.0


@dataclass(slots=True)
class NodeConfig:
    host: str = "127.0.0.1"
    port: int = 9000


@dataclass(slots=True)
class LoggingConfig:
    level: str = "INFO"
    format: str = "%(asctime)s %(levelname)s %(name)s: %(message)s"


@dataclass(slots=True)
class Config:
    protocol: ProtocolConfig = field(default_factory=ProtocolConfig)
    transport: TransportConfig = field(default_factory=TransportConfig)
    node: NodeConfig = field(default_factory=NodeConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _merge_dataclass(cls, data: dict[str, Any] | None):
    if not data:
        return cls()
    known = {f.name for f in cls.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in known}
    return cls(**filtered)


def load_config(path: str | os.PathLike | None = None) -> Config:
    if path is None:
        return Config()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Конфиг не найден: {p}")
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Config(
        protocol=_merge_dataclass(ProtocolConfig, raw.get("protocol")),
        transport=_merge_dataclass(TransportConfig, raw.get("transport")),
        node=_merge_dataclass(NodeConfig, raw.get("node")),
        logging=_merge_dataclass(LoggingConfig, raw.get("logging")),
    )