import struct
import pytest

from overlay.transport.frame import (
    HEADER_SIZE,
    MAX_FRAME_PAYLOAD,
    PROTOCOL_VERSION,
    Frame,
    FrameBuffer,
    FrameError,
    FrameFlags,
    MessageType,
)

def make_request_id(seed: int = 0) -> bytes:
    return bytes([seed] * 16)

def test_frame_roundtrip():
    original = Frame(
        msg_type=MessageType.PING,
        request_id=make_request_id(0xAB),
        payload=b"hello",
        flags=FrameFlags.NONE,
    )
    encoded = original.encode()
    assert len(encoded) == HEADER_SIZE + 5

    decoded = Frame.decode(encoded)
    assert decoded.version == PROTOCOL_VERSION
    assert decoded.msg_type == MessageType.PING
    assert decoded.request_id == make_request_id(0xAB)
    assert decoded.payload == b"hello"
    assert decoded.flags == 0


def test_frame_empty_payload():
    f = Frame(msg_type=MessageType.PONG, request_id=make_request_id(1))
    decoded = Frame.decode(f.encode())
    assert decoded.payload == b""


def test_frame_max_payload():
    payload = b"x" * MAX_FRAME_PAYLOAD
    f = Frame(
        msg_type=MessageType.TUNNEL_DATA,
        request_id=make_request_id(2),
        payload=payload,
    )
    decoded = Frame.decode(f.encode())
    assert len(decoded.payload) == MAX_FRAME_PAYLOAD

def test_frame_rejects_bad_request_id():
    with pytest.raises(FrameError):
        Frame(msg_type=MessageType.PING, request_id=b"short")


def test_frame_rejects_oversized_payload():
    with pytest.raises(FrameError):
        Frame(
            msg_type=MessageType.PING,
            request_id=make_request_id(3),
            payload=b"x" * (MAX_FRAME_PAYLOAD + 1),
        )

def test_decode_rejects_short_buffer():
    with pytest.raises(FrameError):
        Frame.decode(b"\x00" * (HEADER_SIZE - 1))


def test_decode_rejects_incomplete_payload():
    f = Frame(msg_type=MessageType.PING, request_id=make_request_id(4), payload=b"abc")
    encoded = f.encode()
    with pytest.raises(FrameError):
        Frame.decode(encoded[:-1])


def test_decode_rejects_oversized_length_header_only():
    """Заголовок с payload_length больше лимита отклоняется до чтения payload."""
    header = struct.pack(
        "!BBH16sI",
        PROTOCOL_VERSION,
        MessageType.PING,
        0,
        make_request_id(5),
        MAX_FRAME_PAYLOAD + 1,
    )
    with pytest.raises(FrameError):
        Frame.decode(header)

def test_buffer_single_frame():
    buf = FrameBuffer()
    f = Frame(msg_type=MessageType.PING, request_id=make_request_id(6), payload=b"hi")
    frames = buf.feed(f.encode())
    assert len(frames) == 1
    assert frames[0].payload == b"hi"


def test_buffer_partial_header():
    buf = FrameBuffer()
    f = Frame(msg_type=MessageType.PING, request_id=make_request_id(7), payload=b"data")
    encoded = f.encode()

    frames: list[Frame] = []
    for i in range(len(encoded) - 1):
        frames.extend(buf.feed(encoded[i : i + 1]))
        assert frames == []  

    frames.extend(buf.feed(encoded[-1:]))
    assert len(frames) == 1
    assert frames[0].payload == b"data"


def test_buffer_partial_payload():
    buf = FrameBuffer()
    f = Frame(msg_type=MessageType.PING, request_id=make_request_id(8), payload=b"abcdef")
    encoded = f.encode()

    frames = buf.feed(encoded[: HEADER_SIZE + 3])
    assert frames == []

    frames = buf.feed(encoded[HEADER_SIZE + 3 :])
    assert len(frames) == 1
    assert frames[0].payload == b"abcdef"


def test_buffer_multiple_frames_one_read():
    buf = FrameBuffer()
    f1 = Frame(msg_type=MessageType.PING, request_id=make_request_id(9), payload=b"one")
    f2 = Frame(msg_type=MessageType.PONG, request_id=make_request_id(10), payload=b"two")
    f3 = Frame(msg_type=MessageType.ERROR, request_id=make_request_id(11))

    frames = buf.feed(f1.encode() + f2.encode() + f3.encode())
    assert len(frames) == 3
    assert [fr.payload for fr in frames] == [b"one", b"two", b""]


def test_buffer_glued_and_split_reads():
    buf = FrameBuffer()
    f1 = Frame(msg_type=MessageType.PING, request_id=make_request_id(12), payload=b"AA")
    f2 = Frame(msg_type=MessageType.PONG, request_id=make_request_id(13), payload=b"BB")
    stream = f1.encode() + f2.encode()

    cut = HEADER_SIZE + 2 + HEADER_SIZE
    frames = buf.feed(stream[:cut])
    assert len(frames) == 1
    assert frames[0].payload == b"AA"

    frames = buf.feed(stream[cut:])
    assert len(frames) == 1
    assert frames[0].payload == b"BB"


def test_buffer_rejects_oversized_length():
    buf = FrameBuffer()
    header = struct.pack(
        "!BBH16sI",
        PROTOCOL_VERSION,
        MessageType.PING,
        0,
        make_request_id(14),
        MAX_FRAME_PAYLOAD + 1,
    )
    with pytest.raises(FrameError):
        buf.feed(header)


def test_buffer_buffered_bytes():
    buf = FrameBuffer()
    buf.feed(b"\x00\x01\x02")
    assert buf.buffered_bytes() == 3