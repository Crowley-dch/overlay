# Архитектура

## 1. Проектное решение

| Параметр | Значение | Обоснование |
|---|---|---|
| Уровень | продвинутый | собственная защищённая сессия, ≥ 3 ретранслятора, файлы, анализ Sybil/Eclipse |
| Язык | Python 3.11+ | `asyncio` даёт конкурентные соединения без потоков; зрелая библиотека `cryptography` |
| Транспорт | TCP | сохраняет порядок байтов, не требует собственной надёжности поверх UDP |
| Конкурентность | `asyncio` | сотни соединений в одном event loop, простое разделяемое состояние |
| Сериализация | JSON | читаемость и простота отладки на этапах 1–2; сравнение с CBOR/msgpack — опциональный E1-8 |
| Криптография | `cryptography` (OpenSSL) | Ed25519, X25519, HKDF, AES-GCM, ChaCha20-Poly1305 |
| Децентрализация | Kademlia DHT | XOR-метрика и k-bucket, отсутствие центрального каталога |

## 2. Целевые параметры сети

| Параметр | Значение (продвинутый) |
|---|---|
| `N` (число узлов) | 20–21 |
| `K_BUCKET_SIZE` (`k`) | 4 |
| `ALPHA` | 3 |
| `R` (репликация DHT-записей) | 3 |
| ретрансляторов в туннеле | ≥ 3 |
| активных/подготовленных туннелей | ≥ 3 |
| `MAX_FRAME_PAYLOAD` | 65 536 байт |
| `PROTOCOL_VERSION` | 1 |

Параметры вынесены в `configs/*.yaml` и не требуют правки кода.

## 3. Четыре графа

В работе строго различаются:

1. **Underlay** — фактическая IP-достижимость процессов и контейнеров.
2. **Bootstrap** — начальные контакты, известные узлу при присоединении.
3. **DHT-overlay** — контакты, реально находящиеся в k-bucket после discovery и refresh.
4. **Туннельный маршрут** — последовательность ретрансляторов для прикладных данных.

`FIND_NODE` / `FIND_VALUE` — это маршрутизация DHT-запроса. Туннель — отдельная цепочка ретрансляторов. Путь DHT-поиска не считается туннелем. В общей Docker bridge-сети схемы `star`, `ring`, `tree` являются **bootstrap-схемами**, а не физическими топологиями.

## 4. Подсистемы
┌─────────────────────────────────────────────┐
│ Application │
│ (сообщения, файлы, история, дедупликация) │
└──────────────────┬──────────────────────────┘
│
┌──────────────────▼──────────────────────────┐
│ Tunnel Management │
│ (BUILD / ACTIVE / DEGRADED, retransmit) │
└──────────────────┬──────────────────────────┘
│
┌──────────────────▼──────────────────────────┐
│ DHT and Discovery │
│ (k-bucket, RPC, lookup, store, TTL) │
└──────────────────┬──────────────────────────┘
│
┌──────────────────▼──────────────────────────┐
│ Identity and Security │
│ (Ed25519, X25519, HKDF, AEAD, handshake) │
└──────────────────┬──────────────────────────┘
│
┌──────────────────▼──────────────────────────┐
│ Transport │
│ (TCP, кадрирование, RPC-корреляция) │
└──────────────────┬──────────────────────────┘
│
┌──────────────────▼──────────────────────────┐
│ Metrics and Experimentation │
│ (события, метрики, экспорт CSV/JSON) │
└─────────────────────────────────────────────┘

Прикладной слой не обращается к k-bucket напрямую. Рекомендуемый фасад `Node`:

```python
join(bootstrap_peers)
lookup_node(target_node_id) -> (list[Contact], LookupStats)
find_node(target, contact) -> list[Contact]
ping(contact) -> bool
build_tunnel(destination_node_id, constraints)     # этап 5
send_via_tunnel(tunnel_id, payload)                # этап 5
get_network_state()                                # расширение
 ```

 ## 5. Идентичность
