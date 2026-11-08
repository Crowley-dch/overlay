from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import secrets
import statistics
import time
from pathlib import Path

from overlay.bootstrap import BootstrapPlan, build_plan
from overlay.config import load_config
from overlay.dht.contact import Contact
from overlay.dht.record import build_record, record_key_for_node
from overlay.metrics.writer import new_run_dir, write_json, write_jsonl
from overlay.node import Node
from overlay.tunnel import TunnelState


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    if lo == hi:
        return sorted_vals[lo]
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _summary(values: list[float]) -> dict:
    if not values:
        return {
            "count": 0,
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "median": 0.0,
            "p95": None,
        }
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "p95": _percentile(values, 95) if len(values) >= 20 else None,
    }


async def _start_network(
    cfg_path: str,
    n: int,
    run_dir: Path,
) -> tuple[list[Node], "BootstrapPlan", float]:
    cfg = load_config(cfg_path)
    started = time.monotonic()

    nodes: list[Node] = []
    for i in range(n):
        node_cfg = copy.deepcopy(cfg)
        node_cfg.node.port = 0
        node_cfg.node.state_dir = str(run_dir / f"node-{i:02d}")
        nodes.append(Node(node_cfg))

    for node in nodes:
        await node.start()

    contacts = [node.self_contact() for node in nodes]
    plan = build_plan(cfg.bootstrap.scheme, contacts)
    node_by_id = {c.node_id_hex: node for c, node in zip(contacts, nodes)}

    for node_id_hex in plan.launch_order:
        node = node_by_id[node_id_hex]
        seeds = plan.seeds_for(node_id_hex)
        if not seeds:
            continue
        await node.bootstrap(seeds)

    return nodes, plan, time.monotonic() - started


async def _convergence_round(nodes: list[Node]) -> dict:
    total = 0
    success = 0
    durations: list[float] = []
    for i, initiator in enumerate(nodes):
        for j, target in enumerate(nodes):
            if i == j:
                continue
            total += 1
            try:
                result, stats = await initiator.lookup_node(target.node_id)
                found = target.node_id in {c.node_id for c in result}
                if found:
                    success += 1
                durations.append(stats.duration)
            except Exception:
                pass
    return {
        "total": total,
        "succeeded": success,
        "success_rate": success / total if total else 0.0,
        "duration": _summary(durations),
    }


async def _wait_convergence(
    nodes: list[Node],
    max_rounds: int = 5,
    success_threshold: float = 0.95,
    stability_threshold: float = 0.10,
) -> dict:
    history: list[dict] = []
    prev_contacts: list[int] | None = None
    for round_idx in range(max_rounds):
        result = await _convergence_round(nodes)
        result["round"] = round_idx
        contacts_per_node = [len(n.routing.routing) for n in nodes]
        result["contacts_per_node"] = contacts_per_node
        history.append(result)

        if result["success_rate"] < success_threshold:
            prev_contacts = contacts_per_node
            continue

        if prev_contacts is not None:
            changes = [
                abs(a - b) / max(a, b, 1)
                for a, b in zip(contacts_per_node, prev_contacts)
            ]
            if max(changes) <= stability_threshold:
                return {
                    "converged": True,
                    "rounds": history,
                    "rounds_used": round_idx + 1,
                }
        prev_contacts = contacts_per_node
    return {
        "converged": False,
        "rounds": history,
        "rounds_used": len(history),
    }


async def _nondegeneracy_check(
    nodes: list[Node],
    convergence_round: dict,
    *,
    initial_k: int,
) -> dict:
    n = len(nodes)
    contacts_per_node = [len(node.routing.routing) for node in nodes]
    below_n_minus_1 = sum(1 for c in contacts_per_node if c < n - 1)
    ratio_below = below_n_minus_1 / n

    sample_lookups: list[dict] = []
    for _ in range(40):
        if len(sample_lookups) >= 30:
            break
        initiator = secrets.choice(nodes)
        target = secrets.choice(nodes)
        if initiator is target:
            continue
        if initiator.routing.routing.get(target.node_id) is not None:
            continue
        try:
            result, stats = await initiator.lookup_node(target.node_id)
            found = target.node_id in {c.node_id for c in result}
            sample_lookups.append({
                "initiator": initiator.node_id.hex(),
                "target": target.node_id.hex(),
                "found": found,
                "iterations": stats.iterations,
                "rpc_count": stats.rpc_count,
                "duration": stats.duration,
                "used_intermediate": stats.rpc_count >= 1,
            })
        except Exception as e:
            sample_lookups.append({
                "initiator": initiator.node_id.hex(),
                "target": target.node_id.hex(),
                "found": False,
                "error": str(e),
            })

    all_intermediate = all(
        s.get("used_intermediate", False) for s in sample_lookups if s.get("found")
    )

    return {
        "n": n,
        "initial_k": initial_k,
        "contacts_per_node": contacts_per_node,
        "below_n_minus_1_count": below_n_minus_1,
        "below_n_minus_1_ratio": ratio_below,
        "criterion_80pct": ratio_below >= 0.8,
        "sample_lookups_count": len(sample_lookups),
        "sample_lookups_found": sum(1 for s in sample_lookups if s.get("found")),
        "sample_lookups_used_intermediate": all_intermediate,
        "sample_lookups": sample_lookups,
        "convergence_success_rate": convergence_round["success_rate"],
    }


