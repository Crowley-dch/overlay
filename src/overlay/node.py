from __future__ import annotations

import logging

from overlay.config import Config
from overlay.dht.contact import Contact
from overlay.dht.manager import ContactManager
from overlay.dht.wire import (
    WireError,
    decode_find_node_request,
    decode_find_node_response,
    encode_find_node_request,
    encode_find_node_response,
)
from overlay.identity import Identity, IdentityStore
from overlay.transport.connection import (
    Connection,
    ConnectionClosed,
    ConnectionTimeout,
)
from overlay.transport.frame import (
    Frame,
    FrameError,
    FrameFlags,
    MessageType,
)
from overlay.transport.rpc import (
    ConnectionBroken,
    RequestTimeout,
    RpcClient,
)
from overlay.transport.server import Server

log = logging.getLogger(__name__)


class Node:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._identity_store = IdentityStore(config.node.state_dir)
        self._identity = self._identity_store.load_or_create()

        self._routing = ContactManager(
            self_id=self._identity.node_id,
            k=config.dht.k,
            connect_timeout=config.transport.connect_timeout,
            ping_timeout=config.transport.connect_timeout,
        )

        self._server = Server(
            host=config.node.host,
            port=config.node.port,
            handler=self._handle_connection,
        )

    @property
    def identity(self) -> Identity:
        return self._identity

    @property
    def node_id(self) -> bytes:
        return self._identity.node_id

    @property
    def host(self) -> str:
        return self._server.host

    @property
    def port(self) -> int:
        return self._server.port

    @property
    def routing(self) -> ContactManager:
        return self._routing

    def self_contact(self) -> Contact:
        return Contact(
            node_id=self._identity.node_id,
            host=self._server.host,
            port=self._server.port,
        )

    async def start(self) -> None:
        await self._server.start()
        log.info(
            "Node %s слушает %s:%d",
            self._identity.node_id_hex[:12],
            self._server.host,
            self._server.port,
        )

    async def stop(self) -> None:
        await self._server.stop()

    async def find_node(self, target: bytes, contact: Contact) -> list[Contact]:
        conn = await Connection.connect(
            contact.host,
            contact.port,
            connect_timeout=self._config.transport.connect_timeout,
        )
        try:
            rpc = RpcClient(
                conn,
                default_timeout=self._config.transport.connect_timeout,
            )
            await rpc.start()
            try:
                response = await rpc.request(
                    MessageType.FIND_NODE_REQUEST,
                    payload=encode_find_node_request(target),
                    expected_response_types={MessageType.FIND_NODE_RESPONSE},
                )
                return decode_find_node_response(response.payload)
            finally:
                await rpc.stop()
        finally:
            conn.close()
            await conn.wait_closed()

    async def ping(self, contact: Contact) -> bool:
        try:
            conn = await Connection.connect(
                contact.host,
                contact.port,
                connect_timeout=self._config.transport.connect_timeout,
            )
        except (ConnectionTimeout, ConnectionClosed, OSError):
            return False
        try:
            rpc = RpcClient(
                conn,
                default_timeout=self._config.transport.connect_timeout,
            )
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

    async def _handle_connection(self, conn: Connection) -> None:
        while True:
            try:
                frame = await conn.recv()
            except ConnectionClosed:
                raise
            except FrameError as e:
                log.warning(
                    "Кадрирование от %s: %s", conn.connection_id, e
                )
                raise
            await self._dispatch(conn, frame)

    async def _dispatch(self, conn: Connection, frame: Frame) -> None:
        handler = {
            MessageType.PING: self._on_ping,
            MessageType.FIND_NODE_REQUEST: self._on_find_node,
        }.get(MessageType(frame.msg_type))

        if handler is None:
            await self._send_error(
                conn, frame, f"unsupported type: {int(frame.msg_type)}"
            )
            return

        try:
            await handler(conn, frame)
        except WireError as e:
            log.warning("Wire error from %s: %s", conn.connection_id, e)
            await self._send_error(conn, frame, str(e))

    async def _on_ping(self, conn: Connection, frame: Frame) -> None:
        await conn.send(
            Frame(
                msg_type=MessageType.PONG,
                request_id=frame.request_id,
                payload=frame.payload,
                flags=FrameFlags.IS_RESPONSE,
            )
        )

    async def _on_find_node(self, conn: Connection, frame: Frame) -> None:
        target = decode_find_node_request(frame.payload)
        contacts = self._routing.routing.closest(target, self._routing.k)
        await conn.send(
            Frame(
                msg_type=MessageType.FIND_NODE_RESPONSE,
                request_id=frame.request_id,
                payload=encode_find_node_response(contacts),
                flags=FrameFlags.IS_RESPONSE,
            )
        )

    async def _send_error(
        self, conn: Connection, frame: Frame, message: str
    ) -> None:
        await conn.send(
            Frame(
                msg_type=MessageType.ERROR,
                request_id=frame.request_id,
                payload=message.encode("utf-8"),
                flags=FrameFlags.IS_RESPONSE | FrameFlags.IS_ERROR,
            )
        )