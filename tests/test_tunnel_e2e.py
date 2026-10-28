import asyncio
import secrets
from pathlib import Path

import pytest

from overlay.config import Config
from overlay.crypto.handshake import InitiatorHandshake
from overlay.node import Node
from overlay.transport.frame import Frame, MessageType
from overlay.transport.secure import SecureConnection
from overlay.tunnel import (
    TunnelState,
    decode_tunnel_data,
    encode_tunnel_data,
)


def _run(coro):
    return asyncio.run(coro)


def _config(state_dir: Path) -> Config:
    cfg = Config()
    cfg.node.host = "127.0.0.1"
    cfg.node.port = 0
    cfg.node.state_dir = str(state_dir)
    cfg.dht.k = 4
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


def test_e2e_handshake_and_message(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 6)
        a, target = nodes[0], nodes[-1]
        try:
            secure_tunnel = await a.open_secure_tunnel(
                target.self_contact(), relay_count=3
            )
            assert secure_tunnel.peer_node_id == target.node_id
            response = await secure_tunnel.send_message(b"hi-e2e")
            assert response == b"delivered:hi-e2e"
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_e2e_multiple_messages(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 6)
        a, target = nodes[0], nodes[-1]
        try:
            secure_tunnel = await a.open_secure_tunnel(
                target.self_contact(), relay_count=2
            )
            for i in range(5):
                payload = f"msg-{i}".encode()
                resp = await secure_tunnel.send_message(payload)
                assert resp == b"delivered:" + payload
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_relay_never_sees_plaintext(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 6)
        a, target = nodes[0], nodes[-1]
        try:
            secure_tunnel = await a.open_secure_tunnel(
                target.self_contact(), relay_count=2
            )

            captured = []
            for node in nodes[1:-1]:
                if secure_tunnel.tunnel_id not in node._relay_states:
                    continue
                orig = node._on_tunnel_data

                async def wrapper(conn, frame, _orig=orig):
                    try:
                        _, _, p, enc = decode_tunnel_data(frame.payload)
                        captured.append((p, enc))
                    except Exception:
                        pass
                    return await _orig(conn, frame)

                node._on_tunnel_data = wrapper

            secret_payload = b"ultra-secret-diagnostic"
            await secure_tunnel.send_message(secret_payload)

            for relay_payload, encrypted in captured:
                if encrypted:
                    assert secret_payload not in relay_payload
                    assert b"APP_MESSAGE" not in relay_payload
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_e2e_deduplicates_at_destination(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 5)
        a, target = nodes[0], nodes[-1]
        try:
            secure_tunnel = await a.open_secure_tunnel(
                target.self_contact(), relay_count=2
            )

            received = []
            orig_handle = target._on_app_message

            async def capture(inner_frame):
                received.append(inner_frame.request_id)
                return await orig_handle(inner_frame)

            target._on_app_message = capture

            await secure_tunnel.send_message(b"once")
            assert len(received) == 1
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_e2e_rejects_plaintext_in_encrypted_frame(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 5)
        a, target = nodes[0], nodes[-1]
        try:
            secure_tunnel = await a.open_secure_tunnel(
                target.self_contact(), relay_count=2
            )

            import secrets as _s
            from overlay.transport.frame import Frame, MessageType as MT

            fake_payload = (
                b"\x00" * 16
                + (0).to_bytes(8, "big")
                + Frame(
                    msg_type=MT.APP_MESSAGE,
                    request_id=_s.token_bytes(16),
                    payload=b"forged",
                ).encode()
            )

            try:
                await a.send_via_tunnel(
                    secure_tunnel.tunnel_id,
                    fake_payload,
                    encrypted=True,
                )
                raised = False
            except Exception:
                raised = True
            assert raised
        finally:
            await _stop_network(nodes)

    _run(scenario())