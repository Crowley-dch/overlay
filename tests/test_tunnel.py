import asyncio
import secrets
import time
from pathlib import Path

import pytest

from overlay.config import Config
from overlay.dht.contact import Contact
from overlay.node import Node
from overlay.tunnel import (
    RelayState,
    TunnelRoute,
    TunnelState,
    TunnelWireError,
    decode_tunnel_build,
    decode_tunnel_build_fail,
    decode_tunnel_build_ok,
    decode_tunnel_data,
    encode_tunnel_build,
    encode_tunnel_build_fail,
    encode_tunnel_build_ok,
    encode_tunnel_data,
)


def _run(coro):
    return asyncio.run(coro)


def _config(state_dir: Path, *, k: int = 4) -> Config:
    cfg = Config()
    cfg.node.host = "127.0.0.1"
    cfg.node.port = 0
    cfg.node.state_dir = str(state_dir)
    cfg.dht.k = k
    cfg.dht.alpha = 3
    cfg.dht.replication_factor = 3
    cfg.transport.connect_timeout = 1.0
    cfg.ttl.record_ttl = 120
    cfg.ttl.republish_interval = 60
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


def _contact(i: int) -> Contact:
    return Contact(node_id=bytes([i]) * 32, host="127.0.0.1", port=9000 + i)


def test_tunnel_route_requires_three_hops():
    with pytest.raises(Exception):
        TunnelRoute(
            tunnel_id=secrets.token_bytes(16),
            hops=[_contact(1), _contact(2)],
        )


def test_tunnel_route_rejects_duplicates():
    c = _contact(1)
    with pytest.raises(Exception):
        TunnelRoute(
            tunnel_id=secrets.token_bytes(16),
            hops=[c, c, _contact(2), _contact(3)],
        )


def test_tunnel_route_relay_count():
    route = TunnelRoute(
        tunnel_id=secrets.token_bytes(16),
        hops=[_contact(1), _contact(2), _contact(3), _contact(4)],
    )
    assert route.relay_count == 2
    assert route.initiator.node_id == _contact(1).node_id
    assert route.destination.node_id == _contact(4).node_id


def test_wire_tunnel_build_roundtrip():
    tunnel_id = secrets.token_bytes(16)
    prev_c = _contact(1)
    next_c = _contact(2)
    raw = encode_tunnel_build(
        tunnel_id, prev_hop=prev_c, next_hop=next_c, max_hops=4, ttl=300
    )
    decoded = decode_tunnel_build(raw)
    assert decoded["tunnel_id"] == tunnel_id
    assert decoded["prev_hop"].node_id == prev_c.node_id
    assert decoded["next_hop"].node_id == next_c.node_id
    assert decoded["max_hops"] == 4
    assert decoded["ttl"] == 300


def test_wire_tunnel_build_null_hops():
    tunnel_id = secrets.token_bytes(16)
    raw = encode_tunnel_build(
        tunnel_id, prev_hop=None, next_hop=None, max_hops=3, ttl=120
    )
    decoded = decode_tunnel_build(raw)
    assert decoded["prev_hop"] is None
    assert decoded["next_hop"] is None


def test_wire_tunnel_build_ok_roundtrip():
    tunnel_id = secrets.token_bytes(16)
    raw = encode_tunnel_build_ok(tunnel_id)
    assert decode_tunnel_build_ok(raw) == tunnel_id


def test_wire_tunnel_build_fail_roundtrip():
    tunnel_id = secrets.token_bytes(16)
    raw = encode_tunnel_build_fail(tunnel_id, "busy")
    tid, reason = decode_tunnel_build_fail(raw)
    assert tid == tunnel_id
    assert reason == "busy"


def test_wire_tunnel_data_roundtrip():
    tunnel_id = secrets.token_bytes(16)
    message_id = secrets.token_bytes(16)
    payload = b"hello world"
    raw = encode_tunnel_data(tunnel_id, message_id, payload)
    tid, mid, p, enc = decode_tunnel_data(raw)
    assert tid == tunnel_id
    assert mid == message_id
    assert p == payload


def test_wire_rejects_bad_tunnel_id():
    with pytest.raises(TunnelWireError):
        decode_tunnel_build(b'{"tunnel_id": "00"}')


def test_build_tunnel_three_relays(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 6)
        a = nodes[0]
        target = nodes[-1]
        try:
            route = await a.build_tunnel(target.self_contact(), relay_count=3)
            assert route.state == TunnelState.ACTIVE
            assert route.relay_count == 3
            assert route.initiator.node_id == a.node_id
            assert route.destination.node_id == target.node_id
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_build_tunnel_insufficient_relays(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 3)
        a = nodes[0]
        try:
            with pytest.raises(RuntimeError):
                await a.build_tunnel(nodes[-1].self_contact(), relay_count=5)
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_send_via_tunnel_through_three_relays(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 6)
        a, target = nodes[0], nodes[-1]
        try:
            route = await a.build_tunnel(target.self_contact(), relay_count=3)
            assert route.state == TunnelState.ACTIVE
            response = await a.send_via_tunnel(
                route.tunnel_id, b"end-to-end message"
            )
            assert response.startswith(b"delivered:")
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_relay_states_created_on_intermediate(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 6)
        a, target = nodes[0], nodes[-1]
        try:
            route = await a.build_tunnel(target.self_contact(), relay_count=2)
            assert route.state == TunnelState.ACTIVE
            for relay in route.relays:
                relay_node = next(
                    n for n in nodes if n.node_id == relay.node_id
                )
                assert route.tunnel_id in relay_node._relay_states
                rs = relay_node._relay_states[route.tunnel_id]
                assert rs.max_hops == len(route.hops)
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_close_tunnel_removes_state(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 6)
        a, target = nodes[0], nodes[-1]
        try:
            route = await a.build_tunnel(target.self_contact(), relay_count=2)
            await a.close_tunnel(route.tunnel_id)
            assert route.tunnel_id not in a._tunnel_routes

            for relay in route.relays:
                relay_node = next(
                    n for n in nodes if n.node_id == relay.node_id
                )
                assert route.tunnel_id not in relay_node._relay_states
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_send_via_dead_tunnel_fails(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 6)
        a, target = nodes[0], nodes[-1]
        try:
            route = await a.build_tunnel(target.self_contact(), relay_count=2)
            route.state = TunnelState.DEAD
            with pytest.raises(RuntimeError):
                await a.send_via_tunnel(route.tunnel_id, b"x")
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_build_tunnel_fails_when_relay_unreachable(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 5)
        a = nodes[0]
        target = nodes[-1]
        try:
            dead_port = 1
            fake_relay = Contact(
                node_id=secrets.token_bytes(32),
                host="127.0.0.1",
                port=dead_port,
            )
            original_select = a._select_relays

            def select_with_dead(destination, count):
                return [fake_relay, *original_select(destination, count - 1)]

            a._select_relays = select_with_dead

            route = await a.build_tunnel(
                target.self_contact(), relay_count=2
            )
            assert route.state == TunnelState.DEAD
        finally:
            await _stop_network(nodes)

    _run(scenario())