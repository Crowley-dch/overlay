# Руководство пользователя

Практические команды для работы с прототипом. Для теоретического описания — см. `ARCHITECTURE.md`, `PROTOCOL.md` и `README.md`.

## Установка

```bash
git clone <url> overlay
cd overlay
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Проверка
```bash
pytest -q
```

## Быстрый PING/PONG
Терминал 1 — сервер:
```bash
python scripts/ping_server.py
```
Терминал 2 — клиент:
```bash
python scripts/ping_client.py
```
Клиент шлёт 5 пингов, получает 5 PONG. Вывод:
```text
→ PING 0: request_id=...
← PONG: payload=b'ping-0'
```
Аргументы клиента:
```bash
python scripts/ping_client.py <config> <host> <port> <count>
```

## Bootstrap-схем
Запуск сети из 10 узлов по схеме star:
```bash
python scripts/run_bootstrap.py configs/star.yaml 10
```
По схеме ring:
```bash
python scripts/run_bootstrap.py configs/ring.yaml 10
```

Результаты в data/runs/bootstrap-<scheme>-<timestamp>/:
    - manifest.json — параметры прогона;

    - routing.json — снимок k-bucket каждого узла;

    - convergence.json — два раунда контрольных lookup.
Итог в конце:
```bash
Схема: star, узлов: 10
Успешных lookup в последнем раунде: 100.0%
Среднее число контактов в таблице: 8.1
```
## Масштабирование до N=20
Полный эксперимент: сходимость, 30 контрольных lookup, отказы хранителей и ретрансляторов, передача сообщений, работа без seed.
```bash
python scripts/run_scale.py configs/star.yaml -n 20
python scripts/run_scale.py configs/ring.yaml -n 20
python scripts/run_scale.py configs/tree.yaml -n 20
python scripts/run_scale.py configs/multi_seed.yaml -n 20
```

Итог: 
```text
Схема: star, N=20, k=4
Сходимость: да (100.0%)
Невырожденность (≥80% узлов с <N-1): да (100.0%)
Контрольные lookup, где цель не была известна: 30/30
Lookup успех: 100.0%
Отказ хранителя: 100.0%
Восстановление после отказа ретранслятора: 100.0%
Передача сообщений: 100.0%
Работа без seed: 100.0%
```
## Сравнение стратегий ретрансляторов
```bash
python scripts/compare_relay_strategies.py configs/star.yaml -n 12 --trials 40
```
## Анализ результатов
Сводная таблица и графики из сырых данных:
```bash
python scripts/analyze.py
```
Результаты в data/analysis/:

    summary.csv — итоговая таблица (для отчёта);

    lookup_durations.png — распределение длительности lookup;

    convergence_rounds.png — сходимость по раундам;

    contacts_distribution.png — распределение контактов;

    lookup_success.png, store_failure.png, relay_recovery.png, message_success.png — бар-чарты.

## Проверка защищённой сессии
Автоматически через тесты:
```bash
pytest tests/test_secure.py -v
pytest tests/test_handshake.py -v
pytest tests/test_aead.py -v
```
