import os
from pathlib import Path

import pytest

from overlay.identity import (
    NODE_ID_LENGTH,
    Identity,
    IdentityStore,
    compute_node_id_from_bytes,
    node_id_from_hex,
    node_id_to_hex,
    verify_node_id,
    verify_signature,
)


def test_node_id_length():
    identity = Identity.generate()
    assert len(identity.node_id) == NODE_ID_LENGTH


def test_node_id_deterministic_from_same_key():
    identity = Identity.generate()
    pub_bytes = identity.public_key_bytes()
    a = compute_node_id_from_bytes(pub_bytes)
    b = compute_node_id_from_bytes(pub_bytes)
    assert a == b
    assert a == identity.node_id


def test_two_identities_have_different_node_ids():
    a = Identity.generate()
    b = Identity.generate()
    assert a.node_id != b.node_id
    assert a.public_key_bytes() != b.public_key_bytes()


def test_identity_pem_roundtrip(tmp_path: Path):
    store = IdentityStore(tmp_path / "node-a")
    original = store.load_or_create()
    loaded = store.load()
    assert loaded.node_id == original.node_id
    assert loaded.public_key_bytes() == original.public_key_bytes()


def test_two_state_dirs_are_isolated(tmp_path: Path):
    store_a = IdentityStore(tmp_path / "a")
    store_b = IdentityStore(tmp_path / "b")
    id_a = store_a.load_or_create()
    id_b = store_b.load_or_create()
    assert id_a.node_id != id_b.node_id

    assert store_a.load_or_create().node_id == id_a.node_id
    assert store_b.load_or_create().node_id == id_b.node_id


def test_identity_file_permissions(tmp_path: Path):
    store = IdentityStore(tmp_path / "node")
    store.load_or_create()
    mode = os.stat(store.identity_path).st_mode & 0o777
    assert mode == 0o600


def test_sign_verify_roundtrip():
    identity = Identity.generate()
    data = b"important message"
    sig = identity.sign(data)
    assert identity.verify(sig, data) is True
    assert verify_signature(identity.public_key_bytes(), sig, data) is True


def test_verify_with_wrong_key_fails():
    a = Identity.generate()
    b = Identity.generate()
    data = b"important message"
    sig = a.sign(data)
    assert verify_signature(b.public_key_bytes(), sig, data) is False


def test_verify_with_modified_data_fails():
    identity = Identity.generate()
    sig = identity.sign(b"original")
    assert verify_signature(identity.public_key_bytes(), sig, b"modified") is False


def test_verify_node_id_accepts_matching_pair():
    identity = Identity.generate()
    assert verify_node_id(identity.node_id, identity.public_key_bytes()) is True


def test_verify_node_id_rejects_mismatch():
    a = Identity.generate()
    b = Identity.generate()
    assert verify_node_id(a.node_id, b.public_key_bytes()) is False


def test_verify_node_id_rejects_wrong_length():
    identity = Identity.generate()
    assert verify_node_id(b"\x00" * 16, identity.public_key_bytes()) is False


def test_verify_node_id_rejects_bad_public_key_length():
    assert verify_node_id(b"\x00" * 32, b"\x00" * 16) is False


def test_node_id_hex_roundtrip():
    identity = Identity.generate()
    as_hex = node_id_to_hex(identity.node_id)
    assert len(as_hex) == 64
    assert node_id_from_hex(as_hex) == identity.node_id


def test_node_id_from_hex_rejects_wrong_length():
    with pytest.raises(ValueError):
        node_id_from_hex("abcd")


def test_node_id_to_hex_rejects_wrong_length():
    with pytest.raises(ValueError):
        node_id_to_hex(b"\x00" * 16)


def test_load_missing_raises(tmp_path: Path):
    store = IdentityStore(tmp_path / "missing")
    with pytest.raises(FileNotFoundError):
        store.load()