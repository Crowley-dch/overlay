import asyncio
import logging

from overlay.transport.server import Server, echo_ping_handler


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    server = Server("127.0.0.1", 9000, echo_ping_handler)
    await server.start()
    print(f"Сервер запущен на {server.host}:{server.port}. Ctrl+C для выхода.")
    try:
        await asyncio.Event().wait()  # ждём вечно
    finally:
        await server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")