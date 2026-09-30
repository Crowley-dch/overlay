from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from overlay.transport.connection import (
    Connection,
    ConnectionClosed,
    ConnectionTimeout,
)
from overlay.transport.frame import Frame, FrameFlags, MessageType

log = logging.getLogger(__name__)

ConnectionHandler = Callable[[Connection], Awaitable[None]]


class Server:
    
    def __init__(
        self,
        host: str,
        port: int,
        handler: ConnectionHandler,
    ) -> None:
        self.host = host
        self.port = port
        self._handler = handler
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._on_client, host=self.host, port=self.port
        )
        sockets = self._server.sockets or []
        if sockets:
            self.port = sockets[0].getsockname()[1]
        log.info("Сервер слушает %s:%d", self.host, self.port)

    async def stop(self, timeout: float = 2.0) -> None:
        server = self._server
        self._server = None

        if server is not None:
            server.close()

        if self._tasks:
            for task in list(self._tasks):
                task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                log.warning("Некоторые задачи не завершились за %ss", timeout)
            self._tasks.clear()

        if server is not None:
            try:
                await asyncio.wait_for(server.wait_closed(), timeout=timeout)
            except asyncio.TimeoutError:
                log.warning("Server.wait_closed() не завершился за %ss", timeout)
    async def _on_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        conn = Connection.from_accepted(reader, writer)
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            await self._handler(conn)
        except ConnectionClosed:
            log.info("Соединение %s закрыто", conn.connection_id)
        except ConnectionTimeout:
            log.warning("Тайм-аут на %s", conn.connection_id)
        except Exception:
            log.exception("Ошибка в обработчике %s", conn.connection_id)
        finally:
            conn.close()
            await conn.wait_closed()
            if task is not None:
                self._tasks.discard(task)


async def echo_ping_handler(conn: Connection) -> None:
    log.info("Новое соединение: %s", conn)
    try:
        while True:
            frame = await conn.recv()
            if frame.msg_type == MessageType.PING:
                pong = Frame(
                    msg_type=MessageType.PONG,
                    request_id=frame.request_id,
                    payload=frame.payload,
                    flags=FrameFlags.IS_RESPONSE,
                )
                await conn.send(pong)
            else:
                log.warning("Неизвестный тип: %s", frame.msg_type)
    except ConnectionClosed:
        log.info("Соединение %s закрыто пиром", conn.connection_id)
        raise