async def _lookup_series(
    nodes: list[Node],
    count: int = 30,
) -> list[dict]:
    records: list[dict] = []
    attempts = 0
    while len(records) < count and attempts < count * 20:
        attempts += 1
        initiator = secrets.choice(nodes)
        target = secrets.choice(nodes)
        if initiator is target:
            continue
        try:
            result, stats = await initiator.lookup_node(target.node_id)
            found = target.node_id in {c.node_id for c in result}
            was_known = (
                initiator.routing.routing.get(target.node_id) is not None
            )
            records.append({
                "lookup_id": stats.lookup_id,
                "initiator": initiator.node_id.hex(),
                "target": target.node_id.hex(),
                "found": found,
                "was_known_before": was_known,
                "iterations": stats.iterations,
                "rpc_count": stats.rpc_count,
                "timeouts": stats.timeouts,
                "errors": stats.errors,
                "candidates_seen": stats.candidates_seen,
                "duration": stats.duration,
            })
        except Exception as e:
            records.append({
                "initiator": initiator.node_id.hex(),
                "target": target.node_id.hex(),
                "found": False,
                "error": str(e),
            })
    return records


async def _store_failure_series(
    nodes: list[Node],
    count: int = 5,
) -> list[dict]:
    results: list[dict] = []
    for attempt in range(count):
        owner = secrets.choice(nodes)
        record = build_record(
            node_id=owner.node_id,
            identity_public_key=owner.identity.public_key_bytes(),
            addresses=[f"{owner.host}:{owner.port}"],
            sequence_number=attempt + 1,
            ttl=180,
            private_key=owner.identity.private_key,
        )
        key = record_key_for_node(owner.node_id)
        started = time.monotonic()
        try:
            replicas = await owner.put_record(record)
        except Exception as e:
            results.append({"error": str(e), "attempt": attempt})
            continue
        put_duration = time.monotonic() - started

        holder = None
        for node in nodes:
            if node is owner:
                continue
            if node.store.get(key) is not None:
                holder = node
                break

        if holder is None:
            results.append({
                "attempt": attempt,
                "replicas": replicas,
                "error": "no holder found",
            })
            continue

        holder_was = holder.node_id.hex()
        await holder.stop()

        reader = secrets.choice(
            [n for n in nodes if n is not holder and n is not owner]
        )
        started = time.monotonic()
        got = await reader.get_record(key)
        get_duration = time.monotonic() - started
        results.append({
            "attempt": attempt,
            "replicas": replicas,
            "holder": holder_was,
            "found_after_failure": got is not None and got.node_id == owner.node_id,
            "put_duration": put_duration,
            "get_duration": get_duration,
        })
    return results


