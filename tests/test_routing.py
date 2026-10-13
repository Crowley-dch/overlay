import pytest

from overlay.dht.contact import Contact
from overlay.dht.kbucket import AddResult
from overlay.dht.routing import RoutingTable
from overlay.dht.xor import xor_distance_int


def hexid(s: str) -> bytes:
    return bytes.fromhex(s)


SELF_ID = hexid("00" * 32)


def make_contact_hex(hex_str: str) -> Contact:
    return Contact(node_id=hexid(hex_str), host="127.0.0.1", port=9000)


def test_routing_rejects_bad_self_id():
    with pytest.raises(ValueError):
        RoutingTable(self_id=b"\x00" * 16, k=3)


def test_routing_cannot_add_self():
    rt = RoutingTable(self_id=SELF_ID, k=3)
    with pytest.raises(ValueError):
        rt.add(Contact(node_id=SELF_ID, host="127.0.0.1", port=9000))


def test_routing_distributes_into_buckets():
    rt = RoutingTable(self_id=SELF_ID, k=3)
    c1 = make_contact_hex("80" + "00" * 31)
    c2 = make_contact_hex("00" * 31 + "01")
    c3 = make_contact_hex("00" * 16 + "01" + "00" * 15)

    assert rt.add(c1) == AddResult.ADDED
    assert rt.add(c2) == AddResult.ADDED
    assert rt.add(c3) == AddResult.ADDED

    assert len(rt) == 3
    assert rt.bucket_by_index(0) is not None
    assert rt.bucket_by_index(255) is not None
    assert rt.bucket_by_index(135) is not None


def test_routing_get_and_remove():
    rt = RoutingTable(self_id=SELF_ID, k=3)
    c1 = make_contact_hex("80" + "00" * 31)
    rt.add(c1)
    assert rt.get(c1.node_id) is not None
    assert rt.remove(c1.node_id) is True
    assert rt.get(c1.node_id) is None
    assert rt.remove(c1.node_id) is False


def test_routing_closest_sorted_by_xor_distance():
    rt = RoutingTable(self_id=SELF_ID, k=4)
    target = hexid("00" * 31 + "ff")
    c_far = make_contact_hex("80" + "00" * 31)
    c_mid = make_contact_hex("00" * 16 + "ff" + "00" * 15)
    c_near = make_contact_hex("00" * 31 + "fe")

    rt.add(c_far)
    rt.add(c_mid)
    rt.add(c_near)

    closest = rt.closest(target, count=3)
    assert closest[0].node_id == c_near.node_id
    distances = [xor_distance_int(c.node_id, target) for c in closest]
    assert distances == sorted(distances)


def test_routing_buckets_info():
    rt = RoutingTable(self_id=SELF_ID, k=2)
    rt.add(make_contact_hex("80" + "00" * 31))
    rt.add(make_contact_hex("c0" + "00" * 31))
    info = rt.buckets_info()
    assert len(info) >= 1
    for entry in info:
        assert "index" in entry
        assert "capacity" in entry
        assert "size" in entry
        assert "contacts" in entry
        assert entry["capacity"] == 2