import asyncio
import time
from pathlib import Path

from overlay.config import Config
from overlay.dht.record import build_record, record_key_for_node
from overlay.node import Node


def _run(coro):
    return asyncio.run(coro)


def _config(
    state_dir: Path,
    *,
    record_ttl: int = 120,
    republish_interval: int = 60,
) -> Config:
    cfg = Config()
    cfg.node.host = "127.0.0.1"
    cfg.node.port = 0
    cfg.node.state_dir = str(state_dir)
    cfg.dht.k = 4
    cfg.dht.alpha = 3
    cfg.dht.replication_factor = 3
    cfg.transport.connect_timeout = 1.0
    cfg.ttl.record_ttl = record_ttl
    cfg.ttl.republish_interval = republish_interval
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


def test_cleanup_removes_expired_records(tmp_path: Path):
    async def scenario():
        node = Node(_config(tmp_path / "solo"))
        await node.start()
        try:
            past = int(time.time()) - 3600
            record = build_record(
                node_id=node.node_id,
                identity_public_key=node.identity.public_key_bytes(),
                addresses=[f"{node.host}:{node.port}"],
                sequence_number=1,
                ttl=60,
                private_key=node.identity.private_key,
                issued_at=past,
            )
            key = record_key_for_node(node.node_id)
            node.store._records[key] = record
            assert len(node.store) == 1

            removed = node.store.cleanup()
            assert removed == 1
            assert len(node.store) == 0
        finally:
            await node.stop()

    _run(scenario())


def test_republish_increments_sequence(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 3)
        a = nodes[0]
        try:
            record = _self_record(a, seq=1)
            await a.put_record(record)
            key = record_key_for_node(a.node_id)

            before = a.store.get(key)
            assert before is not None
            assert before.sequence_number == 1

            await a._republish_own_records()

            after = a.store.get(key)
            assert after is not None
            assert after.sequence_number == 2
            assert after.verify_signature() is True
            assert after.issued_at >= before.issued_at
            assert after.expires_at >= before.expires_at

            remote = await nodes[1].get_record(key)
            assert remote is not None
            assert remote.sequence_number == 2
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_republish_skips_foreign_records(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 3)
        a, b = nodes[0], nodes[1]
        try:
            record_b = _self_record(b, seq=1)
            await b.put_record(record_b)
            key_b = record_key_for_node(b.node_id)

            await a._republish_own_records()

            still = b.store.get(key_b)
            assert still is not None
            assert still.sequence_number == 1
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_maintenance_task_starts_and_stops(tmp_path: Path):
    async def scenario():
        node = Node(_config(tmp_path / "solo"))
        await node.start()
        assert node._maintenance_task is not None
        assert not node._maintenance_task.done()
        await asyncio.sleep(0.05)
        await node.stop()
        assert node._maintenance_task is None

    _run(scenario())


def test_maintenance_loop_runs_republish_with_short_interval(tmp_path: Path):
    async def scenario():
        nodes = []
        for i in range(3):
            cfg = _config(
                tmp_path / f"node-{i}",
                record_ttl=10,
                republish_interval=1,
            )
            node = Node(cfg)
            await node.start()
            nodes.append(node)
        seed = nodes[0]
        for n in nodes[1:]:
            await n.bootstrap([seed.self_contact()])
        for n in nodes[1:]:
            await n.bootstrap([seed.self_contact()])
        try:
            a = nodes[0]
            record = _self_record(a, seq=1, ttl=10)
            await a.put_record(record)
            key = record_key_for_node(a.node_id)

            initial = a.store.get(key)
            assert initial is not None
            initial_seq = initial.sequence_number

            await asyncio.sleep(4.0)

            after = a.store.get(key)
            assert after is not None
            assert after.sequence_number > initial_seq
        finally:
            await _stop_network(nodes)

    _run(scenario())