async def _relay_failure_series(
    nodes: list[Node],
    count: int = 5,
) -> list[dict]:
    results: list[dict] = []
    for attempt in range(count):
        if len(nodes) < 6:
            break
        initiator = secrets.choice(nodes)
        destination_candidates = [
            n for n in nodes if n is not initiator
        ]
        if not destination_candidates:
            continue
        destination = secrets.choice(destination_candidates)
        try:
            secure = await initiator.open_secure_tunnel(
                destination.self_contact(), relay_count=3
            )
        except Exception as e:
            results.append({"attempt": attempt, "error": str(e)})
            continue

        old_tunnel = secure.tunnel_id
        route = initiator._tunnel_routes.get(old_tunnel)
        if route is None or not route.relays:
            results.append({"attempt": attempt, "error": "no relays"})
            continue

        relay = route.relays[1]
        relay_node = next(n for n in nodes if n.node_id == relay.node_id)
        await relay_node.stop()

        started = time.monotonic()
        try:
            resp = await secure.send_message(f"recover-{attempt}".encode())
            recovered = True
            resp_preview = resp[:32].decode("utf-8", "replace")
        except Exception as e:
            recovered = False
            resp_preview = f"error: {e}"
        recovery_duration = time.monotonic() - started

        results.append({
            "attempt": attempt,
            "old_tunnel_id": old_tunnel.hex(),
            "new_tunnel_id": secure.tunnel_id.hex(),
            "killed_relay": relay.node_id_hex if hasattr(relay, "node_id_hex") else relay.node_id.hex(),
            "recovered": recovered,
            "recovery_duration": recovery_duration,
            "response": resp_preview,
        })
    return results


async def _message_series(
    nodes: list[Node],
    count: int = 30,
    relay_count: int = 3,
) -> list[dict]:
    if len(nodes) < relay_count + 2:
        return []
    initiator = secrets.choice(nodes)
    destination = secrets.choice(
        [n for n in nodes if n is not initiator]
    )
    try:
        secure = await initiator.open_secure_tunnel(
            destination.self_contact(), relay_count=relay_count
        )
    except Exception as e:
        return [{"error": str(e)}]

    results: list[dict] = []
    for i in range(count):
        payload = f"msg-{i}-{secrets.token_hex(4)}".encode()
        started = time.monotonic()
        try:
            resp = await secure.send_message(payload)
            ok = resp == b"delivered:" + payload
            results.append({
                "i": i,
                "ok": ok,
                "duration": time.monotonic() - started,
                "response_len": len(resp),
            })
        except Exception as e:
            results.append({
                "i": i,
                "ok": False,
                "duration": time.monotonic() - started,
                "error": str(e),
            })
    return results


async def _no_seed_check(nodes: list[Node], plan) -> dict:
    seed_hex = plan.launch_order[0]
    seed_node = next(
        (n for n in nodes if n.node_id.hex() == seed_hex), None
    )
    if seed_node is None:
        return {"skipped": True, "reason": "seed not found"}

    survivors = [n for n in nodes if n is not seed_node]
    if len(survivors) < 2:
        return {"skipped": True, "reason": "not enough survivors"}

    await seed_node.stop()

    success = 0
    total = 0
    for _ in range(10):
        initiator = secrets.choice(survivors)
        target = secrets.choice(survivors)
        if initiator is target:
            continue
        total += 1
        try:
            result, _ = await initiator.lookup_node(target.node_id)
            if target.node_id in {c.node_id for c in result}:
                success += 1
        except Exception:
            pass

    return {
        "seed_stopped": seed_hex,
        "total": total,
        "success": success,
        "success_rate": success / total if total else 0.0,
    }


