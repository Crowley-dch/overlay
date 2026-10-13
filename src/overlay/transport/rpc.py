from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field

from overlay.transport.connection import (
    Connection,
    ConnectionClosed,
    ConnectionTimeout,
)
from overlay.transport.frame import Frame, FrameError, MessageType


class RpcError(Exception):
    pass


class RequestTimeout(RpcError):
    pass


class ConnectionBroken(RpcError):
    pass


@dataclass
class _Pending:
    request_id: bytes
    future: asyncio.Future
    expected_types: frozenset[int] = field(default_factory=frozenset)
    deadline: float = 0.0


class RpcClient:
    def __init__(
        self,
        connection: Connection,
        *,
        default_timeout: float = 5.0,
        allowed_types: set[int] | None = None,
    ) -> None:
        self._conn = connection
        self._timeout = default_timeout
        self._pending: dict[bytes, _Pending] = {}
        self._allowed_types = allowed_types or {int(t) for t in MessageType}
        self._reader_task: asyncio.Task | None = None
        self._closed = False

    async def start(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._reader_loop())

    async def stop(self) -> None:
        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        for pending in list(self._pending.values()):
            if not pending.future.done():
                pending.future.set_exception(ConnectionBroken("RPC закрыт"))
        self._pending.clear()

    async def request(
        self,
        msg_type: int,
        payload: bytes = b"",
        *,
        expected_response_types: set[int] | None = None,
        timeout: float | None = None,
    ) -> Frame:
        if self._closed:
            raise ConnectionBroken("RPC закрыт")

        request_id = secrets.token_bytes(16)
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        deadline = loop.time() + (timeout if timeout is not None else self._timeout)

        self._pending[request_id] = _Pending(
            request_id=request_id,
            future=future,
            expected_types=frozenset(expected_response_types or ()),
            deadline=deadline,
        )

        frame = Frame(
            msg_type=msg_type,
            request_id=request_id,
            payload=payload,
        )
        try:
            await self._conn.send(frame)
        except (ConnectionClosed, ConnectionTimeout) as e:
            self._pending.pop(request_id, None)
            if not future.done():
                future.set_exception(ConnectionBroken(str(e)))
            raise ConnectionBroken(str(e)) from e

        try:
            return await asyncio.wait_for(
                future,
                timeout=timeout if timeout is not None else self._timeout,
            )
        except asyncio.TimeoutError as e:
            self._pending.pop(request_id, None)
            raise RequestTimeout(
                f"Нет ответа на request_id={request_id.hex()[:8]}"
            ) from e

    async def _reader_loop(self) -> None:
        try:
            while True:
                frame = await self._conn.recv()
                self._dispatch(frame)
        except asyncio.CancelledError:
            raise
        except (ConnectionClosed, ConnectionTimeout) as e:
            self._fail_all(ConnectionBroken(str(e)))
        except FrameError as e:
            self._fail_all(ConnectionBroken(f"Кадрирование: {e}"))
        except Exception as e:
            self._fail_all(ConnectionBroken(f"Неожиданная ошибка: {e}"))

    def _dispatch(self, frame: Frame) -> None:
        pending = self._pending.pop(frame.request_id, None)
        if pending is None:
            return
        if pending.expected_types and int(frame.msg_type) not in pending.expected_types:
            return
        if not pending.future.done():
            pending.future.set_result(frame)

    def _fail_all(self, exc: Exception) -> None:
        for pending in list(self._pending.values()):
            if not pending.future.done():
                pending.future.set_exception(exc)
        self._pending.clear()