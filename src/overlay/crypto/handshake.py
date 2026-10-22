from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)

from overlay.crypto.aead import AeadSession
from overlay.crypto.hkdf import derive_directional_keys
from overlay.identity.keypair import Identity
from overlay.identity.node_id import compute_node_id_from_bytes

HANDSHAKE_VERSION = 1
SESSION_ID_LEN = 16
NONCE_LEN = 16
SALT_LEN = 4


class HandshakeError(Exception):
    pass


@dataclass
class HandshakeMessage:
    kind: str
    version: int
    sender_node_id: bytes
    sender_identity_pubkey: bytes
    sender_ephemeral_pubkey: bytes
    nonce: bytes
    transcript_signature: bytes = b""
    session_id: bytes = b""

    def to_bytes(self) -> bytes:
        return json.dumps(
            {
                "kind": self.kind,
                "version": self.version,
                "sender_node_id": self.sender_node_id.hex(),
                "sender_identity_pubkey": self.sender_identity_pubkey.hex(),
                "sender_ephemeral_pubkey": self.sender_ephemeral_pubkey.hex(),
                "nonce": self.nonce.hex(),
                "transcript_signature": self.transcript_signature.hex(),
                "session_id": self.session_id.hex(),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> "HandshakeMessage":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise HandshakeError(f"Некорректный JSON: {e}") from e
        try:
            kind = data["kind"]
            version = int(data["version"])
            sender_node_id = bytes.fromhex(data["sender_node_id"])
            sender_identity_pubkey = bytes.fromhex(
                data["sender_identity_pubkey"]
            )
            sender_ephemeral_pubkey = bytes.fromhex(
                data["sender_ephemeral_pubkey"]
            )
            nonce = bytes.fromhex(data["nonce"])
            transcript_signature = bytes.fromhex(
                data.get("transcript_signature", "")
            )
            session_id = bytes.fromhex(data.get("session_id", ""))
        except (KeyError, ValueError, TypeError) as e:
            raise HandshakeError(f"Некорректное поле: {e}") from e

        if kind not in {"hello", "hello_ack", "finish"}:
            raise HandshakeError(f"Неизвестный kind: {kind}")
        if version != HANDSHAKE_VERSION:
            raise HandshakeError(f"Версия handshake {version} не поддерживается")
        if len(sender_node_id) != 32:
            raise HandshakeError("sender_node_id должен быть 32 байта")
        if len(sender_identity_pubkey) != 32:
            raise HandshakeError(
                "sender_identity_pubkey должен быть 32 байта"
            )
        if len(sender_ephemeral_pubkey) != 32:
            raise HandshakeError(
                "sender_ephemeral_pubkey должен быть 32 байта"
            )
        if len(nonce) != NONCE_LEN:
            raise HandshakeError(f"nonce должен быть {NONCE_LEN} байт")

        if compute_node_id_from_bytes(sender_identity_pubkey) != sender_node_id:
            raise HandshakeError(
                "sender_node_id не соответствует sender_identity_pubkey"
            )

        return cls(
            kind=kind,
            version=version,
            sender_node_id=sender_node_id,
            sender_identity_pubkey=sender_identity_pubkey,
            sender_ephemeral_pubkey=sender_ephemeral_pubkey,
            nonce=nonce,
            transcript_signature=transcript_signature,
            session_id=session_id,
        )


@dataclass
class SecureSession:
    session_id: bytes
    send_aead: AeadSession
    recv_aead: AeadSession
    peer_node_id: bytes


def _canonical_transcript(
    *,
    initiator_node_id: bytes,
    responder_node_id: bytes,
    initiator_identity_pubkey: bytes,
    responder_identity_pubkey: bytes,
    initiator_ephemeral_pubkey: bytes,
    responder_ephemeral_pubkey: bytes,
    nonce_init: bytes,
    nonce_resp: bytes,
    session_id: bytes,
) -> bytes:
    return json.dumps(
        {
            "version": HANDSHAKE_VERSION,
            "initiator_node_id": initiator_node_id.hex(),
            "responder_node_id": responder_node_id.hex(),
            "initiator_identity_pubkey": initiator_identity_pubkey.hex(),
            "responder_identity_pubkey": responder_identity_pubkey.hex(),
            "initiator_ephemeral_pubkey": initiator_ephemeral_pubkey.hex(),
            "responder_ephemeral_pubkey": responder_ephemeral_pubkey.hex(),
            "nonce_init": nonce_init.hex(),
            "nonce_resp": nonce_resp.hex(),
            "session_id": session_id.hex(),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _x25519_pub_bytes(key: X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _load_x25519_pub(raw: bytes) -> X25519PublicKey:
    return X25519PublicKey.from_public_bytes(raw)


def _sign(identity: Identity, transcript: bytes) -> bytes:
    return identity.private_key.sign(transcript)


def _verify(identity_pubkey: bytes, signature: bytes, transcript: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(identity_pubkey).verify(
            signature, transcript
        )
        return True
    except Exception:
        return False


class InitiatorHandshake:
    def __init__(self, identity: Identity) -> None:
        self._identity = identity
        self._ephemeral = X25519PrivateKey.generate()
        self._nonce = secrets.token_bytes(NONCE_LEN)
        self._session_id: bytes | None = None
        self._peer: HandshakeMessage | None = None
        self.session: SecureSession | None = None

    def create_hello(self) -> bytes:
        if self._peer is not None:
            raise HandshakeError("hello уже создан")
        msg = HandshakeMessage(
            kind="hello",
            version=HANDSHAKE_VERSION,
            sender_node_id=self._identity.node_id,
            sender_identity_pubkey=self._identity.public_key_bytes(),
            sender_ephemeral_pubkey=_x25519_pub_bytes(self._ephemeral),
            nonce=self._nonce,
        )
        return msg.to_bytes()

    def process_hello_ack(self, raw: bytes) -> bytes:
        if self._session_id is not None:
            raise HandshakeError("hello_ack уже обработан")
        ack = HandshakeMessage.from_bytes(raw)
        if ack.kind != "hello_ack":
            raise HandshakeError(f"Ожидался hello_ack, получен {ack.kind}")
        if len(ack.session_id) != SESSION_ID_LEN:
            raise HandshakeError("session_id должен быть 16 байт")
        if not ack.transcript_signature:
            raise HandshakeError("hello_ack без подписи транскрипта")

        transcript = _canonical_transcript(
            initiator_node_id=self._identity.node_id,
            responder_node_id=ack.sender_node_id,
            initiator_identity_pubkey=self._identity.public_key_bytes(),
            responder_identity_pubkey=ack.sender_identity_pubkey,
            initiator_ephemeral_pubkey=_x25519_pub_bytes(self._ephemeral),
            responder_ephemeral_pubkey=ack.sender_ephemeral_pubkey,
            nonce_init=self._nonce,
            nonce_resp=ack.nonce,
            session_id=ack.session_id,
        )

        if not _verify(
            ack.sender_identity_pubkey,
            ack.transcript_signature,
            transcript,
        ):
            raise HandshakeError("Подпись hello_ack недействительна")

        peer_ephemeral = _load_x25519_pub(ack.sender_ephemeral_pubkey)
        shared_secret = self._ephemeral.exchange(peer_ephemeral)

        key_i_to_r, key_r_to_i = derive_directional_keys(
            shared_secret,
            salt=ack.session_id,
            initiator_node_id=self._identity.node_id,
            responder_node_id=ack.sender_node_id,
        )

        send_aead = AeadSession(key_i_to_r, salt=ack.session_id[:SALT_LEN])
        recv_aead = AeadSession(key_r_to_i, salt=ack.session_id[:SALT_LEN])

        self.session = SecureSession(
            session_id=ack.session_id,
            send_aead=send_aead,
            recv_aead=recv_aead,
            peer_node_id=ack.sender_node_id,
        )

        finish_signature = _sign(self._identity, transcript)
        finish = HandshakeMessage(
            kind="finish",
            version=HANDSHAKE_VERSION,
            sender_node_id=self._identity.node_id,
            sender_identity_pubkey=self._identity.public_key_bytes(),
            sender_ephemeral_pubkey=_x25519_pub_bytes(self._ephemeral),
            nonce=self._nonce,
            transcript_signature=finish_signature,
            session_id=ack.session_id,
        )
        self._peer = ack
        return finish.to_bytes()


class ResponderHandshake:
    def __init__(self, identity: Identity) -> None:
        self._identity = identity
        self._ephemeral = X25519PrivateKey.generate()
        self._nonce = secrets.token_bytes(NONCE_LEN)
        self._session_id = secrets.token_bytes(SESSION_ID_LEN)
        self._initiator: HandshakeMessage | None = None
        self.session: SecureSession | None = None

    def process_hello(self, raw: bytes) -> bytes:
        if self._initiator is not None:
            raise HandshakeError("hello уже обработан")
        hello = HandshakeMessage.from_bytes(raw)
        if hello.kind != "hello":
            raise HandshakeError(f"Ожидался hello, получен {hello.kind}")

        transcript = _canonical_transcript(
            initiator_node_id=hello.sender_node_id,
            responder_node_id=self._identity.node_id,
            initiator_identity_pubkey=hello.sender_identity_pubkey,
            responder_identity_pubkey=self._identity.public_key_bytes(),
            initiator_ephemeral_pubkey=hello.sender_ephemeral_pubkey,
            responder_ephemeral_pubkey=_x25519_pub_bytes(self._ephemeral),
            nonce_init=hello.nonce,
            nonce_resp=self._nonce,
            session_id=self._session_id,
        )

        signature = _sign(self._identity, transcript)

        ack = HandshakeMessage(
            kind="hello_ack",
            version=HANDSHAKE_VERSION,
            sender_node_id=self._identity.node_id,
            sender_identity_pubkey=self._identity.public_key_bytes(),
            sender_ephemeral_pubkey=_x25519_pub_bytes(self._ephemeral),
            nonce=self._nonce,
            transcript_signature=signature,
            session_id=self._session_id,
        )

        peer_ephemeral = _load_x25519_pub(hello.sender_ephemeral_pubkey)
        shared_secret = self._ephemeral.exchange(peer_ephemeral)

        key_i_to_r, key_r_to_i = derive_directional_keys(
            shared_secret,
            salt=self._session_id,
            initiator_node_id=hello.sender_node_id,
            responder_node_id=self._identity.node_id,
        )

        recv_aead = AeadSession(
            key_i_to_r, salt=self._session_id[:SALT_LEN]
        )
        send_aead = AeadSession(
            key_r_to_i, salt=self._session_id[:SALT_LEN]
        )

        self._initiator = hello
        self.session = SecureSession(
            session_id=self._session_id,
            send_aead=send_aead,
            recv_aead=recv_aead,
            peer_node_id=hello.sender_node_id,
        )
        return ack.to_bytes()

    def process_finish(self, raw: bytes) -> None:
        if self._initiator is None:
            raise HandshakeError("finish до hello")
        finish = HandshakeMessage.from_bytes(raw)
        if finish.kind != "finish":
            raise HandshakeError(f"Ожидался finish, получен {finish.kind}")
        if finish.session_id != self._session_id:
            raise HandshakeError("session_id не совпадает")
        if finish.sender_node_id != self._initiator.sender_node_id:
            raise HandshakeError("finish от другого node_id")

        transcript = _canonical_transcript(
            initiator_node_id=self._initiator.sender_node_id,
            responder_node_id=self._identity.node_id,
            initiator_identity_pubkey=self._initiator.sender_identity_pubkey,
            responder_identity_pubkey=self._identity.public_key_bytes(),
            initiator_ephemeral_pubkey=self._initiator.sender_ephemeral_pubkey,
            responder_ephemeral_pubkey=_x25519_pub_bytes(self._ephemeral),
            nonce_init=self._initiator.nonce,
            nonce_resp=self._nonce,
            session_id=self._session_id,
        )

        if not _verify(
            self._initiator.sender_identity_pubkey,
            finish.transcript_signature,
            transcript,
        ):
            raise HandshakeError("Подпись finish недействительна")