async def run(
    cfg_path: str,
    n: int,
    scheme: str,
    lookup_count: int,
) -> Path:
    cfg = load_config(cfg_path)
    logging.basicConfig(level=cfg.logging.level, format=cfg.logging.format)

    run_dir = new_run_dir(
        Path("data") / "runs", prefix=f"scale-{scheme}-n{n}"
    )
    print(f"Каталог прогона: {run_dir}")

    nodes, plan, startup_duration = await _start_network(
        cfg_path, n, run_dir
    )
    try:
        convergence = await _wait_convergence(nodes)

        nondegeneracy = await _nondegeneracy_check(
            nodes,
            convergence["rounds"][-1],
            initial_k=cfg.dht.k,
        )

        lookups = await _lookup_series(nodes, count=lookup_count)

        store_failures = await _store_failure_series(nodes, count=5)

        relay_failures = await _relay_failure_series(nodes, count=5)

        messages = await _message_series(nodes, count=30)

        no_seed = await _no_seed_check(nodes, plan)

        routing_snapshot = {}
        for node in nodes:
            routing_snapshot[node.node_id.hex()] = {
                "total_contacts": len(node.routing.routing),
                "buckets": node.routing.routing.buckets_info(),
            }
    finally:
        for node in nodes:
            try:
                await node.stop()
            except Exception:
                pass

    manifest = {
        "scheme": scheme,
        "n": n,
        "k": cfg.dht.k,
        "alpha": cfg.dht.alpha,
        "replication_factor": cfg.dht.replication_factor,
        "config_file": str(cfg_path),
        "startup_duration": startup_duration,
        "node_ids": [c.node_id_hex for c in [node.self_contact() for node in nodes]],
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "lookup_count_target": lookup_count,
        "relay_count_default": 3,
        "message_count_target": 30,
        "store_failure_target": 5,
        "relay_failure_target": 5,
    }
    write_json(run_dir / "manifest.json", manifest)
    write_json(run_dir / "plan.json", plan.describe())
    write_json(run_dir / "routing.json", routing_snapshot)
    write_json(run_dir / "convergence.json", convergence)
    write_json(run_dir / "nondegeneracy.json", nondegeneracy)
    write_jsonl(run_dir / "lookups.jsonl", lookups)
    write_jsonl(run_dir / "store_failures.jsonl", store_failures)
    write_jsonl(run_dir / "relay_failures.jsonl", relay_failures)
    write_jsonl(run_dir / "messages.jsonl", messages)
    write_json(run_dir / "no_seed.json", no_seed)

    lookup_durations = [
        r["duration"] for r in lookups if "duration" in r
    ]
    message_durations = [
        r["duration"] for r in messages if r.get("ok")
    ]
    relay_recovery = [
        r["recovery_duration"]
        for r in relay_failures
        if r.get("recovered")
    ]

    summary = {
        "converged": convergence["converged"],
        "convergence_success_rate": convergence["rounds"][-1]["success_rate"],
        "contacts_below_n_minus_1_ratio": nondegeneracy["below_n_minus_1_ratio"],
        "criterion_80pct": nondegeneracy["criterion_80pct"],
        "sample_lookups_found": nondegeneracy["sample_lookups_found"],
        "sample_lookups_count": nondegeneracy["sample_lookups_count"],
        "lookups_success_rate": (
            sum(1 for r in lookups if r.get("found")) / len(lookups)
            if lookups else 0.0
        ),
        "lookup_duration": _summary(lookup_durations),
        "store_failure_success_rate": (
            sum(1 for r in store_failures if r.get("found_after_failure"))
            / len(store_failures)
            if store_failures else 0.0
        ),
        "relay_failure_recovery_rate": (
            sum(1 for r in relay_failures if r.get("recovered"))
            / len(relay_failures)
            if relay_failures else 0.0
        ),
        "relay_recovery_duration": _summary(relay_recovery),
        "message_success_rate": (
            sum(1 for r in messages if r.get("ok")) / len(messages)
            if messages else 0.0
        ),
        "message_duration": _summary(message_durations),
        "no_seed_success_rate": no_seed.get("success_rate", 0.0),
    }
    write_json(run_dir / "summary.json", summary)

    print()
    print(f"Схема: {scheme}, N={n}, k={cfg.dht.k}")
    print(f"Сходимость: {'да' if summary['converged'] else 'нет'} "
          f"({summary['convergence_success_rate'] * 100:.1f}%)")
    print(f"Невырожденность (≥80% узлов с <N-1): "
          f"{'да' if summary['criterion_80pct'] else 'нет'} "
          f"({summary['contacts_below_n_minus_1_ratio'] * 100:.1f}%)")
    print(f"Контрольные lookup, где цель не была известна: "
          f"{summary['sample_lookups_found']}/{summary['sample_lookups_count']}")
    print(f"Lookup успех: {summary['lookups_success_rate'] * 100:.1f}%")
    print(f"Отказ хранителя: {summary['store_failure_success_rate'] * 100:.1f}%")
    print(f"Восстановление после отказа ретранслятора: "
          f"{summary['relay_failure_recovery_rate'] * 100:.1f}%")
    print(f"Передача сообщений: {summary['message_success_rate'] * 100:.1f}%")
    print(f"Работа без seed: {summary['no_seed_success_rate'] * 100:.1f}%")
    print(f"Результаты: {run_dir}")
    return run_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config", nargs="?", default="configs/star.yaml")
    parser.add_argument("-n", type=int, default=12)
    parser.add_argument(
        "--scheme", default=None,
        help="Переопределить bootstrap.scheme из конфига",
    )
    parser.add_argument(
        "--lookups", type=int, default=30,
        help="Число контрольных lookup",
    )
    args = parser.parse_args()
    cfg = load_config(args.config)
    scheme = args.scheme if args.scheme else cfg.bootstrap.scheme
    asyncio.run(run(args.config, args.n, scheme, args.lookups))