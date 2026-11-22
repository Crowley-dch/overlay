from __future__ import annotations

import argparse
import asyncio
import copy
import logging
import statistics
import time
from pathlib import Path

from overlay.bootstrap import build_plan
from overlay.config import load_config
from overlay.dht.contact import Contact
from overlay.metrics.writer import new_run_dir, write_json
from overlay.node import Node


async def _run_one(
    cfg_path: str,
    n: int,
    strategy: str,
    relay_count: int,
    trials: int,
) -> dict:
    cfg = load_config(cfg_path)
    cfg.dht.relay_strategy = strategy
    logging.basicConfig(
        level="WARNING", format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    run_dir = new_run_dir(
        Path("data") / "runs", prefix=f"strategy-{strategy}"
    )

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
    for node_id_hex in plan.launch_order:
        node = node_by_id[node_id_hex]
        seeds = plan.seeds_for(node_id_hex)
        if not seeds:
            continue
        await node.bootstrap(seeds)

    durations: list[float] = []
    successes = 0
    total = 0

    try:
        for trial in range(trials):
            initiator = nodes[trial % len(nodes)]
            dest_candidates = [n for n in nodes if n is not initiator]
            if not dest_candidates:
                continue
            destination = dest_candidates[(trial * 7) % len(dest_candidates)]
            started = time.monotonic()
            try:
                secure = await initiator.open_secure_tunnel(
                    destination.self_contact(),
                    relay_count=relay_count,
                )
                total += 1
                durations.append(time.monotonic() - started)
                successes += 1
                try:
                    await initiator.close_secure_tunnel(secure)
                except Exception:
                    pass
            except Exception:
                total += 1

        snapshot = {
            node.node_id.hex(): node.relay_profiles.snapshot()
            for node in nodes
        }
        write_json(run_dir / "profiles.json", snapshot)
    finally:
        for node in nodes:
            try:
                await node.stop()
            except Exception:
                pass

    return {
        "strategy": strategy,
        "trials": total,
        "successes": successes,
        "success_rate": successes / total if total else 0.0,
        "duration_mean": statistics.mean(durations) if durations else 0.0,
        "duration_median": (
            statistics.median(durations) if durations else 0.0
        ),
        "duration_max": max(durations) if durations else 0.0,
        "run_dir": str(run_dir),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", nargs="?", default="configs/star.yaml")
    parser.add_argument("-n", type=int, default=12)
    parser.add_argument("--relay-count", type=int, default=3)
    parser.add_argument("--trials", type=int, default=40)
    args = parser.parse_args()

    results = []
    for strategy in ("random", "rating"):
        print(f"Запуск strategy={strategy}...")
        r = await _run_one(
            args.config,
            args.n,
            strategy,
            args.relay_count,
            args.trials,
        )
        results.append(r)
        print(
            f"  success={r['success_rate']*100:.1f}% "
            f"mean_duration={r['duration_mean']*1000:.1f} ms "
            f"max={r['duration_max']*1000:.1f} ms"
        )

    out = Path("data") / "runs" / "strategy_compare.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    import json
    out.write_text(json.dumps(results, indent=2))

    print()
    print("=== Итог ===")
    for r in results:
        print(
            f"{r['strategy']:>8}  "
            f"success={r['success_rate']*100:5.1f}%  "
            f"mean={r['duration_mean']*1000:7.1f} ms  "
            f"median={r['duration_median']*1000:7.1f} ms"
        )


if __name__ == "__main__":
    asyncio.run(main())