from __future__ import annotations

from overlay.identity.node_id import NODE_ID_LENGTH


def xor_distance(a: bytes, b: bytes) -> bytes:
    if len(a) != NODE_ID_LENGTH or len(b) != NODE_ID_LENGTH:
        raise ValueError(f"NodeID должен быть {NODE_ID_LENGTH} байт")
    return bytes(x ^ y for x, y in zip(a, b))


def xor_distance_int(a: bytes, b: bytes) -> int:
    return int.from_bytes(xor_distance(a, b), "big")


def common_prefix_length(a: bytes, b: bytes) -> int:
    if len(a) != NODE_ID_LENGTH or len(b) != NODE_ID_LENGTH:
        raise ValueError(f"NodeID должен быть {NODE_ID_LENGTH} байт")
    total = 0
    for x, y in zip(a, b):
        diff = x ^ y
        if diff == 0:
            total += 8
            continue
        total += 8 - diff.bit_length()
        break
    return total


def bucket_index(self_id: bytes, node_id: bytes) -> int:
    if self_id == node_id:
        raise ValueError("bucket_index для self_id не определён")
    return common_prefix_length(self_id, node_id)
