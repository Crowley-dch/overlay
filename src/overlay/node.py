from __future__ import annotations

import logging
import time
import secrets
import asyncio
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
from overlay.transport.secure import SecureConnection, SecureConnectionError
from overlay.transport.types import TransportLike
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
from overlay.tunnel import (
    RelayState,
    TunnelRoute,
    TunnelState,
    TunnelWireError,
    decode_tunnel_build,
    decode_tunnel_build_fail,
    decode_tunnel_build_ok,
    decode_tunnel_close,
    decode_tunnel_data,
    encode_tunnel_ack,
    encode_tunnel_build,
    encode_tunnel_build_fail,
    encode_tunnel_build_ok,
    encode_tunnel_close,
    encode_tunnel_data,
)
log = logging.getLogger(__name__)


class Node:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._tunnel_routes: dict[bytes, TunnelRoute] = {}
        self._relay_states: dict[bytes, RelayState] = {}
        self._identity_store = IdentityStore(config.node.state_dir)
        self._identity = self._identity_store.load_or_create()
        self._maintenance_task: asyncio.Task | None = None
        self._routing = ContactManager(
            self_id=self._identity.node_id,
            k=config.dht.k,
            connect_timeout=config.transport.connect_timeout,
            ping_timeout=config.transport.connect_timeout,
            ping_fn=self.ping,
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
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())
        log.info(
            "Node %s слушает %s:%d",
            self._identity.node_id_hex[:12],
            self._server.host,
            self._server.port,
        )

    async def stop(self) -> None:
        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except (asyncio.CancelledError, Exception):
                pass
            self._maintenance_task = None
        await self._server.stop()

    async def find_node(self, target: bytes, contact: Contact) -> list[Contact]:
        secure = await SecureConnection.initiate(
            contact.host,
            contact.port,
            self._identity,
            connect_timeout=self._config.transport.connect_timeout,
        )
        try:
            rpc = RpcClient(
                secure,
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
            secure.close()
            await secure.wait_closed()
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
            secure = await SecureConnection.initiate(
                contact.host,
                contact.port,
                self._identity,
                connect_timeout=self._config.transport.connect_timeout,
            )
        except (
            ConnectionTimeout,
            ConnectionClosed,
            SecureConnectionError,
            OSError,
        ):
            return False
        try:
            rpc = RpcClient(
                secure,
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
            secure.close()
            await secure.wait_closed()

    async def _handle_connection(self, conn: Connection) -> None:
        try:
            secure = await SecureConnection.accept(conn, self._identity)
        except Exception as e:
            log.warning(
                "Handshake failed from %s:%d: %s",
                conn.peer_host,
                conn.peer_port,
                e,
            )
            conn.close()
            try:
                await conn.wait_closed()
            except Exception:
                pass
            raise
        try:
            while True:
                try:
                    frame = await secure.recv()
                except ConnectionClosed:
                    raise
                except FrameError as e:
                    log.warning(
                        "Кадрирование от %s: %s", conn.connection_id, e
                    )
                    raise
                await self._dispatch(secure, frame)
        finally:
            secure.close()
            await secure.wait_closed()
    async def _dispatch(
        self, conn: TransportLike, frame: Frame
    ) -> None:
        handler = {
            MessageType.PING: self._on_ping,
            MessageType.FIND_NODE_REQUEST: self._on_find_node,
            MessageType.STORE_REQUEST: self._on_store,
            MessageType.FIND_VALUE_REQUEST: self._on_find_value,
            MessageType.TUNNEL_BUILD: self._on_tunnel_build,
            MessageType.TUNNEL_DATA: self._on_tunnel_data,
            MessageType.TUNNEL_CLOSE: self._on_tunnel_close,
        }.get(MessageType(frame.msg_type))
        if handler is None:
            await self._send_error(
                conn, frame, f"unsupported type: {int(frame.msg_type)}"
            )
            return

        try:
            await handler(conn, frame)
        except WireError as e:
            log.warning(
                "Wire error from %s: %s", conn.connection_id, e
            )
            await self._send_error(conn, frame, str(e))
    async def _on_ping(
        self, conn: TransportLike, frame: Frame
    ) -> None:
        await conn.send(
            Frame(
                msg_type=MessageType.PONG,
                request_id=frame.request_id,
                payload=frame.payload,
                flags=FrameFlags.IS_RESPONSE,
            )
        )

    async def _on_find_node(
        self, conn: TransportLike, frame: Frame
    ) -> None:
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
    async def _on_store(
        self, conn: TransportLike, frame: Frame
    ) -> None:
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

    async def _on_find_value(
        self, conn: TransportLike, frame: Frame
    ) -> None:
        try:
            key = decode_find_value_request(frame.payload)
        except WireError as e:
            await self._send_error(conn, frame, str(e))
            return
        record = self._store.get(key)
        contacts = self._routing.routing.closest(key, self._routing.k)
        await conn.send(
            Frame(
                msg_type=MessageType.FIND_VALUE_RESPONSE,
                request_id=frame.request_id,
                payload=encode_find_value_response(
                    record.to_dict() if record is not None else None,
                    contacts,
                ),
                flags=FrameFlags.IS_RESPONSE,
            )
        )

    async def _store_rpc(
        self, contact: Contact, key: bytes, record: NodeRecord
    ) -> bool:
        secure = await SecureConnection.initiate(
            contact.host,
            contact.port,
            self._identity,
            connect_timeout=self._config.transport.connect_timeout,
        )
        try:
            rpc = RpcClient(
                secure,
                default_timeout=self._config.transport.connect_timeout,
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
            secure.close()
            await secure.wait_closed()

    async def _find_value_rpc(
        self, contact: Contact, key: bytes
    ) -> tuple[NodeRecord | None, list[Contact]]:
        secure = await SecureConnection.initiate(
            contact.host,
            contact.port,
            self._identity,
            connect_timeout=self._config.transport.connect_timeout,
        )
        try:
            rpc = RpcClient(
                secure,
                default_timeout=self._config.transport.connect_timeout,
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
            secure.close()
            await secure.wait_closed()

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
    async def _maintenance_loop(self) -> None:
        ttl_cfg = self._config.ttl
        cleanup_interval = max(1, min(60, ttl_cfg.record_ttl // 3))
        republish_interval = max(1, ttl_cfg.republish_interval)
        tick = 0.5

        last_cleanup = 0.0
        last_republish = 0.0

        try:
            while True:
                now = asyncio.get_running_loop().time()

                if now - last_cleanup >= cleanup_interval:
                    removed = self._store.cleanup()
                    if removed:
                        log.info(
                            "cleanup: удалено %d истёкших записей", removed
                        )
                    last_cleanup = now

                if now - last_republish >= republish_interval:
                    await self._republish_own_records()
                    last_republish = now

                await asyncio.sleep(tick)
        except asyncio.CancelledError:
            raise
    async def _republish_own_records(self) -> None:
        now = int(time.time())
        ttl = self._config.ttl.record_ttl

        for key, record in self._store.items():
            if record.node_id != self._identity.node_id:
                continue
            refreshed = NodeRecord(
                node_id=record.node_id,
                identity_public_key=record.identity_public_key,
                addresses=list(record.addresses),
                sequence_number=record.sequence_number + 1,
                issued_at=now,
                expires_at=now + ttl,
                alias=record.alias,
            )
            refreshed.sign(self._identity.private_key)
            try:
                await self.put_record(refreshed)
                log.info(
                    "republish key=%s seq=%d",
                    key.hex()[:12],
                    refreshed.sequence_number,
                )
            except Exception as e:
                log.warning(
                    "republish key=%s: %s", key.hex()[:12], e
                )
    async def _send_error(
        self, conn: TransportLike, frame: Frame, message: str
    ) -> None:
        await conn.send(
            Frame(
                msg_type=MessageType.ERROR,
                request_id=frame.request_id,
                payload=message.encode("utf-8"),
                flags=FrameFlags.IS_RESPONSE | FrameFlags.IS_ERROR,
            )
        )
    def _select_relays(
        self, destination: Contact, count: int
    ) -> list[Contact]:
        candidates = [
            c
            for c in self._routing.routing.all_contacts()
            if c.node_id != self._identity.node_id
            and c.node_id != destination.node_id
        ]
        return candidates[:count]

    async def _send_tunnel_build(
        self,
        target: Contact,
        tunnel_id: bytes,
        prev_hop: Contact | None,
        next_hop: Contact | None,
        max_hops: int,
        ttl: int,
    ) -> bool:
        try:
            secure = await SecureConnection.initiate(
                target.host,
                target.port,
                self._identity,
                connect_timeout=self._config.transport.connect_timeout,
            )
        except (
            ConnectionTimeout,
            ConnectionClosed,
            SecureConnectionError,
            OSError,
        ):
            return False
        try:
            rpc = RpcClient(
                secure,
                default_timeout=self._config.transport.connect_timeout,
            )
            await rpc.start()
            try:
                response = await rpc.request(
                    MessageType.TUNNEL_BUILD,
                    payload=encode_tunnel_build(
                        tunnel_id,
                        prev_hop=prev_hop,
                        next_hop=next_hop,
                        max_hops=max_hops,
                        ttl=ttl,
                    ),
                    expected_response_types={
                        MessageType.TUNNEL_BUILD_OK,
                        MessageType.TUNNEL_BUILD_FAIL,
                    },
                )
                if response.msg_type == MessageType.TUNNEL_BUILD_OK:
                    return True
                return False
            except (RequestTimeout, ConnectionBroken):
                return False
            finally:
                await rpc.stop()
        finally:
            secure.close()
            await secure.wait_closed()

    async def build_tunnel(
        self,
        destination: Contact,
        relay_count: int = 3,
        ttl: int = 300,
    ) -> TunnelRoute:
        if relay_count < 1:
            raise ValueError("нужен хотя бы один ретранслятор")
        relays = self._select_relays(destination, relay_count)
        if len(relays) < relay_count:
            raise RuntimeError(
                f"недостаточно ретрансляторов: {len(relays)} < {relay_count}"
            )
        tunnel_id = secrets.token_bytes(16)
        hops = [self.self_contact()] + relays + [destination]
        route = TunnelRoute(
            tunnel_id=tunnel_id,
            hops=hops,
            state=TunnelState.BUILDING,
            max_hops=len(hops),
            expires_at=time.time() + ttl,
        )

        build_tasks = []
        for i in range(1, len(hops)):
            hop = hops[i]
            prev_hop = hops[i - 1]
            next_hop = hops[i + 1] if i + 1 < len(hops) else None
            build_tasks.append(
                self._send_tunnel_build(
                    hop, tunnel_id, prev_hop, next_hop, len(hops), ttl
                )
            )
        results = await asyncio.gather(*build_tasks, return_exceptions=True)
        ok_count = sum(1 for r in results if r is True)

        if ok_count == len(hops) - 1:
            route.state = TunnelState.ACTIVE
            self._tunnel_routes[tunnel_id] = route
            log.info(
                "Туннель %s построен: %d ретрансляторов",
                tunnel_id.hex()[:12],
                route.relay_count,
            )
        else:
            route.state = TunnelState.DEAD
            await self._send_tunnel_close_to_hops(hops[1:], tunnel_id)
            log.warning(
                "Туннель %s не построен: %d/%d подтверждений",
                tunnel_id.hex()[:12],
                ok_count,
                len(hops) - 1,
            )
        return route

    async def _send_tunnel_close_to_hops(
        self, hops: list[Contact], tunnel_id: bytes
    ) -> None:
        for hop in hops:
            try:
                secure = await SecureConnection.initiate(
                    hop.host,
                    hop.port,
                    self._identity,
                    connect_timeout=self._config.transport.connect_timeout,
                )
            except Exception:
                continue
            try:
                await secure.send(
                    Frame(
                        msg_type=MessageType.TUNNEL_CLOSE,
                        request_id=secrets.token_bytes(16),
                        payload=encode_tunnel_close(tunnel_id),
                    )
                )
            except Exception:
                pass
            finally:
                secure.close()
                await secure.wait_closed()

    async def send_via_tunnel(
        self,
        tunnel_id: bytes,
        payload: bytes,
        *,
        timeout: float = 30.0,
    ) -> bytes:
        route = self._tunnel_routes.get(tunnel_id)
        if route is None:
            raise RuntimeError("Неизвестный tunnel_id")
        if route.state != TunnelState.ACTIVE:
            raise RuntimeError(f"Туннель в состоянии {route.state.value}")

        message_id = secrets.token_bytes(16)
        first_relay = route.hops[1]

        secure = await SecureConnection.initiate(
            first_relay.host,
            first_relay.port,
            self._identity,
            connect_timeout=self._config.transport.connect_timeout,
        )
        try:
            rpc = RpcClient(secure, default_timeout=timeout)
            await rpc.start()
            try:
                response = await rpc.request(
                    MessageType.TUNNEL_DATA,
                    payload=encode_tunnel_data(
                        tunnel_id, message_id, payload
                    ),
                    expected_response_types={MessageType.TUNNEL_ACK},
                )
                ack_tunnel_id, ack_message_id, ack_payload = decode_tunnel_data(
                    response.payload
                )
                if ack_tunnel_id != tunnel_id:
                    raise RuntimeError("ACK с чужим tunnel_id")
                if ack_message_id != message_id:
                    raise RuntimeError("ACK с чужим message_id")
                return ack_payload
            finally:
                await rpc.stop()
        finally:
            secure.close()
            await secure.wait_closed()

    async def close_tunnel(self, tunnel_id: bytes) -> None:
        route = self._tunnel_routes.pop(tunnel_id, None)
        if route is None:
            return
        route.state = TunnelState.CLOSING
        await self._send_tunnel_close_to_hops(route.hops[1:], tunnel_id)
        route.state = TunnelState.DEAD

    async def _on_tunnel_build(
        self, conn: TransportLike, frame: Frame
    ) -> None:
        try:
            data = decode_tunnel_build(frame.payload)
        except TunnelWireError as e:
            await self._send_error(conn, frame, str(e))
            return
        tunnel_id = data["tunnel_id"]
        if tunnel_id in self._relay_states:
            await conn.send(
                Frame(
                    msg_type=MessageType.TUNNEL_BUILD_FAIL,
                    request_id=frame.request_id,
                    payload=encode_tunnel_build_fail(
                        tunnel_id, "duplicate tunnel_id"
                    ),
                    flags=FrameFlags.IS_RESPONSE,
                )
            )
            return
        self._relay_states[tunnel_id] = RelayState(
            tunnel_id=tunnel_id,
            prev_hop=data["prev_hop"],
            next_hop=data["next_hop"],
            max_hops=data["max_hops"],
            expires_at=time.time() + data["ttl"],
        )
        log.info(
            "Relay %s: tunnel %s accepted (prev=%s, next=%s)",
            self._identity.node_id_hex[:12],
            tunnel_id.hex()[:12],
            data["prev_hop"].node_id_hex[:8] if data["prev_hop"] else None,
            data["next_hop"].node_id_hex[:8] if data["next_hop"] else None,
        )
        await conn.send(
            Frame(
                msg_type=MessageType.TUNNEL_BUILD_OK,
                request_id=frame.request_id,
                payload=encode_tunnel_build_ok(tunnel_id),
                flags=FrameFlags.IS_RESPONSE,
            )
        )

    async def _on_tunnel_data(
        self, conn: TransportLike, frame: Frame
    ) -> None:
        try:
            tunnel_id, message_id, payload = decode_tunnel_data(
                frame.payload
            )
        except TunnelWireError as e:
            await self._send_error(conn, frame, str(e))
            return

        relay = self._relay_states.get(tunnel_id)
        if relay is None:
            await self._send_error(
                conn, frame, "unknown tunnel_id"
            )
            return

        if relay.is_destination:
            response_payload = b"delivered:" + payload[:16]
            await conn.send(
                Frame(
                    msg_type=MessageType.TUNNEL_ACK,
                    request_id=frame.request_id,
                    payload=encode_tunnel_ack(
                        tunnel_id, message_id, response_payload
                    ),
                    flags=FrameFlags.IS_RESPONSE,
                )
            )
            return

        next_hop = relay.next_hop
        assert next_hop is not None
        try:
            secure = await SecureConnection.initiate(
                next_hop.host,
                next_hop.port,
                self._identity,
                connect_timeout=self._config.transport.connect_timeout,
            )
        except Exception as e:
            await self._send_error(
                conn, frame, f"relay to {next_hop.node_id_hex[:8]}: {e}"
            )
            return
        try:
            rpc = RpcClient(
                secure, default_timeout=self._config.transport.connect_timeout
            )
            await rpc.start()
            try:
                response = await rpc.request(
                    MessageType.TUNNEL_DATA,
                    payload=encode_tunnel_data(
                        tunnel_id, message_id, payload
                    ),
                    expected_response_types={MessageType.TUNNEL_ACK},
                )
                await conn.send(
                    Frame(
                        msg_type=MessageType.TUNNEL_ACK,
                        request_id=frame.request_id,
                        payload=response.payload,
                        flags=FrameFlags.IS_RESPONSE,
                    )
                )
            except (RequestTimeout, ConnectionBroken) as e:
                await self._send_error(conn, frame, f"relay: {e}")
            finally:
                await rpc.stop()
        finally:
            secure.close()
            await secure.wait_closed()

    async def _on_tunnel_close(
        self, conn: TransportLike, frame: Frame
    ) -> None:
        try:
            tunnel_id = decode_tunnel_close(frame.payload)
        except TunnelWireError:
            return
        relay = self._relay_states.pop(tunnel_id, None)
        route = self._tunnel_routes.pop(tunnel_id, None)
        if relay is None and route is None:
            return
        log.info("Tunnel %s closed", tunnel_id.hex()[:12])