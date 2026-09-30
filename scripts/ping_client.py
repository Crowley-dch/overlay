import asyncio
import os
import secrets
import sys

from overlay.transport.connection import Connection
from overlay.transport.frame import Frame, FrameFlags, MessageType


async def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9000
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 5

    conn = await Connection.connect(host, port)
    print(f"Подключено к {conn.peer_host}:{conn.peer_port} (id={conn.connection_id})")

    try:
        for i in range(count):
            request_id = secrets.token_bytes(16)
            payload = f"ping-{i}".encode()
            frame = Frame(
                msg_type=MessageType.PING,
                request_id=request_id,
                payload=payload,
            )
            await conn.send(frame)
            print(f"→ PING {i}: request_id={request_id.hex()[:8]}...")

            response = await conn.recv()
            assert response.request_id == request_id, "request_id не совпал!"
            assert response.msg_type == MessageType.PONG
            assert response.flags & FrameFlags.IS_RESPONSE
            print(f"← PONG: payload={response.payload!r}")
    finally:
        conn.close()
        await conn.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())