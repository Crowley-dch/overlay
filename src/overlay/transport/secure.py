from __future__ import annotations

import logging
import secrets

from overlay.crypto.handshake import (
    InitiatorHandshake,
    ResponderHandshake,
    SecureSession,
)
from overlay.identity.keypair import Identity
from overlay.transport.connection import (
    Connection,
    ConnectionClosed,
    ConnectionTimeout,
)
from overlay.transport.frame import Frame, FrameError, MessageType

log = logging.getLogger(__name__)

SESSION_ID_LEN = 16


class SecureConnectionError(Exception):
    pass


class SecureConnection:
    def __init__(self, conn: Connection, identity: Identity) -> None:
        self._conn = conn
        self._identity = identity
        self._session: SecureSession | None = None
        self._closed = False

    @property
    def connection_id(self) -> str:
        return self._conn.connection_id

    @property
    def peer_host(self) -> str:
        return self._conn.peer_host

    @property
    def peer_port(self) -> int:
        return self._conn.peer_port

    @property
    def is_closed(self) -> bool:
        return self._closed or self._conn.is_closed

    @property
    def session(self) -> SecureSession:
        if self._session is None:
            raise SecureConnectionError("Сессия ещё не установлена")
        return self._session

    @classmethod
    async def initiate(
        cls,
        host: str,
        port: int,
        identity: Identity,
        **connect_kwargs,
    ) -> "SecureConnection":
        conn = await Connection.connect(host, port, **connect_kwargs)
        secure = cls(conn, identity)
        try:
            await secure._handshake_as_initiator()
        except Exception:
            conn.close()
            await conn.wait_closed()
            raise
        return secure

    @classmethod
    async def accept(
        cls,
        conn: Connection,
        identity: Identity,
    ) -> "SecureConnection":
        secure = cls(conn, identity)
        await secure._handshake_as_responder()
        return secure

    async def _handshake_as_initiator(self) -> None:
        hs = InitiatorHandshake(self._identity)
        hello = hs.create_hello()
        await self._conn.send(
            Frame(
                msg_type=MessageType.HANDSHAKE_INIT,
                request_id=secrets.token_bytes(16),
                payload=hello,
            )
        )
        ack_frame = await self._conn.recv()
        if ack_frame.msg_type != MessageType.HANDSHAKE_ACK:
            raise SecureConnectionError(
                f"Ожидался HANDSHAKE_ACK, получен {ack_frame.msg_type}"
            )
        finish = hs.process_hello_ack(ack_frame.payload)
        await self._conn.send(
            Frame(
                msg_type=MessageType.HANDSHAKE_FINISH,
                request_id=secrets.token_bytes(16),
                payload=finish,
            )
        )
        if hs.session is None:
            raise SecureConnectionError("Сессия не создана после handshake")
        self._session = hs.session
        log.info(
            "Handshake (initiator) с %s:%d session=%s",
            self._conn.peer_host,
            self._conn.peer_port,
            self._session.session_id.hex(),
        )

    async def _handshake_as_responder(self) -> None:
        init_frame = await self._conn.recv()
        if init_frame.msg_type != MessageType.HANDSHAKE_INIT:
            raise SecureConnectionError(
                f"Ожидался HANDSHAKE_INIT, получен {init_frame.msg_type}"
            )
        hs = ResponderHandshake(self._identity)
        ack = hs.process_hello(init_frame.payload)
        await self._conn.send(
            Frame(
                msg_type=MessageType.HANDSHAKE_ACK,
                request_id=secrets.token_bytes(16),
                payload=ack,
            )
        )
        finish_frame = await self._conn.recv()
        if finish_frame.msg_type != MessageType.HANDSHAKE_FINISH:
            raise SecureConnectionError(
                f"Ожидался HANDSHAKE_FINISH, получен {finish_frame.msg_type}"
            )
        hs.process_finish(finish_frame.payload)
        if hs.session is None:
            raise SecureConnectionError("Сессия не создана после handshake")
        self._session = hs.session
        log.info(
            "Handshake (responder) с %s:%d session=%s",
            self._conn.peer_host,
            self._conn.peer_port,
            self._session.session_id.hex(),
        )

    def _make_aad(self, session_id: bytes, counter: int) -> bytes:
        return session_id + counter.to_bytes(8, "big")

    async def send(self, frame: Frame) -> None:
        if self._closed:
            raise ConnectionClosed("Соединение закрыто")
        if self._session is None:
            raise SecureConnectionError("Сессия не установлена")
        inner = frame.encode()
        sid = self._session.session_id
        aead = self._session.send_aead
        pre_counter = aead.send_counter
        counter, ct = aead.encrypt(inner, aad=self._make_aad(sid, pre_counter))
        payload = sid + counter.to_bytes(8, "big") + ct
        secure_frame = Frame(
            msg_type=MessageType.SECURE_FRAME,
            request_id=frame.request_id,
            payload=payload,
        )
        await self._conn.send(secure_frame)

    async def recv(self) -> Frame:
        if self._closed:
            raise ConnectionClosed("Соединение закрыто")
        if self._session is None:
            raise SecureConnectionError("Сессия не установлена")
        secure_frame = await self._conn.recv()
        if secure_frame.msg_type != MessageType.SECURE_FRAME:
            raise SecureConnectionError(
                f"Ожидался SECURE_FRAME, получен {secure_frame.msg_type}"
            )
        payload = secure_frame.payload
        if len(payload) < SESSION_ID_LEN + 8:
            raise SecureConnectionError("SECURE_FRAME слишком короткий")
        sid = payload[:SESSION_ID_LEN]
        counter = int.from_bytes(payload[SESSION_ID_LEN:SESSION_ID_LEN + 8], "big")
        ct = payload[SESSION_ID_LEN + 8:]
        if sid != self._session.session_id:
            raise SecureConnectionError(
                "session_id не совпадает с текущей сессией"
            )
        aad = self._make_aad(sid, counter)
        plaintext = self._session.recv_aead.decrypt(counter, ct, aad=aad)
        return Frame.decode(plaintext)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._conn.close()

    async def wait_closed(self) -> None:
        try:
            await self._conn.wait_closed()
        except Exception:
            pass