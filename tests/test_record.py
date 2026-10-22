import time

import pytest

from overlay.dht.record import (
    NodeRecord,
    RecordError,
    build_record,
    record_key_for_alias,
    record_key_for_node,
)
from overlay.identity import Identity


def make_record(
    identity: Identity,
    *,
    seq: int = 1,
    ttl: int = 180,
    addresses: list[str] | None = None,
    alias: str | None = None,
    issued_at: int | None = None,
) -> NodeRecord:
    return build_record(
        node_id=identity.node_id,
        identity_public_key=identity.public_key_bytes(),
        addresses=addresses or ["127.0.0.1:9000"],
        sequence_number=seq,
        ttl=ttl,
        private_key=identity.private_key,
        alias=alias,
        issued_at=issued_at,
    )


def test_record_key_for_node_is_deterministic():
    identity = Identity.generate()
    a = record_key_for_node(identity.node_id)
    b = record_key_for_node(identity.node_id)
    assert a == b
    assert len(a) == 32


def test_record_key_for_node_rejects_bad_length():
    with pytest.raises(RecordError):
        record_key_for_node(b"\x00" * 16)


def test_record_key_for_alias_normalizes():
    a = record_key_for_alias("Alice")
    b = record_key_for_alias("  alice  ")
    c = record_key_for_alias("ALICE")
    assert a == b == c


def test_record_key_for_alias_rejects_empty():
    with pytest.raises(RecordError):
        record_key_for_alias("   ")


def test_record_sign_and_verify():
    identity = Identity.generate()
    record = make_record(identity)
    assert record.verify_signature() is True
    assert record.verify_node_id() is True


def test_record_json_roundtrip():
    identity = Identity.generate()
    record = make_record(identity, alias="Alice")
    raw = record.to_json_bytes()
    decoded = NodeRecord.from_json_bytes(raw)
    assert decoded.node_id == record.node_id
    assert decoded.identity_public_key == record.identity_public_key
    assert decoded.addresses == record.addresses
    assert decoded.sequence_number == record.sequence_number
    assert decoded.alias == record.alias
    assert decoded.verify_signature() is True


def test_record_canonical_bytes_independent_of_dict_order():
    identity = Identity.generate()
    record = make_record(identity)
    a = record.canonical_bytes()
    b = record.canonical_bytes()
    assert a == b
    assert b"signature" not in a


def test_record_rejects_bad_node_id_length():
    identity = Identity.generate()
    with pytest.raises(RecordError):
        NodeRecord(
            node_id=b"\x00" * 16,
            identity_public_key=identity.public_key_bytes(),
            addresses=["127.0.0.1:9000"],
        )


def test_record_rejects_empty_addresses():
    identity = Identity.generate()
    with pytest.raises(RecordError):
        NodeRecord(
            node_id=identity.node_id,
            identity_public_key=identity.public_key_bytes(),
            addresses=[],
        )


def test_record_rejects_bad_sequence():
    identity = Identity.generate()
    with pytest.raises(RecordError):
        NodeRecord(
            node_id=identity.node_id,
            identity_public_key=identity.public_key_bytes(),
            addresses=["127.0.0.1:9000"],
            sequence_number=0,
        )


def test_record_detects_tampered_payload():
    identity = Identity.generate()
    record = make_record(identity)
    record.addresses = ["10.0.0.1:9000"]
    assert record.verify_signature() is False


def test_record_detects_mismatched_node_id():
    a = Identity.generate()
    b = Identity.generate()
    record = make_record(a)
    record.identity_public_key = b.public_key_bytes()
    assert record.verify_node_id() is False


def test_record_validate_rejects_expired():
    identity = Identity.generate()
    past = int(time.time()) - 3600
    record = make_record(identity, ttl=60, issued_at=past)
    with pytest.raises(RecordError):
        record.validate()


def test_record_validate_rejects_not_yet_valid():
    identity = Identity.generate()
    future = int(time.time()) + 3600
    record = make_record(identity, issued_at=future)
    with pytest.raises(RecordError):
        record.validate()


def test_record_max_size_enforced():
    identity = Identity.generate()
    record = make_record(identity, addresses=["127.0.0.1:9000"])
    raw = record.to_json_bytes()
    with pytest.raises(RecordError):
        record.to_json_bytes(max_size=len(raw) - 1)


def test_record_from_json_rejects_garbage():
    with pytest.raises(RecordError):
        NodeRecord.from_json_bytes(b"not json")
    with pytest.raises(RecordError):
        NodeRecord.from_json_bytes(b"[]")
    with pytest.raises(RecordError):
        NodeRecord.from_json_bytes(b'{"node_id": "00"}')