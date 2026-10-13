import asyncio
import secrets

from overlay.transport.connection import Connection
from overlay.transport.frame import Frame, FrameFlags, MessageType
from overlay.transport.server import Server, echo_ping_handler


def test_ping_pong():
    async def scenario():
        server = Server("127.0.0.1", 0, echo_ping_handler)
        await server.start()
        try:
            conn = await Connection.connect("127.0.0.1", server.port)
            try:
                request_id = secrets.token_bytes(16)
                await conn.send(
                    Frame(
                        msg_type=MessageType.PING,
                        request_id=request_id,
                        payload=b"hello",
                    )
                )
                response = await conn.recv()
                assert response.msg_type == MessageType.PONG
                assert response.request_id == request_id
                assert response.payload == b"hello"
                assert response.flags & FrameFlags.IS_RESPONSE
            finally:
                conn.close()
                await conn.wait_closed()
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_multiple_pings_one_connection():
    async def scenario():
        server = Server("127.0.0.1", 0, echo_ping_handler)
        await server.start()
        try:
            conn = await Connection.connect("127.0.0.1", server.port)
            try:
                for i in range(10):
                    rid = secrets.token_bytes(16)
                    await conn.send(
                        Frame(
                            msg_type=MessageType.PING,
                            request_id=rid,
                            payload=str(i).encode(),
                        )
                    )
                    resp = await conn.recv()
                    assert resp.request_id == rid
                    assert resp.payload == str(i).encode()
            finally:
                conn.close()
                await conn.wait_closed()
        finally:
            await server.stop()

    asyncio.run(scenario())

def test_server_survives_bad_client_and_others_work():
    import asyncio
    import secrets
    import struct

    from overlay.transport.frame import PROTOCOL_VERSION

    async def scenario():
        server = Server("127.0.0.1", 0, echo_ping_handler)
        await server.start()
        try:
            good = await Connection.connect("127.0.0.1", server.port)
            try:
                bad = await Connection.connect("127.0.0.1", server.port)
                try:
                    header = struct.pack(
                        "!BBH16sI",
                        PROTOCOL_VERSION + 1,
                        MessageType.PING,
                        0,
                        secrets.token_bytes(16),
                        0,
                    )
                    bad._writer.write(header)
                    await bad._writer.drain()
                    await asyncio.sleep(0.1)
                finally:
                    bad.close()
                    await bad.wait_closed()

                for i in range(5):
                    rid = secrets.token_bytes(16)
                    await good.send(
                        Frame(
                            msg_type=MessageType.PING,
                            request_id=rid,
                            payload=str(i).encode(),
                        )
                    )
                    resp = await good.recv()
                    assert resp.request_id == rid
                    assert resp.payload == str(i).encode()
            finally:
                good.close()
                await good.wait_closed()
        finally:
            await server.stop()

    asyncio.run(scenario())