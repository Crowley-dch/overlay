import asyncio
import secrets
import sys

from overlay.config import load_config
from overlay.transport import frame as frame_module
from overlay.transport.connection import Connection
from overlay.transport.frame import Frame, FrameFlags, MessageType


async def main() -> None:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "configs/default.yaml"
    host = sys.argv[2] if len(sys.argv) > 2 else None
    port = int(sys.argv[3]) if len(sys.argv) > 3 else None
    count = int(sys.argv[4]) if len(sys.argv) > 4 else 5

    cfg = load_config(cfg_path)
    frame_module.configure(
        version=cfg.protocol.version,
        max_payload=cfg.protocol.max_frame_payload,
    )

    if host is None:
        host = cfg.node.host
    if port is None:
        port = cfg.node.port

    conn = await Connection.connect(
        host,
        port,
        connect_timeout=cfg.transport.connect_timeout,
        write_timeout=cfg.transport.write_timeout,
    )
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