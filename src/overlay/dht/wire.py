from __future__ import annotations

import json

from overlay.dht.contact import Contact
from overlay.identity.node_id import NODE_ID_LENGTH


class WireError(Exception):
    pass


def encode_find_node_request(target: bytes, sender: Contact) -> bytes:
    if len(target) != NODE_ID_LENGTH:
        raise WireError(f"target должен быть {NODE_ID_LENGTH} байт")
    return json.dumps(
        {
            "target": target.hex(),
            "sender": _contact_to_dict(sender),
        }
    ).encode("utf-8")


def decode_find_node_request(payload: bytes) -> tuple[bytes, Contact]:
    data = _loads(payload, "FIND_NODE_REQUEST")
    target_hex = data.get("target")
    if not isinstance(target_hex, str) or len(target_hex) != NODE_ID_LENGTH * 2:
        raise WireError("target должен быть hex-строкой из 64 символов")
    try:
        target = bytes.fromhex(target_hex)
    except ValueError as e:
        raise WireError(f"target не является корректным hex: {e}") from e

    sender_raw = data.get("sender")
    if sender_raw is None:
        raise WireError("sender обязателен")
    sender = _dict_to_contact(sender_raw)
    return target, sender


def encode_find_node_response(contacts: list[Contact]) -> bytes:
    payload = {"contacts": [_contact_to_dict(c) for c in contacts]}
    return json.dumps(payload).encode("utf-8")


def decode_find_node_response(payload: bytes) -> list[Contact]:
    data = _loads(payload, "FIND_NODE_RESPONSE")
    raw = data.get("contacts")
    if not isinstance(raw, list):
        raise WireError("contacts должен быть списком")
    contacts: list[Contact] = []
    for i, item in enumerate(raw):
        try:
            contacts.append(_dict_to_contact(item))
        except WireError as e:
            raise WireError(f"contacts[{i}]: {e}") from e
    return contacts


def _loads(payload: bytes, kind: str) -> dict:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise WireError(f"{kind}: некорректный JSON: {e}") from e
    if not isinstance(data, dict):
        raise WireError(f"{kind}: ожидался объект")
    return data


def _contact_to_dict(c: Contact) -> dict:
    return {"node_id": c.node_id_hex, "host": c.host, "port": c.port}


def _dict_to_contact(item) -> Contact:
    if not isinstance(item, dict):
        raise WireError("контакт должен быть объектом")
    node_id_hex = item.get("node_id")
    host = item.get("host")
    port = item.get("port")
    if not isinstance(node_id_hex, str) or len(node_id_hex) != NODE_ID_LENGTH * 2:
        raise WireError("node_id должен быть hex-строкой из 64 символов")
    if not isinstance(host, str) or not host:
        raise WireError("host должен быть непустой строкой")
    if not isinstance(port, int) or not (0 < port < 65536):
        raise WireError("port должен быть целым в диапазоне 1..65535")
    try:
        node_id = bytes.fromhex(node_id_hex)
    except ValueError as e:
        raise WireError(f"node_id не hex: {e}") from e
    return Contact(node_id=node_id, host=host, port=port)

def encode_store_request(key: bytes, record_json: dict) -> bytes:
    if len(key) != 32:
        raise WireError("key должен быть 32 байта")
    return json.dumps({"key": key.hex(), "record": record_json}).encode("utf-8")


def decode_store_request(payload: bytes) -> tuple[bytes, dict]:
    data = _loads(payload, "STORE_REQUEST")
    key_hex = data.get("key")
    record = data.get("record")
    if not isinstance(key_hex, str) or len(key_hex) != 64:
        raise WireError("key должен быть hex-строкой из 64 символов")
    if not isinstance(record, dict):
        raise WireError("record должен быть объектом")
    try:
        key = bytes.fromhex(key_hex)
    except ValueError as e:
        raise WireError(f"key не является корректным hex: {e}") from e
    return key, record


def encode_store_response(stored: bool, reason: str = "") -> bytes:
    return json.dumps({"stored": stored, "reason": reason}).encode("utf-8")


def decode_store_response(payload: bytes) -> tuple[bool, str]:
    data = _loads(payload, "STORE_RESPONSE")
    stored = data.get("stored")
    reason = data.get("reason", "")
    if not isinstance(stored, bool):
        raise WireError("stored должен быть bool")
    if not isinstance(reason, str):
        raise WireError("reason должен быть строкой")
    return stored, reason


def encode_find_value_request(key: bytes) -> bytes:
    if len(key) != 32:
        raise WireError("key должен быть 32 байта")
    return json.dumps({"key": key.hex()}).encode("utf-8")


def decode_find_value_request(payload: bytes) -> bytes:
    data = _loads(payload, "FIND_VALUE_REQUEST")
    key_hex = data.get("key")
    if not isinstance(key_hex, str) or len(key_hex) != 64:
        raise WireError("key должен быть hex-строкой из 64 символов")
    try:
        return bytes.fromhex(key_hex)
    except ValueError as e:
        raise WireError(f"key не является корректным hex: {e}") from e


def encode_find_value_response(
    record_json: dict | None,
    contacts: list[Contact],
) -> bytes:
    payload: dict = {"contacts": [_contact_to_dict(c) for c in contacts]}
    if record_json is not None:
        payload["record"] = record_json
    return json.dumps(payload).encode("utf-8")


def decode_find_value_response(payload: bytes) -> tuple[dict | None, list[Contact]]:
    data = _loads(payload, "FIND_VALUE_RESPONSE")
    raw = data.get("contacts", [])
    if not isinstance(raw, list):
        raise WireError("contacts должен быть списком")
    contacts: list[Contact] = []
    for i, item in enumerate(raw):
        try:
            contacts.append(_dict_to_contact(item))
        except WireError as e:
            raise WireError(f"contacts[{i}]: {e}") from e
    record = data.get("record")
    if record is not None and not isinstance(record, dict):
        raise WireError("record должен быть объектом или отсутствовать")
    return record, contacts