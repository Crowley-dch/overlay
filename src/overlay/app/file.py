from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

CHUNK_SIZE = 12 * 1024
MAX_FILE_SIZE = 5 * 1024 * 1024
DEFAULT_TIMEOUT = 60.0


class FileError(Exception):
    pass


def encode_app_text(text: str) -> bytes:
    return json.dumps(
        {"kind": "text", "text": text},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def encode_app_meta(
    file_id: bytes,
    name: str,
    size: int,
    sha256_hex: str,
    chunks: int,
) -> bytes:
    if len(file_id) != 16:
        raise FileError("file_id должен быть 16 байт")
    return json.dumps(
        {
            "kind": "file_meta",
            "file_id": file_id.hex(),
            "name": name,
            "size": size,
            "sha256": sha256_hex,
            "chunks": chunks,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def encode_app_chunk(
    file_id: bytes,
    index: int,
    data: bytes,
) -> bytes:
    if len(file_id) != 16:
        raise FileError("file_id должен быть 16 байт")
    if len(data) > CHUNK_SIZE:
        raise FileError(
            f"чанк {len(data)} > CHUNK_SIZE {CHUNK_SIZE}"
        )
    return json.dumps(
        {
            "kind": "file_chunk",
            "file_id": file_id.hex(),
            "index": index,
            "data": data.hex(),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def encode_app_end(file_id: bytes) -> bytes:
    if len(file_id) != 16:
        raise FileError("file_id должен быть 16 байт")
    return json.dumps(
        {
            "kind": "file_end",
            "file_id": file_id.hex(),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def decode_app_payload(payload: bytes) -> dict:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise FileError(f"Некорректный JSON: {e}") from e
    if not isinstance(data, dict):
        raise FileError("Ожидался объект")
    kind = data.get("kind")
    if kind not in {"text", "file_meta", "file_chunk", "file_end"}:
        raise FileError(f"Неизвестный kind: {kind!r}")
    return data


@dataclass
class FileMeta:
    file_id: bytes
    name: str
    size: int
    sha256_hex: str
    chunks: int


@dataclass
class _IncomingFile:
    meta: FileMeta
    buffer: list[bytes | None]
    received_chunks: int = 0

    def is_complete(self) -> bool:
        return self.received_chunks == self.meta.chunks


class FileSender:
    def __init__(self, secure_tunnel) -> None:
        self._tunnel = secure_tunnel

    async def send_file(
        self,
        path: str | Path,
        *,
        remote_name: str | None = None,
    ) -> FileMeta:
        p = Path(path)
        if not p.exists() or not p.is_file():
            raise FileError(f"Файл не найден: {p}")
        raw = p.read_bytes()
        if len(raw) > MAX_FILE_SIZE:
            raise FileError(
                f"Размер {len(raw)} > максимум {MAX_FILE_SIZE}"
            )

        digest = hashlib.sha256(raw).hexdigest()
        file_id = secrets.token_bytes(16)
        chunks_total = (len(raw) + CHUNK_SIZE - 1) // CHUNK_SIZE

        meta = FileMeta(
            file_id=file_id,
            name=remote_name or p.name,
            size=len(raw),
            sha256_hex=digest,
            chunks=chunks_total,
        )

        ack = await self._tunnel.send_message(
            encode_app_meta(
                file_id, meta.name, meta.size, meta.sha256_hex, meta.chunks
            )
        )
        if ack != b"ok":
            raise FileError(f"Получатель отклонил meta: {ack!r}")

        for i in range(chunks_total):
            start = i * CHUNK_SIZE
            end = min(start + CHUNK_SIZE, len(raw))
            chunk = raw[start:end]
            ack = await self._tunnel.send_message(
                encode_app_chunk(file_id, i, chunk)
            )
            if ack != b"ok":
                raise FileError(
                    f"Получатель отклонил chunk {i}: {ack!r}"
                )

        ack = await self._tunnel.send_message(encode_app_end(file_id))
        if ack != b"ok":
            raise FileError(f"Получатель отклонил end: {ack!r}")

        log.info(
            "Файл %s (%d байт, %d чанков) доставлен",
            meta.name,
            meta.size,
            meta.chunks,
        )
        return meta


class FileReceiver:
    def __init__(self, output_dir: str | Path) -> None:
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._incoming: dict[bytes, _IncomingFile] = {}

    def handle(self, payload: bytes) -> bytes:
        try:
            data = decode_app_payload(payload)
        except FileError as e:
            return f"error:{e}".encode("utf-8")
        kind = data["kind"]
        if kind == "text":
            return self._on_text(data)
        if kind == "file_meta":
            return self._on_meta(data)
        if kind == "file_chunk":
            return self._on_chunk(data)
        if kind == "file_end":
            return self._on_end(data)
        return b"error:unknown kind"

    def _on_text(self, data: dict) -> bytes:
        text = data.get("text", "")
        if not isinstance(text, str):
            return b"error:text must be string"
        return b"ok"

    def _on_meta(self, data: dict) -> bytes:
        try:
            file_id = bytes.fromhex(data["file_id"])
            name = data["name"]
            size = int(data["size"])
            sha = data["sha256"]
            chunks = int(data["chunks"])
        except (KeyError, ValueError, TypeError) as e:
            return f"error:meta {e}".encode("utf-8")

        if len(file_id) != 16:
            return b"error:bad file_id"
        if not isinstance(name, str) or "/" in name or name.startswith("."):
            return b"error:unsafe name"
        if size < 0 or size > MAX_FILE_SIZE:
            return b"error:size"
        if not isinstance(sha, str) or len(sha) != 64:
            return b"error:sha format"
        if chunks < 0 or chunks > (MAX_FILE_SIZE // CHUNK_SIZE) + 1:
            return b"error:chunks"
        if file_id in self._incoming:
            return b"error:duplicate file_id"

        meta = FileMeta(
            file_id=file_id,
            name=name,
            size=size,
            sha256_hex=sha,
            chunks=chunks,
        )
        self._incoming[file_id] = _IncomingFile(
            meta=meta,
            buffer=[None] * chunks,
        )
        return b"ok"

    def _on_chunk(self, data: dict) -> bytes:
        try:
            file_id = bytes.fromhex(data["file_id"])
            index = int(data["index"])
            chunk = bytes.fromhex(data["data"])
        except (KeyError, ValueError, TypeError) as e:
            return f"error:chunk {e}".encode("utf-8")

        state = self._incoming.get(file_id)
        if state is None:
            return b"error:no file_meta"
        if index < 0 or index >= state.meta.chunks:
            return b"error:index"
        if len(chunk) > CHUNK_SIZE:
            return b"error:chunk too large"

        if state.buffer[index] is None:
            state.received_chunks += 1
        state.buffer[index] = chunk
        return b"ok"

    def _on_end(self, data: dict) -> bytes:
        try:
            file_id = bytes.fromhex(data["file_id"])
        except (KeyError, ValueError) as e:
            return f"error:end {e}".encode("utf-8")

        state = self._incoming.pop(file_id, None)
        if state is None:
            return b"error:no file_meta"

        if not state.is_complete():
            return (
                f"error:missing {state.meta.chunks - state.received_chunks}"
                .encode("utf-8")
            )

        assembled = b"".join(state.buffer)
        digest = hashlib.sha256(assembled).hexdigest()
        if digest != state.meta.sha256_hex:
            return b"error:sha mismatch"

        out_path = self._unique_path(state.meta.name)
        out_path.write_bytes(assembled)
        log.info(
            "Принят файл %s (%d байт)",
            out_path.name,
            len(assembled),
        )
        return b"ok"

    def _unique_path(self, name: str) -> Path:
        candidate = self._dir / name
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        suffix = candidate.suffix
        counter = 1
        while True:
            alt = self._dir / f"{stem}-{counter}{suffix}"
            if not alt.exists():
                return alt
            counter += 1