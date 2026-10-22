import asyncio
import time
from pathlib import Path

import pytest

from overlay.config import Config
from overlay.dht.record import RecordError, build_record, record_key_for_node
from overlay.node import Node


def _run(coro):
    return asyncio.run(coro)


def _config(state_dir: Path, *, k: int = 4, r: int = 3) -> Config:
    cfg = Config()
    cfg.node.host = "127.0.0.1"
    cfg.node.port = 0
    cfg.node.state_dir = str(state_dir)
    cfg.dht.k = k
    cfg.dht.alpha = 3
    cfg.dht.replication_factor = r
    cfg.transport.connect_timeout = 1.0
    cfg.ttl.record_ttl = 120
    cfg.ttl.republish_interval = 50
    cfg.ttl.max_record_size = 4096
    return cfg


async def _start_network(tmp_path: Path, n: int) -> list[Node]:
    nodes = []
    for i in range(n):
        node = Node(_config(tmp_path / f"node-{i}"))
        await node.start()
        nodes.append(node)
    seed = nodes[0]
    for node in nodes[1:]:
        await node.bootstrap([seed.self_contact()])
    for node in nodes[1:]:
        await node.bootstrap([seed.self_contact()])
    return nodes


async def _stop_network(nodes: list[Node]) -> None:
    for node in nodes:
        await node.stop()


def _self_record(node: Node, *, seq: int = 1, ttl: int = 120):
    return build_record(
        node_id=node.node_id,
        identity_public_key=node.identity.public_key_bytes(),
        addresses=[f"{node.host}:{node.port}"],
        sequence_number=seq,
        ttl=ttl,
        private_key=node.identity.private_key,
    )


def test_put_record_replicates_and_get_returns(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 5)
        a = nodes[0]
        try:
            record = _self_record(a)
            successes = await a.put_record(record)
            assert successes >= 2

            key = record_key_for_node(a.node_id)
            got = await nodes[2].get_record(key)
            assert got is not None
            assert got.node_id == a.node_id
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_get_record_after_holder_failure(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 5)
        a, b = nodes[0], nodes[1]
        try:
            record = _self_record(a)
            await a.put_record(record)
            key = record_key_for_node(a.node_id)

            await a.stop()

            got = await nodes[3].get_record(key)
            assert got is not None
            assert got.node_id == a.node_id
        finally:
            for node in nodes:
                if node is not a:
                    await node.stop()

    _run(scenario())


def test_get_record_returns_none_for_missing_key(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 3)
        try:
            missing = b"\x42" * 32
            got = await nodes[0].get_record(missing)
            assert got is None
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_put_record_rejects_insufficient_replicas(tmp_path: Path):
    async def scenario():
        node = Node(_config(tmp_path / "solo"))
        await node.start()
        try:
            record = _self_record(node)
            try:
                await node.put_record(record)
                insufficient = False
            except RuntimeError:
                insufficient = True
            assert insufficient
        finally:
            await node.stop()

    _run(scenario())


def test_store_rejects_expired_record_on_remote(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 3)
        a, b = nodes[0], nodes[1]
        try:
            past = int(time.time()) - 3600
            record = build_record(
                node_id=a.node_id,
                identity_public_key=a.identity.public_key_bytes(),
                addresses=[f"{a.host}:{a.port}"],
                sequence_number=1,
                ttl=60,
                private_key=a.identity.private_key,
                issued_at=past,
            )
            key = record_key_for_node(a.node_id)
            stored = await a._store_rpc(b.self_contact(), key, record)
            assert stored is False
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_store_rejects_rollback_on_remote(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 3)
        a, b = nodes[0], nodes[1]
        try:
            key = record_key_for_node(a.node_id)
            newer = build_record(
                node_id=a.node_id,
                identity_public_key=a.identity.public_key_bytes(),
                addresses=[f"{a.host}:{a.port}"],
                sequence_number=5,
                ttl=120,
                private_key=a.identity.private_key,
            )
            older = build_record(
                node_id=a.node_id,
                identity_public_key=a.identity.public_key_bytes(),
                addresses=[f"{a.host}:{a.port}"],
                sequence_number=3,
                ttl=120,
                private_key=a.identity.private_key,
            )
            assert await a._store_rpc(b.self_contact(), key, newer) is True
            assert await a._store_rpc(b.self_contact(), key, older) is False
        finally:
            await _stop_network(nodes)

    _run(scenario())