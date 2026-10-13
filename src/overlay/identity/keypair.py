from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from overlay.identity.node_id import compute_node_id


class Identity:
    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private_key = private_key
        self._public_key = private_key.public_key()
        self._node_id = compute_node_id(self._public_key)

    @classmethod
    def generate(cls) -> "Identity":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_private_bytes(cls, raw: bytes) -> "Identity":
        return cls(Ed25519PrivateKey.from_private_bytes(raw))

    @classmethod
    def from_pem(cls, data: bytes) -> "Identity":
        key = serialization.load_pem_private_key(data, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("Ожидался Ed25519 private key")
        return cls(key)

    @property
    def private_key(self) -> Ed25519PrivateKey:
        return self._private_key

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._public_key

    @property
    def node_id(self) -> bytes:
        return self._node_id

    @property
    def node_id_hex(self) -> str:
        return self._node_id.hex()

    def public_key_bytes(self) -> bytes:
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def to_private_pem(self) -> bytes:
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def sign(self, data: bytes) -> bytes:
        return self._private_key.sign(data)

    def verify(self, signature: bytes, data: bytes) -> bool:
        try:
            self._public_key.verify(signature, data)
            return True
        except InvalidSignature:
            return False

    def __repr__(self) -> str:
        return f"<Identity node_id={self._node_id.hex()[:12]}...>"


def verify_signature(
    public_key_bytes: bytes,
    signature: bytes,
    data: bytes,
) -> bool:
    try:
        pub = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        pub.verify(signature, data)
        return True
    except (InvalidSignature, ValueError):
        return False