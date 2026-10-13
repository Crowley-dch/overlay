import asyncio
import secrets

from overlay.dht.contact import Contact
from overlay.dht.kbucket import AddResult
from overlay.dht.manager import ContactManager
from overlay.transport.server import Server, echo_ping_handler


def _run(coro):
    return asyncio.run(coro)


def _random_id() -> bytes:
    return secrets.token_bytes(32)


def _same_bucket_ids(self_id: bytes, count: int) -> list[bytes]:
    # XOR последнего байта с 0b100..0b111
    # старший различающийся бит — позиция 2 → bucket_index = 253
    return [
        self_id[:-1] + bytes([self_id[-1] ^ (0b100 + i)])
        for i in range(count)
    ]


async def _start_server() -> Server:
    server = Server("127.0.0.1", 0, echo_ping_handler)
    await server.start()
    return server


async def _stop_server(server: Server) -> None:
    await server.stop()


def test_add_contact_when_bucket_not_full():
    async def scenario():
        manager = ContactManager(self_id=_random_id(), k=3)
        c1 = Contact(node_id=_random_id(), host="127.0.0.1", port=1)
        c2 = Contact(node_id=_random_id(), host="127.0.0.1", port=2)

        r1 = await manager.add_contact(c1)
        r2 = await manager.add_contact(c2)
        assert r1 == AddResult.ADDED
        assert r2 == AddResult.ADDED
        assert len(manager.routing) == 2

    _run(scenario())


def test_add_self_raises():
    async def scenario():
        manager = ContactManager(self_id=_random_id(), k=3)
        self_contact = Contact(
            node_id=manager.self_id, host="127.0.0.1", port=1
        )
        try:
            await manager.add_contact(self_contact)
            raised = False
        except ValueError:
            raised = True
        assert raised

    _run(scenario())


def test_live_lru_is_kept_and_new_contact_rejected():
    async def scenario():
        server = await _start_server()
        try:
            self_id = _random_id()
            manager = ContactManager(
                self_id=self_id,
                k=3,
                connect_timeout=1.0,
                ping_timeout=1.0,
            )

            ids = _same_bucket_ids(self_id, 3)
            for node_id in ids:
                c = Contact(
                    node_id=node_id,
                    host="127.0.0.1",
                    port=server.port,
                )
                await manager.add_contact(c)

            assert len(manager.routing) == 3
            bucket = manager.routing.bucket_by_index(253)
            assert bucket is not None
            assert len(bucket) == 3
            lru_before = bucket.least_recent()
            assert lru_before.node_id == ids[0]

            new_id = _same_bucket_ids(self_id, 4)[3]
            new_contact = Contact(
                node_id=new_id,
                host="127.0.0.1",
                port=server.port,
            )
            result = await manager.add_contact(new_contact)

            assert result == AddResult.FULL
            bucket = manager.routing.bucket_by_index(253)
            assert bucket is not None
            assert len(bucket) == 3
            assert bucket.most_recent().node_id == ids[0]
            assert bucket.get(new_id) is None
        finally:
            await _stop_server(server)

    _run(scenario())


def test_dead_lru_is_replaced():
    async def scenario():
        live_server = await _start_server()
        try:
            self_id = _random_id()
            manager = ContactManager(
                self_id=self_id,
                k=3,
                connect_timeout=0.5,
                ping_timeout=0.5,
            )

            ids = _same_bucket_ids(self_id, 3)
            for node_id in ids:
                c = Contact(
                    node_id=node_id,
                    host="127.0.0.1",
                    port=live_server.port,
                )
                await manager.add_contact(c)

            bucket = manager.routing.bucket_by_index(253)
            assert bucket is not None
            assert bucket.least_recent().node_id == ids[0]

            dead_server = await _start_server()
            dead_port = dead_server.port
            await dead_server.stop()

            dead_contact = bucket.get(ids[0])
            assert dead_contact is not None
            dead_contact.port = dead_port

            new_id = _same_bucket_ids(self_id, 4)[3]
            new_contact = Contact(
                node_id=new_id,
                host="127.0.0.1",
                port=live_server.port,
            )
            result = await manager.add_contact(new_contact)

            assert result == AddResult.ADDED
            bucket = manager.routing.bucket_by_index(253)
            assert bucket is not None
            assert len(bucket) == 3
            assert bucket.get(new_id) is not None
            assert bucket.get(ids[0]) is None
        finally:
            await _stop_server(live_server)

    _run(scenario())


def test_ping_unreachable_returns_false():
    async def scenario():
        manager = ContactManager(
            self_id=_random_id(),
            k=3,
            connect_timeout=0.5,
            ping_timeout=0.5,
        )
        c = Contact(node_id=_random_id(), host="127.0.0.1", port=1)
        alive = await manager.ping(c)
        assert alive is False

    _run(scenario())


def test_ping_live_returns_true():
    async def scenario():
        server = await _start_server()
        try:
            manager = ContactManager(
                self_id=_random_id(),
                k=3,
                connect_timeout=1.0,
                ping_timeout=1.0,
            )
            c = Contact(
                node_id=_random_id(),
                host="127.0.0.1",
                port=server.port,
            )
            alive = await manager.ping(c)
            assert alive is True
        finally:
            await _stop_server(server)

    _run(scenario())