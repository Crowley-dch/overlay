from __future__ import annotations

import enum
import struct
from dataclasses import dataclass, field

PROTOCOL_VERSION: int = 1
MAX_FRAME_PAYLOAD: int = 65_536

HEADER_FORMAT = "!BBH16sI"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)  # = 24

assert HEADER_SIZE == 24, f"Ожидалось 24 байта, получено {HEADER_SIZE}"


class MessageType(enum.IntEnum):

    PING = 0x01
    PONG = 0x02
    FIND_NODE_REQUEST = 0x10
    FIND_NODE_RESPONSE = 0x11
    STORE_REQUEST = 0x20
    STORE_RESPONSE = 0x21
    FIND_VALUE_REQUEST = 0x30
    FIND_VALUE_RESPONSE = 0x31
    TUNNEL_BUILD = 0x40
    TUNNEL_BUILD_OK = 0x41
    TUNNEL_BUILD_FAIL = 0x42
    TUNNEL_DATA = 0x50
    TUNNEL_ACK = 0x51
    TUNNEL_CLOSE = 0x52
    APP_MESSAGE = 0x60
    APP_ACK = 0x61
    ERROR = 0xFF


class FrameFlags(enum.IntFlag):

    NONE = 0x0000
    IS_RESPONSE = 0x0001
    IS_ERROR = 0x0002
    IS_FRAGMENT = 0x0004


class FrameError(Exception):
    """Ошибка кадрирования. Соединение после неё обычно закрывается."""


@dataclass(slots=True)
class Frame:

    msg_type: int
    request_id: bytes
    payload: bytes = b""
    version: int = PROTOCOL_VERSION
    flags: int = 0

    def __post_init__(self) -> None:
        if len(self.request_id) != 16:
            raise FrameError(
                f"request_id должен быть 16 байт, получено {len(self.request_id)}"
            )
        if len(self.payload) > MAX_FRAME_PAYLOAD:
            raise FrameError(
                f"payload превышает MAX_FRAME_PAYLOAD "
                f"({len(self.payload)} > {MAX_FRAME_PAYLOAD})"
            )

    def encode(self) -> bytes:
        header = struct.pack(
            HEADER_FORMAT,
            self.version,
            self.msg_type,
            self.flags,
            self.request_id,
            len(self.payload),
        )
        return header + self.payload

    @classmethod
    def decode(cls, data: bytes) -> "Frame":
        if len(data) < HEADER_SIZE:
            raise FrameError(f"Слишком короткий буфер: {len(data)} < {HEADER_SIZE}")

        version, msg_type, flags, request_id, payload_length = struct.unpack(
            HEADER_FORMAT, data[:HEADER_SIZE]
        )

        if payload_length > MAX_FRAME_PAYLOAD:
            raise FrameError(
                f"payload_length {payload_length} > MAX_FRAME_PAYLOAD "
                f"{MAX_FRAME_PAYLOAD}"
            )

        total = HEADER_SIZE + payload_length
        if len(data) < total:
            raise FrameError(
                f"Неполный кадр: есть {len(data)} байт, нужно {total}"
            )

        payload = data[HEADER_SIZE:total]
        return cls(
            version=version,
            msg_type=msg_type,
            flags=flags,
            request_id=request_id,
            payload=payload,
        )

    @property
    def total_size(self) -> int:
        return HEADER_SIZE + len(self.payload)


class FrameBuffer:

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        if not data:
            return []
        self._buf.extend(data)
        frames: list[Frame] = []

        while True:
            if len(self._buf) < HEADER_SIZE:
                break  

            version, msg_type, flags, request_id, payload_length = struct.unpack(
                HEADER_FORMAT, self._buf[:HEADER_SIZE]
            )

            if payload_length > MAX_FRAME_PAYLOAD:
                self._buf.clear()
                raise FrameError(
                    f"payload_length {payload_length} > MAX_FRAME_PAYLOAD "
                    f"{MAX_FRAME_PAYLOAD}"
                )

            total = HEADER_SIZE + payload_length
            if len(self._buf) < total:
                break  

            payload = bytes(self._buf[HEADER_SIZE:total])
            frames.append(
                Frame(
                    version=version,
                    msg_type=msg_type,
                    flags=flags,
                    request_id=request_id,
                    payload=payload,
                )
            )
            del self._buf[:total]

        return frames

    def buffered_bytes(self) -> int:
        return len(self._buf)