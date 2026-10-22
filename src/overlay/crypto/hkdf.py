from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

HKDF_HASH = hashes.SHA256()
HASH_LEN = 32


def derive_keys(
    shared_secret: bytes,
    *,
    salt: bytes,
    info: bytes,
    length: int = 64,
) -> bytes:
    if not shared_secret:
        raise ValueError("shared_secret пуст")
    if length <= 0:
        raise ValueError("length должен быть > 0")
    if length > 255 * HASH_LEN:
        raise ValueError("length превышает максимум HKDF-SHA256")
    hkdf = HKDF(
        algorithm=HKDF_HASH,
        length=length,
        salt=salt,
        info=info,
    )
    return hkdf.derive(shared_secret)


def derive_directional_keys(
    shared_secret: bytes,
    *,
    salt: bytes,
    initiator_node_id: bytes,
    responder_node_id: bytes,
) -> tuple[bytes, bytes]:
    info_i_to_r = b"overlay-v1-i-to-r:" + initiator_node_id + responder_node_id
    info_r_to_i = b"overlay-v1-r-to-i:" + responder_node_id + initiator_node_id
    key_i_to_r = derive_keys(
        shared_secret, salt=salt, info=info_i_to_r, length=32
    )
    key_r_to_i = derive_keys(
        shared_secret, salt=salt, info=info_r_to_i, length=32
    )
    return key_i_to_r, key_r_to_i