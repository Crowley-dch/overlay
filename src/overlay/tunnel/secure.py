from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from overlay.crypto.handshake import SecureSession
from overlay.transport.frame import Frame, MessageType

if TYPE_CHECKING:
    from overlay.dht.contact import Contact
    from overlay.node import Node


class SecureTunnelError(Exception):
    pass


class SecureTunnel:
    def __init__(
        self,
        node: "Node",
        tunnel_id: bytes,
        destination: "Contact",
        session: SecureSession,
        relay_count: int,
    ) -> None:
        self._node = node
        self._tunnel_id = tunnel_id
        self.destination = destination
        self._session = session
        self._relay_count = relay_count
        self._closed = False

    @property
    def tunnel_id(self) -> bytes:
        return self._tunnel_id

    @property
    def session_id(self) -> bytes:
        return self._session.session_id

    @property
    def peer_node_id(self) -> bytes:
        return self._session.peer_node_id

    def _wrap(self, message_id: bytes, payload: bytes) -> bytes:
        inner = Frame(
            msg_type=MessageType.APP_MESSAGE,
            request_id=message_id,
            payload=payload,
        ).encode()
        sid = self._session.session_id
        pre = self._session.send_aead.send_counter
        aad = sid + pre.to_bytes(8, "big")
        counter, ct = self._session.send_aead.encrypt(inner, aad=aad)
        return sid + counter.to_bytes(8, "big") + ct

    def _unwrap_ack(self, ack_data: bytes, message_id: bytes) -> bytes:
        if len(ack_data) < 24:
            raise SecureTunnelError("ACK слишком короткий")
        ack_sid = ack_data[:16]
        ack_counter = int.from_bytes(ack_data[16:24], "big")
        ack_ct = ack_data[24:]
        if ack_sid != self._session.session_id:
            raise SecureTunnelError("session_id не совпадает в ACK")
        ack_aad = ack_sid + ack_counter.to_bytes(8, "big")
        inner_bytes = self._session.recv_aead.decrypt(
            ack_counter, ack_ct, aad=ack_aad
        )
        inner_frame = Frame.decode(inner_bytes)
        if inner_frame.msg_type != MessageType.APP_ACK:
            raise SecureTunnelError(
                f"Ожидался APP_ACK, получен {inner_frame.msg_type}"
            )
        if inner_frame.request_id != message_id:
            raise SecureTunnelError("message_id не совпадает в ACK")
        return inner_frame.payload

    async def _attempt(self, message_id: bytes, payload: bytes) -> bytes:
        wrapped = self._wrap(message_id, payload)
        ack_data = await self._node.send_via_tunnel(
            self._tunnel_id, wrapped, encrypted=True
        )
        return self._unwrap_ack(ack_data, message_id)

    async def _recover(self) -> None:
        failed = await self._node.detect_failed_relays(self._tunnel_id)
        self._node.mark_relays_failed(failed)
        try:
            await self._node.close_tunnel(self._tunnel_id)
        except Exception:
            pass
        new_tunnel = await self._node.open_secure_tunnel(
            self.destination,
            relay_count=self._relay_count,
            exclude=set(failed),
        )
        self._tunnel_id = new_tunnel._tunnel_id
        self._session = new_tunnel._session
        new_tunnel._closed = True

    async def send_message(self, payload: bytes) -> bytes:
        if self._closed:
            raise SecureTunnelError("SecureTunnel закрыт")
        message_id = secrets.token_bytes(16)
        try:
            return await self._attempt(message_id, payload)
        except Exception:
            if self._closed:
                raise
            await self._recover()
            return await self._attempt(message_id, payload)

    def close(self) -> None:
        self._closed = True