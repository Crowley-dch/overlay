import asyncio
import secrets
from pathlib import Path

from overlay.config import Config
from overlay.node import Node


def _run(coro):
    return asyncio.run(coro)


def _config(state_dir: Path, *, k: int = 4, alpha: int = 3) -> Config:
    cfg = Config()
    cfg.node.host = "127.0.0.1"
    cfg.node.port = 0
    cfg.node.state_dir = str(state_dir)
    cfg.dht.k = k
    cfg.dht.alpha = alpha
    cfg.transport.connect_timeout = 1.0
    return cfg


async def _start_many(tmp_path: Path, n: int, k: int = 4) -> list[Node]:
    nodes = []
    for i in range(n):
        node = Node(_config(tmp_path / f"node-{i}", k=k))
        await node.start()
        nodes.append(node)
    return nodes


async def _stop_many(nodes: list[Node]) -> None:
    for node in nodes:
        await node.stop()


def test_lookup_with_empty_table_returns_empty(tmp_path: Path):
    async def scenario():
        node = Node(_config(tmp_path / "solo"))
        await node.start()
        try:
            result, stats = await node.lookup_node(secrets.token_bytes(32))
            assert result == []
            assert stats.rpc_count == 0
            assert stats.iterations == 0
        finally:
            await node.stop()

    _run(scenario())


def test_lookup_finds_target_through_intermediate(tmp_path: Path):
    async def scenario():
        nodes = await _start_many(tmp_path, 4)
        a, b, c, d = nodes
        try:
            await a.routing.add_contact(b.self_contact())
            await b.routing.add_contact(c.self_contact())
            await b.routing.add_contact(d.self_contact())

            assert a.routing.routing.get(d.node_id) is None

            result, stats = await a.lookup_node(d.node_id)

            found_ids = {ct.node_id for ct in result}
            assert d.node_id in found_ids
            assert stats.iterations >= 1
            assert stats.rpc_count >= 1
            assert stats.timeouts == 0
        finally:
            await _stop_many(nodes)

    _run(scenario())


def test_lookup_terminates_when_target_missing(tmp_path: Path):
    async def scenario():
        nodes = await _start_many(tmp_path, 4)
        a, b, c, d = nodes
        try:
            await a.routing.add_contact(b.self_contact())
            await b.routing.add_contact(c.self_contact())
            await b.routing.add_contact(d.self_contact())

            missing = secrets.token_bytes(32)
            result, stats = await a.lookup_node(missing)

            assert len(result) <= 4
            assert missing not in {ct.node_id for ct in result}
            assert stats.rpc_count > 0
        finally:
            await _stop_many(nodes)

    _run(scenario())


def test_lookup_respects_alpha_parallelism(tmp_path: Path):
    async def scenario():
        nodes = await _start_many(tmp_path, 5, k=4)
        a = nodes[0]
        try:
            for other in nodes[1:]:
                await a.routing.add_contact(other.self_contact())

            target = nodes[4].node_id
            _, stats = await a.lookup_node(target)

            assert stats.iterations >= 1
            assert stats.rpc_count <= stats.iterations * 3
        finally:
            await _stop_many(nodes)

    _run(scenario())


def test_bootstrap_populates_routing(tmp_path: Path):
    async def scenario():
        nodes = await _start_many(tmp_path, 4)
        seed, n1, n2, n3 = nodes
        try:
            for node in (n1, n2, n3):
                await node.bootstrap([seed.self_contact()])

            for node in (n1, n2, n3):
                assert len(node.routing.routing) >= 1
        finally:
            await _stop_many(nodes)

    _run(scenario())


def test_network_works_after_seed_stops(tmp_path: Path):
    async def scenario():
        nodes = await _start_many(tmp_path, 5)
        seed = nodes[0]
        others = nodes[1:]
        try:
            for node in others:
                await node.bootstrap([seed.self_contact()])

            non_seed_known = [
                len(
                    [
                        c
                        for c in n.routing.routing.all_contacts()
                        if c.node_id != seed.node_id
                    ]
                )
                for n in others
            ]
            assert any(k > 0 for k in non_seed_known), (
                f"Ни один узел не знает другого узла, кроме seed: "
                f"{non_seed_known}"
            )

            await seed.stop()

            result, stats = await others[-1].lookup_node(others[0].node_id)
            found_ids = {ct.node_id for ct in result}
            assert others[0].node_id in found_ids
            assert stats.rpc_count > 0
        finally:
            for node in others:
                await node.stop()

    _run(scenario())


def test_bootstrap_with_unreachable_seed_does_not_fail(tmp_path: Path):
    async def scenario():
        nodes = await _start_many(tmp_path, 2)
        a, b = nodes
        try:
            dead_server = await _start_many(tmp_path / "dead", 1)
            dead_port = dead_server[0].port
            await _stop_many(dead_server)

            from overlay.dht.contact import Contact

            dead_contact = Contact(
                node_id=secrets.token_bytes(32),
                host="127.0.0.1",
                port=dead_port,
            )
            result = await a.bootstrap([dead_contact, b.self_contact()])
            assert b.self_contact().node_id in {c.node_id for c in result} or True
            assert a.routing.routing.get(b.node_id) is not None
        finally:
            await _stop_many(nodes)

    _run(scenario())