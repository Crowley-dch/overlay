import pytest

from overlay.crypto.aead import (
    AeadSession,
    AuthenticationError,
    ReplayError,
)


def make_key() -> bytes:
    return bytes(range(32))


def test_encrypt_decrypt_roundtrip():
    key = make_key()
    sender = AeadSession(key)
    receiver = AeadSession(key, salt=sender.salt)
    counter, ct = sender.encrypt(b"hello")
    assert counter == 0
    pt = receiver.decrypt(counter, ct)
    assert pt == b"hello"


def test_multiple_messages_use_increasing_counter():
    sender = AeadSession(make_key())
    receiver = AeadSession(make_key(), salt=sender.salt)
    for i in range(5):
        counter, ct = sender.encrypt(f"msg-{i}".encode())
        assert counter == i
        pt = receiver.decrypt(counter, ct)
        assert pt == f"msg-{i}".encode()


def test_empty_plaintext():
    sender = AeadSession(make_key())
    receiver = AeadSession(make_key(), salt=sender.salt)
    counter, ct = sender.encrypt(b"")
    assert receiver.decrypt(counter, ct) == b""


def test_aad_is_authenticated():
    sender = AeadSession(make_key())
    receiver = AeadSession(make_key(), salt=sender.salt)
    counter, ct = sender.encrypt(b"msg", aad=b"header")
    with pytest.raises(AuthenticationError):
        receiver.decrypt(counter, ct, aad=b"different")


def test_wrong_counter_does_not_decrypt_correctly():
    sender = AeadSession(make_key())
    receiver = AeadSession(make_key(), salt=sender.salt)
    counter, ct = sender.encrypt(b"msg")
    wrong_counter = 5
    with pytest.raises(AuthenticationError):
        receiver.decrypt(wrong_counter, ct)


def test_tampered_ciphertext_rejected():
    sender = AeadSession(make_key())
    receiver = AeadSession(make_key(), salt=sender.salt)
    counter, ct = sender.encrypt(b"hello")
    tampered = bytearray(ct)
    tampered[0] ^= 0xFF
    with pytest.raises(AuthenticationError):
        receiver.decrypt(counter, bytes(tampered))


def test_replay_with_same_counter_rejected():
    sender = AeadSession(make_key())
    receiver = AeadSession(make_key(), salt=sender.salt)
    counter, ct = sender.encrypt(b"hello")
    receiver.decrypt(counter, ct)
    with pytest.raises(ReplayError):
        receiver.decrypt(counter, ct)


def test_replay_with_lower_counter_rejected():
    sender = AeadSession(make_key())
    receiver = AeadSession(make_key(), salt=sender.salt)
    c0, ct0 = sender.encrypt(b"m0")
    c1, ct1 = sender.encrypt(b"m1")
    receiver.decrypt(c1, ct1)
    with pytest.raises(ReplayError):
        receiver.decrypt(c0, ct0)


def test_different_algorithms_roundtrip():
    for algo in ("aes-gcm", "chacha20poly1305"):
        sender = AeadSession(make_key(), algorithm=algo)
        receiver = AeadSession(make_key(), algorithm=algo, salt=sender.salt)
        counter, ct = sender.encrypt(b"msg")
        assert receiver.decrypt(counter, ct) == b"msg"


def test_mismatched_salt_fails():
    sender = AeadSession(make_key())
    receiver = AeadSession(make_key())
    counter, ct = sender.encrypt(b"msg")
    with pytest.raises(AuthenticationError):
        receiver.decrypt(counter, ct)


def test_rejects_bad_key_length():
    with pytest.raises(ValueError):
        AeadSession(b"short")


def test_rejects_unknown_algorithm():
    with pytest.raises(ValueError):
        AeadSession(make_key(), algorithm="rc4")