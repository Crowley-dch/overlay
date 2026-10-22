import asyncio
import secrets
from pathlib import Path

import pytest

from overlay.config import Config
from overlay.dht.contact import Contact
from overlay.dht.wire import (
    WireError,
    decode_find_node_request,
    decode_find_node_response,
    encode_find_node_request,
    encode_find_node_response,
)
from overlay.node import Node


def _run(coro):
    return asyncio.run(coro)


def _random_id() -> bytes:
    return secrets.token_bytes(32)


def _config(state_dir: Path, *, k: int = 4, port: int = 0) -> Config:
    cfg = Config()
    cfg.node.host = "127.0.0.1"
    cfg.node.port = port
    cfg.node.state_dir = str(state_dir)
    cfg.dht.k = k
    cfg.transport.connect_timeout = 1.0
    return cfg


def _same_bucket_ids(self_id: bytes, count: int) -> list[bytes]:
    return [
        self_id[:-1] + bytes([self_id[-1] ^ (0b100 + i)])
        for i in range(count)
    ]


def test_wire_find_node_request_roundtrip():
    target = _random_id()
    sender = Contact(node_id=_random_id(), host="127.0.0.1", port=9001)
    payload = encode_find_node_request(target, sender)
    decoded_target, decoded_sender = decode_find_node_request(payload)
    assert decoded_target == target
    assert decoded_sender.node_id == sender.node_id
    assert decoded_sender.host == sender.host
    assert decoded_sender.port == sender.port
def test_wire_find_node_response_roundtrip():
    contacts = [
        Contact(node_id=_random_id(), host="127.0.0.1", port=9001),
        Contact(node_id=_random_id(), host="10.0.0.5", port=9002),
    ]
    payload = encode_find_node_response(contacts)
    decoded = decode_find_node_response(payload)
    assert len(decoded) == 2
    for a, b in zip(contacts, decoded):
        assert a.node_id == b.node_id
        assert a.host == b.host
        assert a.port == b.port


def test_wire_find_node_request_rejects_bad_payload():
    sender = Contact(node_id=_random_id(), host="127.0.0.1", port=9001)
    with pytest.raises(WireError):
        decode_find_node_request(b"not json")
    with pytest.raises(WireError):
        decode_find_node_request(b'{"target": "abcd"}')
    with pytest.raises(WireError):
        decode_find_node_request(b'{"target": 123}')
    with pytest.raises(WireError):
        decode_find_node_request(
            b'{"target": "' + (_random_id().hex().encode()) + b'"}'
        )
    with pytest.raises(WireError):
        decode_find_node_request(
            b'{"target": "' + (_random_id().hex().encode()) + b'", '
            b'"sender": {"node_id": "00"}}'
        )

def test_wire_find_node_response_rejects_bad_contact():
    with pytest.raises(WireError):
        decode_find_node_response(b'{"contacts": [{"node_id": "00"}]}')
    with pytest.raises(WireError):
        decode_find_node_response(
            b'{"contacts": [{"node_id": "' + b"a" * 64 + b'", '
            b'"host": "", "port": 9000}]}'
        )
    with pytest.raises(WireError):
        decode_find_node_response(
            b'{"contacts": [{"node_id": "' + b"a" * 64 + b'", '
            b'"host": "127.0.0.1", "port": 0}]}'
        )


def test_node_ping_another_node(tmp_path: Path):
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


def test_find_node_sender_is_added_to_responder_routing(tmp_path: Path):
    async def scenario():
        a = Node(_config(tmp_path / "a"))
        b = Node(_config(tmp_path / "b"))
        await a.start()
        await b.start()
        try:
            assert len(b.routing.routing) == 0

            contacts = await a.find_node(_random_id(), b.self_contact())

            assert len(b.routing.routing) == 1
            assert b.routing.routing.get(a.node_id) is not None
            assert len(contacts) == 1
            assert contacts[0].node_id == a.node_id
        finally:
            await a.stop()
            await b.stop()

    _run(scenario())

def test_find_node_returns_closest_to_target(tmp_path: Path):
    async def scenario():
        a = Node(_config(tmp_path / "a"))
        b = Node(_config(tmp_path / "b", k=4))
        await a.start()
        await b.start()
        try:
            ids = _same_bucket_ids(b.node_id, 4)
            for node_id in ids:
                await b.routing.add_contact(
                    Contact(
                        node_id=node_id,
                        host="127.0.0.1",
                        port=1,
                    )
                )

            target = ids[3]
            contacts = await a.find_node(target, b.self_contact())
            assert len(contacts) == 4
            assert contacts[0].node_id == ids[3]
        finally:
            await a.stop()
            await b.stop()

    _run(scenario())


def test_find_node_respects_k(tmp_path: Path):
    async def scenario():
        a = Node(_config(tmp_path / "a"))
        b = Node(_config(tmp_path / "b", k=3))
        await a.start()
        await b.start()
        try:
            ids = _same_bucket_ids(b.node_id, 8)
            for node_id in ids:
                await b.routing.add_contact(
                    Contact(node_id=node_id, host="127.0.0.1", port=1)
                )

            contacts = await a.find_node(ids[0], b.self_contact())
            assert len(contacts) == 3
        finally:
            await a.stop()
            await b.stop()

    _run(scenario())


def test_secure_find_node_rejects_bad_request(tmp_path: Path):
    async def scenario():
        a = Node(_config(tmp_path / "a"))
        b = Node(_config(tmp_path / "b"))
        await a.start()
        await b.start()
        try:
            from overlay.transport.frame import MessageType
            from overlay.transport.rpc import RequestTimeout, RpcClient
            from overlay.transport.secure import SecureConnection

            secure = await SecureConnection.initiate(
                b.host, b.port, a.identity, connect_timeout=1.0
            )
            try:
                rpc = RpcClient(secure, default_timeout=1.0)
                await rpc.start()
                try:
                    try:
                        await rpc.request(
                            MessageType.FIND_NODE_REQUEST,
                            payload=b"not json",
                            expected_response_types={
                                MessageType.FIND_NODE_RESPONSE
                            },
                        )
                        raised = False
                    except RequestTimeout:
                        raised = True
                    assert raised
                finally:
                    await rpc.stop()
            finally:
                secure.close()
                await secure.wait_closed()
        finally:
            await a.stop()
            await b.stop()

    _run(scenario())