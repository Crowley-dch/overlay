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
    ) -> None:
        self._node = node
        self.tunnel_id = tunnel_id
        self.destination = destination
        self._session = session
        self._closed = False

    @property
    def session_id(self) -> bytes:
        return self._session.session_id

    @property
    def peer_node_id(self) -> bytes:
        return self._session.peer_node_id

    async def send_message(self, payload: bytes) -> bytes:
        if self._closed:
            raise SecureTunnelError("SecureTunnel закрыт")

        message_id = secrets.token_bytes(16)
        inner = Frame(
            msg_type=MessageType.APP_MESSAGE,
            request_id=message_id,
            payload=payload,
        ).encode()

        sid = self._session.session_id
        pre = self._session.send_aead.send_counter
        aad = sid + pre.to_bytes(8, "big")
        counter, ct = self._session.send_aead.encrypt(inner, aad=aad)
        wrapped = sid + counter.to_bytes(8, "big") + ct

        ack_data = await self._node.send_via_tunnel(
            self.tunnel_id, wrapped, encrypted=True
        )

        if len(ack_data) < 24:
            raise SecureTunnelError("ACK слишком короткий")
        ack_sid = ack_data[:16]
        ack_counter = int.from_bytes(ack_data[16:24], "big")
        ack_ct = ack_data[24:]
        if ack_sid != sid:
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

    def close(self) -> None:
        self._closed = True