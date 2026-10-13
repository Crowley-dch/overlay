import asyncio
import logging
import sys

from overlay.config import load_config
from overlay.transport import frame as frame_module
from overlay.transport.server import Server, echo_ping_handler


async def main() -> None:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "configs/default.yaml"
    cfg = load_config(cfg_path)

    logging.basicConfig(level=cfg.logging.level, format=cfg.logging.format)

    frame_module.configure(
        version=cfg.protocol.version,
        max_payload=cfg.protocol.max_frame_payload,
    )

    server = Server(cfg.node.host, cfg.node.port, echo_ping_handler)
    await server.start()
    print(f"Сервер запущен на {server.host}:{server.port}. Ctrl+C для выхода.")
    try:
        await asyncio.Event().wait()
    finally:
        await server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")