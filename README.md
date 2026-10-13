# Overlay — защищённая оверлейная P2P-сеть

Курсовая работа. Тема: «Защищённая оверлейная сеть передачи данных на основе пиринговых протоколов». Уровень — продвинутый.

Проект — воспроизводимый программный прототип узла децентрализованной оверлейной сети поверх TCP/IP. Не промышленная система. Оцениваются корректность сетевых и криптографических механизмов, архитектурная обоснованность, качество испытаний и способность интерпретировать результаты.

## Требования

- Python 3.11+
- Linux
- Docker и Docker Compose (для развёртывания сети из `N` узлов, этап 6+)

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```
## Запуск тестов 
```bash
pytest -v
pytest tests/test_frame.py -v
pytest tests/test_connection.py -v
pytest tests/test_rpc.py -v
pytest tests/test_identity.py -v
pytest tests/test_xor.py -v
pytest tests/test_kbucket.py -v
pytest tests/test_routing.py -v
pytest tests/test_manager.py -v
```

## Проверка Ping/Pong 
### терминал 1 
```bash
python scripts/ping_server.py
```
### терминал 2
```bash
python scripts/ping_client.py
```
Сервер отвечает PONG на каждый PING. Клиент шлёт 5 пингов и печатает ответы. Завершение — Ctrl+C.

Дополнительно есть минимальный smoke-тест, который поднимает сервер на случайном порту и проверяет один обмен:
```bash
python scripts/smoke_ping.py
```
## Конфигурация 
Все параметры вынесены в YAML и не требуют правки кода:
```yaml
# configs/default.yaml
protocol:
  version: 1
  max_frame_payload: 65536

transport:
  connect_timeout: 5.0
  write_timeout: 5.0

node:
  host: "127.0.0.1"
  port: 9000
  state_dir: "data/node-0"

logging:
  level: "INFO"
  format: "%(asctime)s %(levelname)s %(name)s: %(message)s"
```

## Структура 
```text
overlay/
├── configs/              конфигурации экспериментов
├── data/                 состояние узлов и сырые данные (не коммитится)
├── scripts/              запускаемые сценарии
│   ├── ping_server.py
│   ├── ping_client.py
│   └── smoke_ping.py
├── src/overlay/
│   ├── config.py         загрузка YAML в dataclass
│   ├── transport/        TCP, кадрирование, RPC
│   │   ├── frame.py      заголовок 24 байта, FrameBuffer
│   │   ├── connection.py асинхронное соединение
│   │   ├── server.py     приём соединений
│   │   └── rpc.py        корреляция запросов по request_id
│   ├── identity/         Ed25519, NodeID, хранилище
│   │   ├── node_id.py    SHA-256(canonical_encode(pubkey))
│   │   ├── keypair.py    обёртка над Ed25519
│   │   └── store.py      load_or_create с правами 0600
│   └── dht/              Kademlia
│       ├── xor.py        XOR-метрика, bucket_index
│       ├── contact.py    Contact(node_id, host, port)
│       ├── kbucket.py    k-bucket с LRU
│       ├── routing.py    таблица из 256 бакетов
│       └── manager.py    ContactManager с PING-проверкой LRU
└── tests/                unit- и integration-тесты
```