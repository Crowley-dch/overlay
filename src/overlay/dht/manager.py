from __future__ import annotations

import logging
from typing import Awaitable, Callable

from overlay.dht.contact import Contact
from overlay.dht.kbucket import AddResult
from overlay.dht.routing import RoutingTable
from overlay.dht.xor import bucket_index
from overlay.transport.connection import (
    Connection,
    ConnectionClosed,
    ConnectionTimeout,
)
from overlay.transport.frame import MessageType
from overlay.transport.rpc import (
    ConnectionBroken,
    RequestTimeout,
    RpcClient,
)

log = logging.getLogger(__name__)

PingFn = Callable[[Contact], Awaitable[bool]]


class ContactManager:
    def __init__(
        self,
        self_id: bytes,
        k: int,
        *,
        connect_timeout: float = 2.0,
        ping_timeout: float = 2.0,
        ping_fn: PingFn | None = None,
    ) -> None:
        self._routing = RoutingTable(self_id, k)
        self._connect_timeout = connect_timeout
        self._ping_timeout = ping_timeout
        self._ping_fn = ping_fn

    @property
    def routing(self) -> RoutingTable:
        return self._routing

    @property
    def self_id(self) -> bytes:
        return self._routing.self_id

    @property
    def k(self) -> int:
        return self._routing.k

    async def ping(self, contact: Contact) -> bool:
        try:
            conn = await Connection.connect(
                contact.host,
                contact.port,
                connect_timeout=self._connect_timeout,
            )
        except (ConnectionTimeout, ConnectionClosed, OSError):
            return False

        try:
            rpc = RpcClient(conn, default_timeout=self._ping_timeout)
            await rpc.start()
            try:
                response = await rpc.request(
                    MessageType.PING,
                    expected_response_types={MessageType.PONG},
                )
                return response.msg_type == MessageType.PONG
            except (RequestTimeout, ConnectionBroken):
                return False
            finally:
                await rpc.stop()
        finally:
            conn.close()
            await conn.wait_closed()

    async def add_contact(self, contact: Contact) -> AddResult:
        if contact.node_id == self._routing.self_id:
            raise ValueError("Нельзя добавить себя в таблицу маршрутизации")

        result = self._routing.add(contact)
        if result != AddResult.FULL:
            return result

        idx = bucket_index(self._routing.self_id, contact.node_id)
        bucket = self._routing.bucket_by_index(idx)
        if bucket is None:
            return AddResult.FULL

        lru = bucket.least_recent()
        if lru is None:
            return AddResult.FULL

        if self._ping_fn is not None:
            alive = await self._ping_fn(lru)
        else:
            alive = await self.ping(lru)

        bucket = self._routing.bucket_by_index(idx)
        if bucket is None:
            return self._routing.add(contact)

        current_lru = bucket.least_recent()
        if current_lru is None or current_lru.node_id != lru.node_id:
            return self._routing.add(contact)

        if alive:
            self._routing.touch(lru.node_id)
            return AddResult.FULL

        self._routing.replace_lru(contact)
        return AddResult.ADDED

    async def close(self) -> None:
        return None