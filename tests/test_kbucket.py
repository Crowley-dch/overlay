import pytest

from overlay.dht.contact import Contact
from overlay.dht.kbucket import AddResult, KBucket


def make_contact(seed: int) -> Contact:
    return Contact(
        node_id=bytes([seed] * 32),
        host="127.0.0.1",
        port=9000,
    )


def test_contact_rejects_bad_node_id():
    with pytest.raises(ValueError):
        Contact(node_id=b"\x00" * 16, host="127.0.0.1", port=9000)


def test_kbucket_add_new_contact():
    b = KBucket(capacity=3, index=0)
    assert b.add_or_update(make_contact(1)) == AddResult.ADDED
    assert len(b) == 1


def test_kbucket_capacity_limit():
    b = KBucket(capacity=2, index=0)
    assert b.add_or_update(make_contact(1)) == AddResult.ADDED
    assert b.add_or_update(make_contact(2)) == AddResult.ADDED
    assert b.add_or_update(make_contact(3)) == AddResult.FULL
    assert len(b) == 2


def test_kbucket_duplicate_updates_and_moves_to_front():
    b = KBucket(capacity=3, index=0)
    c1 = make_contact(1)
    c2 = make_contact(2)
    b.add_or_update(c1)
    b.add_or_update(c2)
    assert b.most_recent().node_id == c2.node_id
    assert b.add_or_update(make_contact(1)) == AddResult.UPDATED
    assert len(b) == 2
    assert b.most_recent().node_id == c1.node_id
    assert b.least_recent().node_id == c2.node_id


def test_kbucket_replace_lru():
    b = KBucket(capacity=2, index=0)
    b.add_or_update(make_contact(1))
    b.add_or_update(make_contact(2))
    removed = b.replace_lru(make_contact(3))
    assert removed.node_id == bytes([1] * 32)
    assert len(b) == 2
    assert b.most_recent().node_id == bytes([3] * 32)
    assert b.least_recent().node_id == bytes([2] * 32)


def test_kbucket_touch_moves_to_front():
    b = KBucket(capacity=3, index=0)
    b.add_or_update(make_contact(1))
    b.add_or_update(make_contact(2))
    b.add_or_update(make_contact(3))
    assert b.least_recent().node_id == bytes([1] * 32)
    assert b.touch(bytes([1] * 32)) is True
    assert b.most_recent().node_id == bytes([1] * 32)
    assert b.least_recent().node_id == bytes([2] * 32)


def test_kbucket_remove():
    b = KBucket(capacity=3, index=0)
    b.add_or_update(make_contact(1))
    assert b.remove(bytes([1] * 32)) is True
    assert b.remove(bytes([1] * 32)) is False
    assert len(b) == 0


def test_kbucket_get():
    b = KBucket(capacity=3, index=0)
    c = make_contact(1)
    b.add_or_update(c)
    assert b.get(c.node_id) is not None
    assert b.get(bytes([99] * 32)) is None


def test_kbucket_replace_lru_on_empty_raises():
    b = KBucket(capacity=3, index=0)
    with pytest.raises(RuntimeError):
        b.replace_lru(make_contact(1))