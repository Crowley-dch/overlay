import pytest

from overlay.crypto.hkdf import derive_directional_keys, derive_keys


def test_derive_keys_deterministic():
    a = derive_keys(b"secret", salt=b"salt", info=b"info", length=32)
    b = derive_keys(b"secret", salt=b"salt", info=b"info", length=32)
    assert a == b
    assert len(a) == 32


def test_derive_keys_different_info():
    a = derive_keys(b"secret", salt=b"salt", info=b"a", length=32)
    b = derive_keys(b"secret", salt=b"salt", info=b"b", length=32)
    assert a != b


def test_derive_keys_different_salt():
    a = derive_keys(b"secret", salt=b"s1", info=b"x", length=32)
    b = derive_keys(b"secret", salt=b"s2", info=b"x", length=32)
    assert a != b


def test_derive_keys_length_64():
    out = derive_keys(b"secret", salt=b"salt", info=b"info", length=64)
    assert len(out) == 64


def test_derive_keys_rejects_empty_secret():
    with pytest.raises(ValueError):
        derive_keys(b"", salt=b"salt", info=b"info")


def test_derive_keys_rejects_non_positive_length():
    with pytest.raises(ValueError):
        derive_keys(b"secret", salt=b"salt", info=b"info", length=0)


def test_directional_keys_are_distinct():
    node_a = b"\xaa" * 32
    node_b = b"\xbb" * 32
    key_i_to_r, key_r_to_i = derive_directional_keys(
        b"shared",
        salt=b"\x00" * 16,
        initiator_node_id=node_a,
        responder_node_id=node_b,
    )
    assert key_i_to_r != key_r_to_i
    assert len(key_i_to_r) == 32
    assert len(key_r_to_i) == 32


def test_directional_keys_swapped_roles_differ():
    node_a = b"\xaa" * 32
    node_b = b"\xbb" * 32
    key1_i_r, key1_r_i = derive_directional_keys(
        b"shared",
        salt=b"\x00" * 16,
        initiator_node_id=node_a,
        responder_node_id=node_b,
    )
    key2_i_r, key2_r_i = derive_directional_keys(
        b"shared",
        salt=b"\x00" * 16,
        initiator_node_id=node_b,
        responder_node_id=node_a,
    )
    assert key1_i_r != key2_i_r
    assert key1_i_r != key2_r_i
    assert key1_r_i != key2_i_r
    assert key1_r_i != key2_r_i