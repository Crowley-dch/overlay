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
pytest tests/test_record.py -v
pytest tests/test_store.py -v
pytest tests/test_store_integration.py -v
pytest tests/test_maintenance.py -v
pytest tests/test_hkdf.py -v
pytest tests/test_aead.py -v
pytest tests/test_handshake.py -v
pytest tests/test_secure.py -v
pytest tests/test_tunnel.py -v
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

## Проверка bootstrap-схем
Запуск сети из 10 узлов по схеме star:
```bash 
python scripts/run_bootstrap.py configs/star.yaml 10
```
По схеме ring:
```bash 
python scripts/run_bootstrap.py configs/ring.yaml 10
```
Обе схемы отличаются только полем bootstrap.scheme. Результаты сохраняются в data/runs/bootstrap-<scheme>-<timestamp>/:
- manifest.json — параметры прогона, node_ids, launch_order;
- routing.json — снимок k-bucket каждого узла (buckets_info);
- convergence.json — два раунда контрольных lookup с iterations, rpc_count, timeouts, duration;
- node-XX/identity.pem — приватные ключи (не коммитятся).

Ожидаемый вывод в конце:
```text
Схема: star, узлов: 10
Успешных lookup в последнем раунде: 100.0%
Среднее число контактов в таблице: 8.1
```
## Проверка защищённой сессии и туннелей
Защищённая сессия между двумя узлами проверяется автоматически в tests/test_secure.py:

```bash
pytest tests/test_secure.py -v
```
Туннели проверяются в tests/test_tunnel.py:
```bash
pytest tests/test_tunnel.py -v
```
## Конфигурация 
Все параметры вынесены в YAML и не требуют правки кода:
```yaml
# configs/star.yaml
protocol:
  version: 1
  max_frame_payload: 65536

transport:
  connect_timeout: 5.0
  write_timeout: 5.0

node:
  host: "127.0.0.1"
  port: 0
  state_dir: "data/network"

dht:
  k: 4
  alpha: 3
  replication_factor: 3

ttl:
  record_ttl: 180
  republish_interval: 80
  max_record_size: 4096

bootstrap:
  scheme: "star"
  n_nodes: 5

logging:
  level: "INFO"
  format: "%(asctime)s %(levelname)s %(name)s: %(message)s"
```

## Структура 
```text
overlay/
├── ARCHITECTURE.md          проектное решение и границы этапов
├── PROTOCOL.md              спецификация кадра и типов
├── configs/
│   ├── star.yaml            star-схема
│   ├── ring.yaml            ring-схема
│   └── default.yaml         прежний конфиг
├── data/                    состояние узлов и сырые данные (не коммитится)
├── scripts/
│   ├── ping_server.py
│   ├── ping_client.py
│   ├── smoke_ping.py
│   └── run_bootstrap.py     раннер bootstrap-схем
├── src/overlay/
│   ├── config.py            загрузка YAML в dataclass
│   ├── node.py              объединяет identity, DHT, store, tunnel
│   ├── transport/
│   │   ├── frame.py         заголовок 24 байта, FrameBuffer
│   │   ├── connection.py    асинхронное соединение
│   │   ├── server.py        приём соединений
│   │   ├── rpc.py           корреляция запросов по request_id
│   │   ├── secure.py        SecureConnection (handshake + AEAD)
│   │   └── types.py         TransportLike-протокол
│   ├── identity/
│   │   ├── node_id.py       SHA-256(canonical_encode(pubkey))
│   │   ├── keypair.py       обёртка над Ed25519
│   │   └── store.py         load_or_create с правами 0600
│   ├── crypto/              примитивы учебного AKE
│   │   ├── hkdf.py          HKDF-SHA-256, направленные ключи
│   │   ├── aead.py          AES-GCM и ChaCha20-Poly1305, replay-защита
│   │   └── handshake.py     трёхшаговый AKE hello/ack/finish
│   ├── dht/
│   │   ├── xor.py           XOR-метрика, bucket_index
│   │   ├── contact.py       Contact(node_id, host, port)
│   │   ├── kbucket.py       k-bucket с LRU
│   │   ├── routing.py       таблица из 256 бакетов
│   │   ├── manager.py       ContactManager + ping_fn
│   │   ├── lookup.py        итеративный lookup ALPHA=3
│   │   ├── wire.py          JSON-сериализация FIND_NODE/STORE/FIND_VALUE
│   │   ├── record.py        NodeRecord с подписью и валидацией
│   │   └── store.py         RecordStore с TTL и rollback-защитой
│   ├── tunnel/              оверлейная передача
│   │   ├── state.py         TunnelState
│   │   ├── route.py         TunnelRoute и RelayState
│   │   └── wire.py          TUNNEL_BUILD/DATA/ACK/CLOSE
│   ├── bootstrap.py         star/ring планы
│   └── metrics/
│       └── writer.py        new_run_dir, write_json
└── tests/
```