import asyncio
import secrets

from overlay.transport.connection import Connection
from overlay.transport.frame import Frame, MessageType
from overlay.transport.server import Server, echo_ping_handler


async def main() -> None:
    print("start server", flush=True)
    server = Server("127.0.0.1", 0, echo_ping_handler)
    await server.start()
    print(f"server port = {server.port}", flush=True)

    print("connect", flush=True)
    conn = await Connection.connect("127.0.0.1", server.port)
    print(f"connected: {conn}", flush=True)

    rid = secrets.token_bytes(16)
    print("send PING", flush=True)
    await conn.send(
        Frame(msg_type=MessageType.PING, request_id=rid, payload=b"hello")
    )
    print("wait for PONG", flush=True)
    resp = await conn.recv()
    print(f"got: {resp}", flush=True)
    print(f"payload = {resp.payload!r}", flush=True)

    conn.close()
    await conn.wait_closed()
    print("stop server", flush=True)
    await server.stop()
    print("done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())