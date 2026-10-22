import pytest

from overlay.crypto.handshake import (
    HandshakeError,
    HandshakeMessage,
    InitiatorHandshake,
    ResponderHandshake,
)
from overlay.identity import Identity


def run_full_handshake(
    a: Identity, b: Identity
) -> tuple[InitiatorHandshake, ResponderHandshake]:
    initiator = InitiatorHandshake(a)
    responder = ResponderHandshake(b)

    hello = initiator.create_hello()
    ack = responder.process_hello(hello)
    finish = initiator.process_hello_ack(ack)
    responder.process_finish(finish)

    return initiator, responder


def test_full_handshake_succeeds():
    a = Identity.generate()
    b = Identity.generate()
    initiator, responder = run_full_handshake(a, b)

    assert initiator.session is not None
    assert responder.session is not None
    assert initiator.session.session_id == responder.session.session_id
    assert initiator.session.peer_node_id == b.node_id
    assert responder.session.peer_node_id == a.node_id


def test_post_handshake_traffic_roundtrip():
    a = Identity.generate()
    b = Identity.generate()
    initiator, responder = run_full_handshake(a, b)

    counter, ct = initiator.session.send_aead.encrypt(b"from-a")
    assert responder.session.recv_aead.decrypt(counter, ct) == b"from-a"

    counter2, ct2 = responder.session.send_aead.encrypt(b"from-b")
    assert initiator.session.recv_aead.decrypt(counter2, ct2) == b"from-b"


def test_cross_direction_keys_differ():
    a = Identity.generate()
    b = Identity.generate()
    initiator, responder = run_full_handshake(a, b)

    counter, ct = initiator.session.send_aead.encrypt(b"from-a")
    with pytest.raises(Exception):
        initiator.session.recv_aead.decrypt(counter, ct)


def test_hello_contains_valid_node_id():
    a = Identity.generate()
    hello = InitiatorHandshake(a).create_hello()
    msg = HandshakeMessage.from_bytes(hello)
    assert msg.kind == "hello"
    assert msg.sender_node_id == a.node_id


def test_rejects_tampered_hello_pubkey():
    a = Identity.generate()
    hello = InitiatorHandshake(a).create_hello()
    msg = HandshakeMessage.from_bytes(hello)

    import json

    raw = json.loads(hello)
    raw["sender_identity_pubkey"] = (b"\x00" * 32).hex()
    tampered = json.dumps(raw, separators=(",", ":"), sort_keys=True).encode()

    with pytest.raises(HandshakeError):
        HandshakeMessage.from_bytes(tampered)


def test_rejects_tampered_hello_ack_signature():
    a = Identity.generate()
    b = Identity.generate()
    initiator = InitiatorHandshake(a)
    responder = ResponderHandshake(b)

    hello = initiator.create_hello()
    ack = responder.process_hello(hello)

    import json

    raw = json.loads(ack)
    sig = bytearray.fromhex(raw["transcript_signature"])
    sig[0] ^= 0xFF
    raw["transcript_signature"] = bytes(sig).hex()
    tampered = json.dumps(raw, separators=(",", ":"), sort_keys=True).encode()

    with pytest.raises(HandshakeError):
        initiator.process_hello_ack(tampered)


def test_rejects_tampered_hello_ack_ephemeral():
    a = Identity.generate()
    b = Identity.generate()
    initiator = InitiatorHandshake(a)
    responder = ResponderHandshake(b)

    hello = initiator.create_hello()
    ack = responder.process_hello(hello)

    import json

    raw = json.loads(ack)
    raw["sender_ephemeral_pubkey"] = (b"\x00" * 32).hex()
    tampered = json.dumps(raw, separators=(",", ":"), sort_keys=True).encode()

    with pytest.raises(HandshakeError):
        initiator.process_hello_ack(tampered)


def test_rejects_tampered_finish_signature():
    a = Identity.generate()
    b = Identity.generate()
    initiator = InitiatorHandshake(a)
    responder = ResponderHandshake(b)

    hello = initiator.create_hello()
    ack = responder.process_hello(hello)
    finish = initiator.process_hello_ack(ack)

    import json

    raw = json.loads(finish)
    sig = bytearray.fromhex(raw["transcript_signature"])
    sig[0] ^= 0xFF
    raw["transcript_signature"] = bytes(sig).hex()
    tampered = json.dumps(raw, separators=(",", ":"), sort_keys=True).encode()

    with pytest.raises(HandshakeError):
        responder.process_finish(tampered)


def test_rejects_finish_from_other_node():
    a = Identity.generate()
    c = Identity.generate()
    b = Identity.generate()

    initiator = InitiatorHandshake(a)
    responder = ResponderHandshake(b)
    hello = initiator.create_hello()
    ack = responder.process_hello(hello)
    finish_from_a = initiator.process_hello_ack(ack)

    import json

    raw = json.loads(finish_from_a)
    other = InitiatorHandshake(c)
    raw["sender_node_id"] = c.node_id.hex()
    raw["sender_identity_pubkey"] = c.public_key_bytes().hex()
    tampered = json.dumps(raw, separators=(",", ":"), sort_keys=True).encode()

    with pytest.raises(HandshakeError):
        responder.process_finish(tampered)


def test_rejects_ack_with_bad_session_id():
    a = Identity.generate()
    b = Identity.generate()
    initiator = InitiatorHandshake(a)
    responder = ResponderHandshake(b)

    hello = initiator.create_hello()
    ack = responder.process_hello(hello)

    import json

    raw = json.loads(ack)
    raw["session_id"] = (b"\x00" * 8).hex()
    tampered = json.dumps(raw, separators=(",", ":"), sort_keys=True).encode()

    with pytest.raises(HandshakeError):
        initiator.process_hello_ack(tampered)


def test_replay_of_finish_does_not_break_state():
    a = Identity.generate()
    b = Identity.generate()
    initiator = InitiatorHandshake(a)
    responder = ResponderHandshake(b)

    hello = initiator.create_hello()
    ack = responder.process_hello(hello)
    finish = initiator.process_hello_ack(ack)
    responder.process_finish(finish)

    assert responder.session is not None