import asyncio
import secrets

from overlay.transport.connection import Connection
from overlay.transport.frame import Frame, FrameFlags, MessageType
from overlay.transport.rpc import RpcClient, RequestTimeout
from overlay.transport.server import Server, echo_ping_handler


def _run(coro):
    return asyncio.run(coro)


def test_request_returns_matching_response():
    async def scenario():
        server = Server("127.0.0.1", 0, echo_ping_handler)
        await server.start()
        try:
            conn = await Connection.connect("127.0.0.1", server.port)
            rpc = RpcClient(conn, default_timeout=2.0)
            await rpc.start()
            try:
                response = await rpc.request(
                    MessageType.PING,
                    payload=b"abc",
                    expected_response_types={MessageType.PONG},
                )
                assert response.msg_type == MessageType.PONG
                assert response.payload == b"abc"
                assert response.flags & FrameFlags.IS_RESPONSE
            finally:
                await rpc.stop()
                conn.close()
                await conn.wait_closed()
        finally:
            await server.stop()

    _run(scenario())


def test_response_with_unknown_request_id_is_ignored():
    async def scenario():
        server = Server("127.0.0.1", 0, echo_ping_handler)
        await server.start()
        try:
            conn = await Connection.connect("127.0.0.1", server.port)
            rpc = RpcClient(conn, default_timeout=0.5)
            await rpc.start()
            try:
                try:
                    await rpc.request(
                        MessageType.PING,
                        payload=b"x",
                        expected_response_types={MessageType.FIND_NODE_RESPONSE},
                    )
                    raised = False
                except RequestTimeout:
                    raised = True
                assert raised, "Ожидался RequestTimeout"
            finally:
                await rpc.stop()
                conn.close()
                await conn.wait_closed()
        finally:
            await server.stop()

    _run(scenario())


def test_parallel_requests_are_correlated():
    async def scenario():
        server = Server("127.0.0.1", 0, echo_ping_handler)
        await server.start()
        try:
            conn = await Connection.connect("127.0.0.1", server.port)
            rpc = RpcClient(conn, default_timeout=2.0)
            await rpc.start()
            try:
                async def one(i: int) -> bytes:
                    payload = f"p{i}".encode()
                    resp = await rpc.request(
                        MessageType.PING,
                        payload=payload,
                        expected_response_types={MessageType.PONG},
                    )
                    assert resp.payload == payload
                    return resp.payload

                results = await asyncio.gather(*(one(i) for i in range(20)))
                assert sorted(results) == sorted(f"p{i}".encode() for i in range(20))
            finally:
                await rpc.stop()
                conn.close()
                await conn.wait_closed()
        finally:
            await server.stop()

    _run(scenario())