import time

import pytest

from overlay.dht.record import RecordError, build_record
from overlay.dht.store import RecordConflict, RecordStore
from overlay.identity import Identity


def make_record(
    identity: Identity,
    *,
    seq: int = 1,
    ttl: int = 180,
    addresses: list[str] | None = None,
):
    return build_record(
        node_id=identity.node_id,
        identity_public_key=identity.public_key_bytes(),
        addresses=addresses or ["127.0.0.1:9000"],
        sequence_number=seq,
        ttl=ttl,
        private_key=identity.private_key,
    )


def test_store_put_and_get():
    store = RecordStore()
    identity = Identity.generate()
    key = identity.node_id
    record = make_record(identity)
    store.put(key, record)
    got = store.get(key)
    assert got is not None
    assert got.node_id == identity.node_id
    assert len(store) == 1


def test_store_get_returns_none_for_missing():
    store = RecordStore()
    assert store.get(b"\x00" * 32) is None


def test_store_put_rejects_invalid_signature():
    store = RecordStore()
    identity = Identity.generate()
    record = make_record(identity)
    record.addresses = ["10.0.0.1:9000"]
    with pytest.raises(RecordError):
        store.put(identity.node_id, record)


def test_store_put_rejects_expired():
    store = RecordStore()
    identity = Identity.generate()
    past = int(time.time()) - 3600
    record = build_record(
        node_id=identity.node_id,
        identity_public_key=identity.public_key_bytes(),
        addresses=["127.0.0.1:9000"],
        sequence_number=1,
        ttl=60,
        private_key=identity.private_key,
        issued_at=past,
    )
    with pytest.raises(RecordError):
        store.put(identity.node_id, record)


def test_store_put_rejects_rollback():
    store = RecordStore()
    identity = Identity.generate()
    key = identity.node_id
    store.put(key, make_record(identity, seq=5))
    with pytest.raises(RecordConflict):
        store.put(key, make_record(identity, seq=3))


def test_store_accepts_higher_sequence():
    store = RecordStore()
    identity = Identity.generate()
    key = identity.node_id
    store.put(key, make_record(identity, seq=3))
    store.put(key, make_record(identity, seq=5))
    got = store.get(key)
    assert got is not None
    assert got.sequence_number == 5


def test_store_rejects_same_sequence_different_signature():
    store = RecordStore()
    identity = Identity.generate()
    key = identity.node_id
    store.put(key, make_record(identity, seq=3, addresses=["127.0.0.1:9000"]))
    with pytest.raises(RecordConflict):
        store.put(
            key,
            make_record(identity, seq=3, addresses=["127.0.0.1:9001"]),
        )


def test_store_idempotent_for_identical_record():
    store = RecordStore()
    identity = Identity.generate()
    key = identity.node_id
    record = make_record(identity, seq=3)
    store.put(key, record)
    store.put(key, record)
    assert len(store) == 1


def test_store_rejects_foreign_node_id_on_same_key():
    store = RecordStore()
    a = Identity.generate()
    b = Identity.generate()
    key = a.node_id
    store.put(key, make_record(a))
    with pytest.raises(RecordConflict):
        store.put(key, make_record(b))


def test_store_get_removes_expired():
    store = RecordStore()
    identity = Identity.generate()
    past = int(time.time()) - 3600
    record = build_record(
        node_id=identity.node_id,
        identity_public_key=identity.public_key_bytes(),
        addresses=["127.0.0.1:9000"],
        sequence_number=1,
        ttl=60,
        private_key=identity.private_key,
        issued_at=past,
    )
    store._records[identity.node_id] = record
    assert store.get(identity.node_id) is None
    assert len(store) == 0


def test_store_cleanup_removes_expired():
    store = RecordStore()
    identity = Identity.generate()
    past = int(time.time()) - 3600
    record = build_record(
        node_id=identity.node_id,
        identity_public_key=identity.public_key_bytes(),
        addresses=["127.0.0.1:9000"],
        sequence_number=1,
        ttl=60,
        private_key=identity.private_key,
        issued_at=past,
    )
    store._records[identity.node_id] = record
    removed = store.cleanup()
    assert removed == 1
    assert len(store) == 0


def test_store_snapshot():
    store = RecordStore()
    identity = Identity.generate()
    store.put(identity.node_id, make_record(identity))
    snapshot = store.snapshot()
    key_hex = identity.node_id.hex()
    assert key_hex in snapshot
    assert snapshot[key_hex]["sequence_number"] == 1
    assert snapshot[key_hex]["expires_in"] > 0


def test_store_remove():
    store = RecordStore()
    identity = Identity.generate()
    key = identity.node_id
    store.put(key, make_record(identity))
    assert store.remove(key) is True
    assert store.remove(key) is False