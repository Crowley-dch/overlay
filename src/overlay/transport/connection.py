from __future__ import annotations

import asyncio
import uuid

from overlay.transport.frame import (
    Frame,
    FrameBuffer,
    FrameError,
    MessageType,
)

DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 10.0
DEFAULT_WRITE_TIMEOUT = 5.0


class ConnectionClosed(Exception):
    pass


class ConnectionTimeout(Exception):
    pass


class Connection:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        write_timeout: float = DEFAULT_WRITE_TIMEOUT,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._buffer = FrameBuffer()
        self._pending: list[Frame] = []
        self._read_timeout = read_timeout
        self._write_timeout = write_timeout
        self._connect_timeout = connect_timeout
        self._closed = False

        self.connection_id: str = uuid.uuid4().hex[:12]
        peer = writer.get_extra_info("peername") or ("?", 0)
        self.peer_host: str = peer[0]
        self.peer_port: int = peer[1]

    @classmethod
    async def connect(
        cls,
        host: str,
        port: int,
        **kwargs,
    ) -> "Connection":
        connect_timeout = kwargs.get("connect_timeout", DEFAULT_CONNECT_TIMEOUT)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=connect_timeout,
            )
        except asyncio.TimeoutError as e:
            raise ConnectionTimeout(
                f"Не удалось подключиться к {host}:{port} за {connect_timeout}s"
            ) from e
        return cls(reader, writer, **kwargs)

    @classmethod
    def from_accepted(
        cls,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        **kwargs,
    ) -> "Connection":
        return cls(reader, writer, **kwargs)

    async def send(self, frame: Frame) -> None:
        if self._closed:
            raise ConnectionClosed("Соединение закрыто")
        try:
            self._writer.write(frame.encode())
            await asyncio.wait_for(
                self._writer.drain(), timeout=self._write_timeout
            )
        except asyncio.TimeoutError as e:
            raise ConnectionTimeout("Тайм-аут записи") from e
        except (ConnectionError, OSError) as e:
            self._closed = True
            raise ConnectionClosed(str(e)) from e

    async def recv(self) -> Frame:
        if self._closed:
            raise ConnectionClosed("Соединение закрыто")

        if self._pending:
            return self._pending.pop(0)

        while True:
            try:
                data = await self._reader.read(4096)
            except (ConnectionError, OSError) as e:
                self._closed = True
                raise ConnectionClosed(str(e)) from e

            if not data:
                self._closed = True
                raise ConnectionClosed("Пир закрыл соединение")

            try:
                frames = self._buffer.feed(data)
            except FrameError as e:
                self._closed = True
                raise ConnectionClosed(f"Ошибка кадрирования: {e}") from e

            if frames:
                self._pending.extend(frames[1:])
                return frames[0]
    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._writer.close()
            except Exception:
                pass

    async def wait_closed(self) -> None:
        try:
            await self._writer.wait_closed()
        except Exception:
            pass

    @property
    def is_closed(self) -> bool:
        return self._closed

    def __repr__(self) -> str:
        return (
            f"<Connection {self.connection_id} "
            f"{self.peer_host}:{self.peer_port} "
            f"{'closed' if self._closed else 'open'}>"
        )