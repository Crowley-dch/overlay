from __future__ import annotations

import json

from overlay.dht.contact import Contact
from overlay.identity.node_id import NODE_ID_LENGTH
from overlay.tunnel.route import TUNNEL_ID_LEN


class TunnelWireError(Exception):
    pass


def _contact_to_dict(c: Contact) -> dict:
    return {"node_id": c.node_id_hex, "host": c.host, "port": c.port}


def _dict_to_contact(item) -> Contact:
    if not isinstance(item, dict):
        raise TunnelWireError("Контакт должен быть объектом")
    node_id_hex = item.get("node_id")
    host = item.get("host")
    port = item.get("port")
    if (
        not isinstance(node_id_hex, str)
        or len(node_id_hex) != NODE_ID_LENGTH * 2
    ):
        raise TunnelWireError("node_id должен быть hex из 64 символов")
    if not isinstance(host, str) or not host:
        raise TunnelWireError("host должен быть непустой строкой")
    if not isinstance(port, int) or not (0 < port < 65536):
        raise TunnelWireError("port должен быть целым 1..65535")
    try:
        node_id = bytes.fromhex(node_id_hex)
    except ValueError as e:
        raise TunnelWireError(f"node_id не hex: {e}") from e
    return Contact(node_id=node_id, host=host, port=port)


def _tunnel_id_hex(tunnel_id: bytes) -> str:
    if len(tunnel_id) != TUNNEL_ID_LEN:
        raise TunnelWireError(
            f"tunnel_id должен быть {TUNNEL_ID_LEN} байт"
        )
    return tunnel_id.hex()


def _parse_tunnel_id(raw) -> bytes:
    if not isinstance(raw, str) or len(raw) != TUNNEL_ID_LEN * 2:
        raise TunnelWireError("tunnel_id должен быть hex из 32 символов")
    try:
        return bytes.fromhex(raw)
    except ValueError as e:
        raise TunnelWireError(f"tunnel_id не hex: {e}") from e


def _loads(payload: bytes, kind: str) -> dict:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise TunnelWireError(f"{kind}: некорректный JSON: {e}") from e
    if not isinstance(data, dict):
        raise TunnelWireError(f"{kind}: ожидался объект")
    return data


def encode_tunnel_build(
    tunnel_id: bytes,
    *,
    prev_hop: Contact | None,
    next_hop: Contact | None,
    max_hops: int,
    ttl: int,
) -> bytes:
    return json.dumps(
        {
            "tunnel_id": _tunnel_id_hex(tunnel_id),
            "prev_hop": _contact_to_dict(prev_hop) if prev_hop else None,
            "next_hop": _contact_to_dict(next_hop) if next_hop else None,
            "max_hops": int(max_hops),
            "ttl": int(ttl),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def decode_tunnel_build(payload: bytes) -> dict:
    data = _loads(payload, "TUNNEL_BUILD")
    tunnel_id = _parse_tunnel_id(data.get("tunnel_id"))
    prev_raw = data.get("prev_hop")
    next_raw = data.get("next_hop")
    max_hops = data.get("max_hops")
    ttl = data.get("ttl")
    if not isinstance(max_hops, int) or max_hops <= 0:
        raise TunnelWireError("max_hops должен быть > 0")
    if not isinstance(ttl, int) or ttl <= 0:
        raise TunnelWireError("ttl должен быть > 0")
    return {
        "tunnel_id": tunnel_id,
        "prev_hop": _dict_to_contact(prev_raw) if prev_raw else None,
        "next_hop": _dict_to_contact(next_raw) if next_raw else None,
        "max_hops": max_hops,
        "ttl": ttl,
    }


def encode_tunnel_build_ok(tunnel_id: bytes) -> bytes:
    return json.dumps(
        {"tunnel_id": _tunnel_id_hex(tunnel_id)},
        separators=(",", ":"),
    ).encode("utf-8")


def decode_tunnel_build_ok(payload: bytes) -> bytes:
    data = _loads(payload, "TUNNEL_BUILD_OK")
    return _parse_tunnel_id(data.get("tunnel_id"))


def encode_tunnel_build_fail(tunnel_id: bytes, reason: str) -> bytes:
    return json.dumps(
        {"tunnel_id": _tunnel_id_hex(tunnel_id), "reason": reason},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def decode_tunnel_build_fail(payload: bytes) -> tuple[bytes, str]:
    data = _loads(payload, "TUNNEL_BUILD_FAIL")
    tunnel_id = _parse_tunnel_id(data.get("tunnel_id"))
    reason = data.get("reason", "")
    if not isinstance(reason, str):
        raise TunnelWireError("reason должен быть строкой")
    return tunnel_id, reason


def encode_tunnel_data(
    tunnel_id: bytes,
    message_id: bytes,
    payload: bytes,
) -> bytes:
    if len(message_id) != 16:
        raise TunnelWireError("message_id должен быть 16 байт")
    return json.dumps(
        {
            "tunnel_id": _tunnel_id_hex(tunnel_id),
            "message_id": message_id.hex(),
            "payload": payload.hex(),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def decode_tunnel_data(payload: bytes) -> tuple[bytes, bytes, bytes]:
    data = _loads(payload, "TUNNEL_DATA")
    tunnel_id = _parse_tunnel_id(data.get("tunnel_id"))
    message_hex = data.get("message_id")
    payload_hex = data.get("payload")
    if not isinstance(message_hex, str) or len(message_hex) != 32:
        raise TunnelWireError("message_id должен быть hex из 32 символов")
    if not isinstance(payload_hex, str):
        raise TunnelWireError("payload должен быть hex-строкой")
    try:
        message_id = bytes.fromhex(message_hex)
        inner = bytes.fromhex(payload_hex)
    except ValueError as e:
        raise TunnelWireError(f"hex-поле некорректно: {e}") from e
    return tunnel_id, message_id, inner


def encode_tunnel_ack(
    tunnel_id: bytes,
    message_id: bytes,
    payload: bytes,
) -> bytes:
    if len(message_id) != 16:
        raise TunnelWireError("message_id должен быть 16 байт")
    return json.dumps(
        {
            "tunnel_id": _tunnel_id_hex(tunnel_id),
            "message_id": message_id.hex(),
            "payload": payload.hex(),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def decode_tunnel_ack(payload: bytes) -> tuple[bytes, bytes, bytes]:
    return decode_tunnel_data(payload)


def encode_tunnel_close(tunnel_id: bytes) -> bytes:
    return json.dumps(
        {"tunnel_id": _tunnel_id_hex(tunnel_id)},
        separators=(",", ":"),
    ).encode("utf-8")


def decode_tunnel_close(payload: bytes) -> bytes:
    data = _loads(payload, "TUNNEL_CLOSE")
    return _parse_tunnel_id(data.get("tunnel_id"))