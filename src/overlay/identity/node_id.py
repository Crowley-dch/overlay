from __future__ import annotations
import hashlib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

NODE_ID_LENGTH = 32


def canonical_encode_public_key(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def compute_node_id_from_bytes(public_key_bytes: bytes) -> bytes:
    if len(public_key_bytes) != 32:
        raise ValueError(
            f"public_key должен быть 32 байта, получено {len(public_key_bytes)}"
        )
    return hashlib.sha256(public_key_bytes).digest()


def compute_node_id(public_key: Ed25519PublicKey) -> bytes:
    return compute_node_id_from_bytes(canonical_encode_public_key(public_key))


def verify_node_id(node_id: bytes, public_key_bytes: bytes) -> bool:
    if len(node_id) != NODE_ID_LENGTH:
        return False
    try:
        return compute_node_id_from_bytes(public_key_bytes) == node_id
    except ValueError:
        return False


def node_id_to_hex(node_id: bytes) -> str:
    if len(node_id) != NODE_ID_LENGTH:
        raise ValueError(
            f"NodeID должен быть {NODE_ID_LENGTH} байт, получено {len(node_id)}"
        )
    return node_id.hex()


def node_id_from_hex(s: str) -> bytes:
    if len(s) != NODE_ID_LENGTH * 2:
        raise ValueError(
            f"NodeID hex должен быть {NODE_ID_LENGTH * 2} символов, получено {len(s)}"
        )
    return bytes.fromhex(s)