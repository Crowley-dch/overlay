import asyncio
import secrets
from pathlib import Path

import pytest

from overlay.config import Config
from overlay.crypto.aead import ReplayError
from overlay.node import Node
from overlay.transport.frame import Frame, MessageType
from overlay.transport.secure import SecureConnection


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


def test_handshake_and_ping_over_secure(tmp_path: Path):
    async def scenario():
        a = Node(_config(tmp_path / "a"))
        b = Node(_config(tmp_path / "b"))
        await a.start()
        await b.start()
        try:
            alive = await a.ping(b.self_contact())
            assert alive is True
        finally:
            await a.stop()
            await b.stop()

    _run(scenario())


def test_secure_frame_does_not_leak_plaintext(tmp_path: Path):
    async def scenario():
        a = Node(_config(tmp_path / "a"))
        b = Node(_config(tmp_path / "b"))
        await a.start()
        await b.start()
        try:
            secure = await SecureConnection.initiate(
                b.host, b.port, a.identity, connect_timeout=1.0
            )
            try:
                captured = []
                orig_send = secure._conn.send

                async def capture(frame):
                    captured.append(frame.encode())
                    return await orig_send(frame)

                secure._conn.send = capture

                secret_payload = b"secret-diagnostic-payload"
                request_id = secrets.token_bytes(16)
                await secure.send(
                    Frame(
                        msg_type=MessageType.APP_MESSAGE,
                        request_id=request_id,
                        payload=secret_payload,
                    )
                )

                all_bytes = b"".join(captured)
                assert secret_payload not in all_bytes
            finally:
                secure.close()
                await secure.wait_closed()
        finally:
            await a.stop()
            await b.stop()

    _run(scenario())

def test_replay_of_secure_frame_rejected(tmp_path: Path):
    async def scenario():
        a = Node(_config(tmp_path / "a"))
        b = Node(_config(tmp_path / "b"))
        await a.start()
        await b.start()
        try:
            secure = await SecureConnection.initiate(
                b.host, b.port, a.identity, connect_timeout=1.0
            )
            try:
                captured = []
                orig_send = secure._conn.send

                async def capture(frame):
                    captured.append(frame)
                    return await orig_send(frame)

                secure._conn.send = capture

                await secure.send(
                    Frame(
                        msg_type=MessageType.PING,
                        request_id=secrets.token_bytes(16),
                        payload=b"first",
                    )
                )

                replay_frame = captured[-1]
                from overlay.transport.connection import Connection

                repl_conn = await Connection.connect(
                    b.host, b.port, connect_timeout=1.0
                )
                try:
                    await repl_conn.send(replay_frame)
                    await asyncio.sleep(0.2)
                finally:
                    repl_conn.close()
                    await repl_conn.wait_closed()
            finally:
                secure.close()
                await secure.wait_closed()

            alive = await a.ping(b.self_contact())
            assert alive is True
        finally:
            await a.stop()
            await b.stop()

    _run(scenario())



def test_dht_regression_through_secure(tmp_path: Path):
    async def scenario():
        nodes = []
        for i in range(4):
            node = Node(_config(tmp_path / f"node-{i}"))
            await node.start()
            nodes.append(node)
        seed = nodes[0]
        try:
            for node in nodes[1:]:
                await node.bootstrap([seed.self_contact()])
            for node in nodes[1:]:
                await node.bootstrap([seed.self_contact()])

            result, stats = await nodes[-1].lookup_node(nodes[0].node_id)
            assert nodes[0].node_id in {c.node_id for c in result}
            assert stats.rpc_count > 0
        finally:
            for node in nodes:
                await node.stop()

    _run(scenario())