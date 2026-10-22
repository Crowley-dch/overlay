from __future__ import annotations

import logging

from overlay.config import Config
from overlay.dht.contact import Contact
from overlay.dht.lookup import IterativeLookup, LookupStats
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

from overlay.dht.record import (
    NodeRecord,
    RecordError,
    record_key_for_alias,
    record_key_for_node,
)
from overlay.dht.store import RecordConflict, RecordStore
from overlay.dht.wire import (
    WireError,
    decode_find_node_request,
    decode_find_node_response,
    decode_find_value_request,
    decode_find_value_response,
    decode_store_request,
    decode_store_response,
    encode_find_node_request,
    encode_find_node_response,
    encode_find_value_request,
    encode_find_value_response,
    encode_store_request,
    encode_store_response,
)

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

        self._store = RecordStore(
            max_record_size=config.ttl.max_record_size,
            default_ttl=config.ttl.record_ttl,
        )

    @property
    def identity(self) -> Identity:
        return self._identity

    @property
    def node_id(self) -> bytes:
        return self._identity.node_id

    @property
    def store(self) -> RecordStore:
        return self._store
    
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
                    payload=encode_find_node_request(
                        target, self.self_contact()
                    ),
                    expected_response_types={
                        MessageType.FIND_NODE_RESPONSE
                    },
                )
                return decode_find_node_response(response.payload)
            finally:
                await rpc.stop()
        finally:
            conn.close()
            await conn.wait_closed()
    async def _find_node_rpc(
        self, contact: Contact, target: bytes
    ) -> list[Contact]:
        return await self.find_node(target, contact)

    async def lookup_node(
        self, target: bytes
    ) -> tuple[list[Contact], LookupStats]:
        initial = self._routing.routing.closest(target, self._config.dht.k)
        lookup = IterativeLookup(
            self_id=self._identity.node_id,
            find_node_fn=self._find_node_rpc,
            initial_candidates=initial,
            alpha=self._config.dht.alpha,
            k=self._config.dht.k,
        )
        result, stats = await lookup.run(target)
        for c in result:
            if c.node_id == self._identity.node_id:
                continue
            try:
                await self._routing.add_contact(c)
            except ValueError:
                pass
        return result, stats

    async def bootstrap(self, seeds: list[Contact]) -> list[Contact]:
        for seed in seeds:
            if seed.node_id == self._identity.node_id:
                continue
            alive = await self.ping(seed)
            if not alive:
                log.info(
                    "Bootstrap seed %s недоступен",
                    seed.node_id_hex[:12],
                )
                continue
            try:
                await self._routing.add_contact(seed)
            except ValueError:
                continue
        result, _ = await self.lookup_node(self._identity.node_id)
        return result
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
            MessageType.STORE_REQUEST: self._on_store,
            MessageType.FIND_VALUE_REQUEST: self._on_find_value,
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
        target, sender = decode_find_node_request(frame.payload)
        if sender.node_id != self._identity.node_id:
            try:
                await self._routing.add_contact(sender)
            except ValueError:
                pass
        contacts = self._routing.routing.closest(target, self._routing.k)
        await conn.send(
            Frame(
                msg_type=MessageType.FIND_NODE_RESPONSE,
                request_id=frame.request_id,
                payload=encode_find_node_response(contacts),
                flags=FrameFlags.IS_RESPONSE,
            )
        )
    async def _on_store(self, conn: Connection, frame: Frame) -> None:
        try:
            key, record_json = decode_store_request(frame.payload)
            record = NodeRecord._from_dict(record_json)
            self._store.put(key, record)
        except (WireError, RecordError, RecordConflict) as e:
            await conn.send(
                Frame(
                    msg_type=MessageType.STORE_RESPONSE,
                    request_id=frame.request_id,
                    payload=encode_store_response(False, str(e)),
                    flags=FrameFlags.IS_RESPONSE,
                )
            )
            return
        await conn.send(
            Frame(
                msg_type=MessageType.STORE_RESPONSE,
                request_id=frame.request_id,
                payload=encode_store_response(True),
                flags=FrameFlags.IS_RESPONSE,
            )
        )

    async def _on_find_value(self, conn: Connection, frame: Frame) -> None:
        try:
            key = decode_find_value_request(frame.payload)
        except WireError as e:
            await self._send_error(conn, frame, str(e))
            return
        record = self._store.get(key)
        contacts = self._routing.routing.closest(key, self._routing.k)
        if record is not None:
            await conn.send(
                Frame(
                    msg_type=MessageType.FIND_VALUE_RESPONSE,
                    request_id=frame.request_id,
                    payload=encode_find_value_response(
                        record.to_dict(), contacts
                    ),
                    flags=FrameFlags.IS_RESPONSE,
                )
            )
        else:
            await conn.send(
                Frame(
                    msg_type=MessageType.FIND_VALUE_RESPONSE,
                    request_id=frame.request_id,
                    payload=encode_find_value_response(None, contacts),
                    flags=FrameFlags.IS_RESPONSE,
                )
            )

    async def _store_rpc(
        self, contact: Contact, key: bytes, record: NodeRecord
    ) -> bool:
        conn = await Connection.connect(
            contact.host,
            contact.port,
            connect_timeout=self._config.transport.connect_timeout,
        )
        try:
            rpc = RpcClient(
                conn, default_timeout=self._config.transport.connect_timeout
            )
            await rpc.start()
            try:
                response = await rpc.request(
                    MessageType.STORE_REQUEST,
                    payload=encode_store_request(key, record.to_dict()),
                    expected_response_types={MessageType.STORE_RESPONSE},
                )
                stored, _ = decode_store_response(response.payload)
                return stored
            finally:
                await rpc.stop()
        finally:
            conn.close()
            await conn.wait_closed()

    async def _find_value_rpc(
        self, contact: Contact, key: bytes
    ) -> tuple[NodeRecord | None, list[Contact]]:
        conn = await Connection.connect(
            contact.host,
            contact.port,
            connect_timeout=self._config.transport.connect_timeout,
        )
        try:
            rpc = RpcClient(
                conn, default_timeout=self._config.transport.connect_timeout
            )
            await rpc.start()
            try:
                response = await rpc.request(
                    MessageType.FIND_VALUE_REQUEST,
                    payload=encode_find_value_request(key),
                    expected_response_types={
                        MessageType.FIND_VALUE_RESPONSE
                    },
                )
                record_json, contacts = decode_find_value_response(
                    response.payload
                )
                record = (
                    NodeRecord._from_dict(record_json)
                    if record_json is not None
                    else None
                )
                return record, contacts
            finally:
                await rpc.stop()
        finally:
            conn.close()
            await conn.wait_closed()

    async def put_record(
        self,
        record: NodeRecord,
        *,
        replication_factor: int | None = None,
    ) -> int:
        r = replication_factor or self._config.dht.replication_factor
        if record.alias is not None:
            key = record_key_for_alias(record.alias)
        else:
            key = record_key_for_node(record.node_id)

        self._store.put(key, record)

        candidates, _ = await self.lookup_node(key)
        holders = [c for c in candidates if c.node_id != self._identity.node_id]
        holders = holders[:r]

        successes = 1
        for contact in holders:
            try:
                ok = await self._store_rpc(contact, key, record)
                if ok:
                    successes += 1
            except Exception:
                continue

        log.info(
            "put_record key=%s seq=%d реплик=%d/%d",
            key.hex()[:12],
            record.sequence_number,
            successes,
            1 + len(holders),
        )
        if successes < 2:
            raise RuntimeError(
                f"STORE получил только {successes} подтверждений, нужно ≥ 2"
            )
        return successes

    async def get_record(self, key: bytes) -> NodeRecord | None:
        local = self._store.get(key)
        if local is not None:
            return local

        from overlay.dht.xor import xor_distance_int
        import asyncio as _asyncio

        pool: dict[bytes, Contact] = {}
        queried: set[bytes] = set()
        for c in self._routing.routing.closest(key, self._config.dht.k):
            if c.node_id != self._identity.node_id:
                pool[c.node_id] = c

        while True:
            candidates = [
                c for nid, c in pool.items() if nid not in queried
            ]
            if not candidates:
                break
            candidates.sort(key=lambda c: xor_distance_int(c.node_id, key))
            batch = candidates[: self._config.dht.alpha]
            results = await _asyncio.gather(
                *(self._find_value_rpc(c, key) for c in batch),
                return_exceptions=True,
            )
            for c, r in zip(batch, results):
                queried.add(c.node_id)
                if isinstance(r, BaseException):
                    continue
                record, closer = r
                if record is not None:
                    try:
                        record.validate(
                            max_size=self._config.ttl.max_record_size
                        )
                    except RecordError:
                        continue
                    self._store.put(key, record)
                    return record
                for cc in closer:
                    if cc.node_id == self._identity.node_id:
                        continue
                    if cc.node_id not in pool and cc.node_id not in queried:
                        pool[cc.node_id] = cc
        return None
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