- Долговременная пара Ed25519 создаётся при первом запуске и хранится в <state_dir>/identity.pem с правами 0600.

- NodeID = SHA-256(canonical_encode(public_key)), 32 байта. Каноническое представление — raw 32 байта открытого ключа.

- При получении чужого ключа NodeID пересчитывается и сравнивается с заявленным.

- Случайная генерация нового NodeID на каждом запуске запрещена.

## 6. DHT (Kademlia-lite)

- XOR-метрика: xor_distance(a, b), common_prefix_length(a, b), bucket_index(self_id, node_id).

- k-bucket ёмкостью k с LRU-порядком: ADDED, UPDATED, FULL.

- RoutingTable: 256 бакетов, по одному на каждую позицию первого различающегося бита с self_id.

-  ContactManager.add_contact: если bucket полон — PING LRU; живой сохраняется (touch), мёртвый заменяется новым контактом.

- Итеративный lookup с ALPHA = 3: параллельные FIND_NODE, пул кандидатов растёт по ответам, терминация по k опрошенным ближайшим.

- Bootstrap: PING + add_contact seed-узлов, затем self-lookup. Схемы star и ring реализованы; tree и multi-seed — этап 6.

## 7. Границы этапов

### Реализовано (E1, E2)

    Кадрирование, FrameBuffer, Connection, Server на asyncio.

    Корреляция RPC по request_id (RpcClient).

    Конфигурация через YAML.

    Ed25519-идентичность, NodeID = SHA-256(pubkey), хранилище с правами 0600.

    XOR-метрика, k-bucket, RoutingTable, ContactManager с LRU-проверкой через PING.

    FIND_NODE с sender в запросе: responder добавляет отправителя в таблицу перед ответом.

    Итеративный lookup с ALPHA = 3, LookupStats.

    Bootstrap-схемы star и ring, раннер scripts/run_bootstrap.py с JSON-выгрузкой.

### Не реализовано

    STORE / FIND_VALUE, репликация R = 3, TTL, переопубликование (этап 3).

    Подписанные NodeRecord, псевдонимы (этап 3).

    Защищённая сессия: собственный AKE на Ed25519/X25519/HKDF/AEAD (этап 4).

    Туннели, восстановление после отказа ретранслятора (этап 5).

    Прикладные сообщения и файлы (этап 5).

    Масштабирование до N = 20–21, K_BUCKET_SIZE = 4, netem (этапы 6–7).

    Автоматизированный эксперимент и статистика (этап 8).

## 8. Модель угроз (границы)

Обязательные угрозы: пассивный перехват, MITM, подмена узла, модификация кадра, replay в текущей и завершённой сессии, подмена и откат DHT-записи, отказ ретранслятора.

Не заявляется и не проверяется: анонимность относительно глобального наблюдателя, устойчивость к неограниченной Sybil-атаке, сокрытие размеров и временных характеристик пакетов. Эти ограничения должны быть указаны в пояснительной записке.

## 9. Конфигурация

Все параметры вынесены в configs/*.yaml:

    protocol.version, protocol.max_frame_payload;

    transport.connect_timeout, transport.write_timeout;

    node.host, node.port, node.state_dir;

    dht.k, dht.alpha, dht.replication_factor;

    bootstrap.scheme, bootstrap.n_nodes;

    logging.level, logging.format.

Минимальный набор конфигов: configs/star.yaml, configs/ring.yaml. Оба отличаются только полем bootstrap.scheme при неизменных остальных параметрах.

## 10. Воспроизводимость

Процедура проверки:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
python scripts/run_bootstrap.py configs/star.yaml 10
python scripts/run_bootstrap.py configs/ring.yaml 10
```
Результаты сохраняются в data/runs/bootstrap-<scheme>-<timestamp>/ и включают manifest.json, routing.json, convergence.json. Каталог data/runs/ исключён из git.
