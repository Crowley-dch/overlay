import asyncio
import copy
import logging
import sys
import time
from pathlib import Path

from overlay.bootstrap import build_plan
from overlay.config import load_config
from overlay.metrics.writer import new_run_dir, write_json
from overlay.node import Node


async def run(config_path: str, n_override: int | None = None) -> Path:
    cfg = load_config(config_path)
    n = n_override if n_override is not None else cfg.bootstrap.n_nodes
    scheme = cfg.bootstrap.scheme

    logging.basicConfig(level=cfg.logging.level, format=cfg.logging.format)

    run_dir = new_run_dir(Path("data") / "runs", prefix=f"bootstrap-{scheme}")
    print(f"Каталог прогона: {run_dir}")

    nodes: list[Node] = []
    node_cfgs = []
    for i in range(n):
        node_cfg = copy.deepcopy(cfg)
        node_cfg.node.port = 0
        node_cfg.node.state_dir = str(run_dir / f"node-{i:02d}")
        node_cfgs.append(node_cfg)
        nodes.append(Node(node_cfg))

    for node in nodes:
        await node.start()

    contacts = [node.self_contact() for node in nodes]
    plan = build_plan(scheme, contacts)
    node_by_id = {c.node_id_hex: node for c, node in zip(contacts, nodes)}

    for node_id_hex in plan.launch_order:
        node = node_by_id[node_id_hex]
        seeds = plan.seeds_for(node_id_hex)
        if not seeds:
            print(
                f"  узел {node_id_hex[:12]}… стартовал без seeds "
                f"(bootstrap seed)"
            )
            continue
        await node.bootstrap(seeds)
        print(
            f"  узел {node_id_hex[:12]}… bootstrap через "
            f"{[s.node_id_hex[:12] + '…' for s in seeds]}"
        )

    rounds = []
    for round_idx in range(2):
        results = []
        for i, initiator in enumerate(nodes):
            for j, target in enumerate(nodes):
                if i == j:
                    continue
                try:
                    found_contacts, stats = await initiator.lookup_node(
                        target.node_id
                    )
                    found = target.node_id in {c.node_id for c in found_contacts}
                    results.append(
                        {
                            "initiator": initiator.node_id.hex(),
                            "target": target.node_id.hex(),
                            "found": found,
                            "iterations": stats.iterations,
                            "rpc_count": stats.rpc_count,
                            "timeouts": stats.timeouts,
                            "duration": stats.duration,
                        }
                    )
                except Exception as e:
                    results.append(
                        {
                            "initiator": initiator.node_id.hex(),
                            "target": target.node_id.hex(),
                            "found": False,
                            "error": str(e),
                        }
                    )
        rounds.append(results)

    finished_at = time.strftime("%Y-%m-%dT%H:%M:%S")

    write_json(
        run_dir / "manifest.json",
        {
            "scheme": scheme,
            "n_nodes": n,
            "k": cfg.dht.k,
            "alpha": cfg.dht.alpha,
            "replication_factor": cfg.dht.replication_factor,
            "config_file": str(config_path),
            "finished_at": finished_at,
            "node_ids": [c.node_id_hex for c in contacts],
            "launch_order": plan.launch_order,
            "plan": plan.describe(),
        },
    )

    routing_snapshot = {}
    for node in nodes:
        routing_snapshot[node.node_id.hex()] = {
            "total_contacts": len(node.routing.routing),
            "buckets": node.routing.routing.buckets_info(),
        }
    write_json(run_dir / "routing.json", routing_snapshot)

    convergence = []
    for idx, results in enumerate(rounds):
        total = len(results)
        succeeded = sum(1 for r in results if r["found"])
        convergence.append(
            {
                "round": idx,
                "total": total,
                "succeeded": succeeded,
                "success_rate": (succeeded / total) if total else 0.0,
                "results": results,
            }
        )
    write_json(run_dir / "convergence.json", convergence)

    for node in nodes:
        await node.stop()

    last = convergence[-1] if convergence else {"success_rate": 0.0}
    avg_contacts = (
        sum(len(node.routing.routing) for node in nodes) / len(nodes)
        if nodes
        else 0.0
    )
    print()
    print(f"Схема: {scheme}, узлов: {n}")
    print(f"Успешных lookup в последнем раунде: {last['success_rate'] * 100:.1f}%")
    print(f"Среднее число контактов в таблице: {avg_contacts:.1f}")
    print(f"Результаты: {run_dir}")
    return run_dir


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "configs/star.yaml"
    n_arg = int(sys.argv[2]) if len(sys.argv) > 2 else None
    asyncio.run(run(config_path, n_arg))