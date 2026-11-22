import asyncio
import hashlib
import secrets
from pathlib import Path

import pytest

from overlay.app.file import (
    CHUNK_SIZE,
    FileError,
    FileReceiver,
    FileSender,
    decode_app_payload,
    encode_app_chunk,
    encode_app_end,
    encode_app_meta,
    encode_app_text,
)
from overlay.config import Config
from overlay.node import Node


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
        try:
            await node.stop()
        except Exception:
            pass


def test_encode_decode_text():
    payload = encode_app_text("hello")
    data = decode_app_payload(payload)
    assert data["kind"] == "text"
    assert data["text"] == "hello"


def test_encode_decode_file_meta():
    file_id = secrets.token_bytes(16)
    payload = encode_app_meta(
        file_id, "test.txt", 100, "a" * 64, 1
    )
    data = decode_app_payload(payload)
    assert data["kind"] == "file_meta"
    assert data["name"] == "test.txt"
    assert data["size"] == 100


def test_chunk_size_limit():
    file_id = secrets.token_bytes(16)
    with pytest.raises(FileError):
        encode_app_chunk(file_id, 0, b"x" * (CHUNK_SIZE + 1))


def test_receiver_rejects_unsafe_name(tmp_path: Path):
    receiver = FileReceiver(tmp_path)
    file_id = secrets.token_bytes(16)
    payload = encode_app_meta(
        file_id, "../etc/passwd", 10, "a" * 64, 1
    )
    assert receiver.handle(payload).startswith(b"error:")


def test_receiver_rejects_chunk_without_meta(tmp_path: Path):
    receiver = FileReceiver(tmp_path)
    file_id = secrets.token_bytes(16)
    payload = encode_app_chunk(file_id, 0, b"x")
    assert receiver.handle(payload) == b"error:no file_meta"


def test_receiver_checks_sha_mismatch(tmp_path: Path):
    receiver = FileReceiver(tmp_path)
    file_id = secrets.token_bytes(16)
    data = b"hello"
    wrong_sha = "0" * 64
    receiver.handle(
        encode_app_meta(file_id, "f.txt", len(data), wrong_sha, 1)
    )
    receiver.handle(encode_app_chunk(file_id, 0, data))
    result = receiver.handle(encode_app_end(file_id))
    assert result == b"error:sha mismatch"


def test_receiver_saves_file(tmp_path: Path):
    receiver = FileReceiver(tmp_path / "recv")
    content = b"test content here"
    file_id = secrets.token_bytes(16)
    sha = hashlib.sha256(content).hexdigest()
    assert receiver.handle(
        encode_app_meta(file_id, "x.txt", len(content), sha, 1)
    ) == b"ok"
    assert receiver.handle(
        encode_app_chunk(file_id, 0, content)
    ) == b"ok"
    assert receiver.handle(encode_app_end(file_id)) == b"ok"
    saved = (tmp_path / "recv" / "x.txt").read_bytes()
    assert saved == content


def test_send_file_end_to_end(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 5)
        a, target = nodes[0], nodes[-1]
        try:
            src = tmp_path / "source.bin"
            content = secrets.token_bytes(150 * 1024)
            src.write_bytes(content)

            secure = await a.open_secure_tunnel(
                target.self_contact(), relay_count=2
            )
            meta = await secure.send_file(src)

            assert meta.size == len(content)
            assert meta.sha256_hex == hashlib.sha256(content).hexdigest()
            assert meta.chunks == 13

            received = (
                Path(target._config.node.state_dir)
                / "received"
                / meta.name
            )
            assert received.exists()
            assert received.read_bytes() == content
        finally:
            await _stop_network(nodes)

    _run(scenario())


def test_send_empty_file(tmp_path: Path):
    async def scenario():
        nodes = await _start_network(tmp_path, 4)
        a, target = nodes[0], nodes[-1]
        try:
            src = tmp_path / "empty.bin"
            src.write_bytes(b"")

            secure = await a.open_secure_tunnel(
                target.self_contact(), relay_count=2
            )
            meta = await secure.send_file(src)
            assert meta.size == 0
            assert meta.chunks == 0
        finally:
            await _stop_network(nodes)

    _run(scenario())