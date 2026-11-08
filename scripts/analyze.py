from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

RUNS_ROOT = Path("data") / "runs"
OUT_DIR = Path("data") / "analysis"


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) < 20 and p > 90:
        return None
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    if lo == hi:
        return sorted_vals[lo]
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def summary(values: list[float]) -> dict:
    if not values:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "p95": None,
        }
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "p95": percentile(values, 95),
    }


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def discover_runs(root: Path, scheme: str | None = None) -> list[Path]:
    if not root.exists():
        return []
    runs: list[Path] = []
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        if not p.name.startswith("scale-"):
            continue
        if scheme is not None and f"-{scheme}-" not in p.name:
            continue
        if not (p / "manifest.json").exists():
            continue
        runs.append(p)
    return runs


def parse_run(run_dir: Path) -> dict:
    manifest = load_json(run_dir / "manifest.json")
    summary_data = load_json(run_dir / "summary.json") if (
        run_dir / "summary.json"
    ).exists() else {}
    convergence = load_json(run_dir / "convergence.json") if (
        run_dir / "convergence.json"
    ).exists() else {}
    nondeg = load_json(run_dir / "nondegeneracy.json") if (
        run_dir / "nondegeneracy.json"
    ).exists() else {}
    no_seed = load_json(run_dir / "no_seed.json") if (
        run_dir / "no_seed.json"
    ).exists() else {}

    lookups = load_jsonl(run_dir / "lookups.jsonl")
    store_failures = load_jsonl(run_dir / "store_failures.jsonl")
    relay_failures = load_jsonl(run_dir / "relay_failures.jsonl")
    messages = load_jsonl(run_dir / "messages.jsonl")

    lookup_durations = [
        r["duration"] for r in lookups if "duration" in r
    ]
    lookup_iterations = [
        r["iterations"] for r in lookups if "iterations" in r
    ]
    lookup_rpc = [
        r["rpc_count"] for r in lookups if "rpc_count" in r
    ]
    lookup_found = sum(1 for r in lookups if r.get("found"))
    lookup_total = len(lookups)

    store_durations = [
        r["get_duration"] for r in store_failures if "get_duration" in r
    ]
    store_ok = sum(
        1 for r in store_failures if r.get("found_after_failure")
    )

    relay_recovery_durations = [
        r["recovery_duration"]
        for r in relay_failures
        if r.get("recovered")
    ]
    relay_ok = sum(1 for r in relay_failures if r.get("recovered"))

    message_durations = [
        r["duration"] for r in messages if r.get("ok")
    ]
    message_ok = sum(1 for r in messages if r.get("ok"))

    return {
        "run": run_dir.name,
        "scheme": manifest.get("scheme"),
        "n": manifest.get("n"),
        "k": manifest.get("k"),
        "alpha": manifest.get("alpha"),
        "r": manifest.get("replication_factor"),
        "startup_duration": manifest.get("startup_duration"),
        "converged": summary_data.get("converged"),
        "convergence_success_rate": summary_data.get(
            "convergence_success_rate"
        ),
        "contacts_below_n_minus_1_ratio": summary_data.get(
            "contacts_below_n_minus_1_ratio"
        ),
        "criterion_80pct": summary_data.get("criterion_80pct"),
        "sample_lookups_found": summary_data.get("sample_lookups_found"),
        "sample_lookups_count": summary_data.get("sample_lookups_count"),
        "lookup_success_rate": (
            lookup_found / lookup_total if lookup_total else 0.0
        ),
        "lookup_duration": summary(lookup_durations),
        "lookup_iterations": summary(lookup_iterations),
        "lookup_rpc": summary(lookup_rpc),
        "store_failure_rate": (
            store_ok / len(store_failures) if store_failures else 0.0
        ),
        "store_failure_duration": summary(store_durations),
        "relay_recovery_rate": (
            relay_ok / len(relay_failures) if relay_failures else 0.0
        ),
        "relay_recovery_duration": summary(relay_recovery_durations),
        "message_success_rate": (
            message_ok / len(messages) if messages else 0.0
        ),
        "message_duration": summary(message_durations),
        "no_seed_success_rate": no_seed.get("success_rate"),
        "raw_lookup_durations": lookup_durations,
        "raw_message_durations": message_durations,
        "raw_iterations": lookup_iterations,
        "convergence_rounds": convergence.get("rounds", []),
        "contacts_per_node": nondeg.get("contacts_per_node", []),
    }


def write_summary_csv(parsed: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "run", "scheme", "n", "k", "alpha", "r",
            "startup_duration",
            "converged", "convergence_success_rate",
            "criterion_80pct", "contacts_below_n_minus_1_ratio",
            "sample_lookups_found", "sample_lookups_count",
            "lookup_success_rate",
            "lookup_dur_mean", "lookup_dur_median", "lookup_dur_p95",
            "lookup_iter_mean", "lookup_iter_median",
            "lookup_rpc_mean",
            "store_failure_rate",
            "store_failure_dur_median",
            "relay_recovery_rate",
            "relay_recovery_dur_median",
            "message_success_rate",
            "message_dur_mean", "message_dur_median",
            "no_seed_success_rate",
        ])
        for r in parsed:
            ld = r["lookup_duration"]
            li = r["lookup_iterations"]
            lr = r["lookup_rpc"]
            sf = r["store_failure_duration"]
            rr = r["relay_recovery_duration"]
            md = r["message_duration"]
            w.writerow([
                r["run"], r["scheme"], r["n"], r["k"], r["alpha"], r["r"],
                _fmt(r["startup_duration"]),
                r["converged"],
                _fmt(r["convergence_success_rate"]),
                r["criterion_80pct"],
                _fmt(r["contacts_below_n_minus_1_ratio"]),
                r["sample_lookups_found"], r["sample_lookups_count"],
                _fmt(r["lookup_success_rate"]),
                _fmt(ld["mean"]), _fmt(ld["median"]), _fmt(ld["p95"]),
                _fmt(li["mean"]), _fmt(li["median"]),
                _fmt(lr["mean"]),
                _fmt(r["store_failure_rate"]),
                _fmt(sf["median"]),
                _fmt(r["relay_recovery_rate"]),
                _fmt(rr["median"]),
                _fmt(r["message_success_rate"]),
                _fmt(md["mean"]), _fmt(md["median"]),
                _fmt(r["no_seed_success_rate"]),
            ])


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:.4f}"
    return str(value)


def plot_lookup_durations_by_scheme(
    parsed: list[dict], path: Path
) -> None:
    schemes = sorted({r["scheme"] for r in parsed})
    data = []
    labels = []
    for s in schemes:
        rows = [r for r in parsed if r["scheme"] == s]
        all_dur: list[float] = []
        for r in rows:
            all_dur.extend(r["raw_lookup_durations"])
        if all_dur:
            data.append(all_dur)
            labels.append(s)
    if not data:
        return
    plt.figure(figsize=(8, 5))
    plt.boxplot(data, tick_labels=labels, showmeans=True)
    plt.ylabel("Lookup duration, s")
    plt.title("Длительность DHT lookup по схемам")
    plt.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_convergence_rounds(parsed: list[dict], path: Path) -> None:
    plt.figure(figsize=(8, 5))
    for r in parsed:
        rounds = r["convergence_rounds"]
        if not rounds:
            continue
        xs = [rd.get("round", i) for i, rd in enumerate(rounds)]
        ys = [rd.get("success_rate", 0.0) * 100.0 for rd in rounds]
        plt.plot(
            xs, ys, marker="o",
            label=f"{r['scheme']} (N={r['n']})",
        )
    plt.xlabel("Раунд")
    plt.ylabel("Успешных lookup, %")
    plt.title("Сходимость DHT по раундам")
    plt.ylim(0, 105)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_contacts_distribution(parsed: list[dict], path: Path) -> None:
    plt.figure(figsize=(8, 5))
    for r in parsed:
        contacts = r["contacts_per_node"]
        if not contacts:
            continue
        plt.hist(
            contacts,
            bins=range(0, max(contacts) + 2),
            alpha=0.5,
            label=f"{r['scheme']} N={r['n']}",
        )
    plt.xlabel("Контактов в таблице маршрутизации")
    plt.ylabel("Число узлов")
    plt.title("Распределение числа контактов (после сходимости)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_metric_bar(
    parsed: list[dict],
    metric_key: str,
    title: str,
    ylabel: str,
    path: Path,
) -> None:
    labels = [f"{r['scheme']}\nN={r['n']}" for r in parsed]
    values = [r.get(metric_key) or 0.0 for r in parsed]
    if not values:
        return
    plt.figure(figsize=(8, 5))
    bars = plt.bar(labels, values)
    for b, v in zip(bars, values):
        plt.text(
            b.get_x() + b.get_width() / 2,
            b.get_height(),
            f"{v * 100:.1f}%",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    plt.ylabel(ylabel)
    plt.title(title)
    plt.ylim(0, 1.1)
    plt.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_message_durations(parsed: list[dict], path: Path) -> None:
    labels = [f"{r['scheme']}\nN={r['n']}" for r in parsed]
    means = [r["message_duration"]["mean"] or 0.0 for r in parsed]
    medians = [r["message_duration"]["median"] or 0.0 for r in parsed]
    if not any(means):
        return
    x = range(len(labels))
    width = 0.35
    plt.figure(figsize=(8, 5))
    plt.bar([i - width / 2 for i in x], means, width, label="mean")
    plt.bar([i + width / 2 for i in x], medians, width, label="median")
    plt.xticks(list(x), labels)
    plt.ylabel("Message duration, s")
    plt.title("Задержка end-to-end сообщения")
    plt.grid(True, axis="y", alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def write_raw_aggregate(parsed: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in parsed:
            for d in r["raw_lookup_durations"]:
                f.write(
                    json.dumps({
                        "run": r["run"],
                        "scheme": r["scheme"],
                        "n": r["n"],
                        "duration": d,
                    }) + "\n"
                )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scheme", default=None,
        help="Только одна схема (star, ring, tree, multi-seed)",
    )
    parser.add_argument(
        "--root", default=str(RUNS_ROOT),
        help="Каталог с data/runs",
    )
    args = parser.parse_args()

    runs = discover_runs(Path(args.root), scheme=args.scheme)
    if not runs:
        print(f"Нет прогонов в {args.root}")
        sys.exit(1)

    print(f"Найдено прогонов: {len(runs)}")
    parsed = [parse_run(r) for r in runs]

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    write_summary_csv(parsed, OUT_DIR / "summary.csv")
    write_raw_aggregate(parsed, OUT_DIR / "lookup_durations.jsonl")

    plot_lookup_durations_by_scheme(
        parsed, OUT_DIR / "lookup_durations.png"
    )
    plot_convergence_rounds(
        parsed, OUT_DIR / "convergence_rounds.png"
    )
    plot_contacts_distribution(
        parsed, OUT_DIR / "contacts_distribution.png"
    )
    plot_metric_bar(
        parsed,
        "lookup_success_rate",
        "Успешность DHT lookup",
        "Успешных lookup",
        OUT_DIR / "lookup_success.png",
    )
    plot_metric_bar(
        parsed,
        "store_failure_rate",
        "Успешность после отказа хранителя",
        "Успешных FIND_VALUE",
        OUT_DIR / "store_failure.png",
    )
    plot_metric_bar(
        parsed,
        "relay_recovery_rate",
        "Восстановление после отказа ретранслятора",
        "Восстановлений",
        OUT_DIR / "relay_recovery.png",
    )
    plot_metric_bar(
        parsed,
        "message_success_rate",
        "Успешность end-to-end передачи",
        "Успешных сообщений",
        OUT_DIR / "message_success.png",
    )
    plot_message_durations(
        parsed, OUT_DIR / "message_durations.png"
    )

    print()
    print(f"Сводка: {OUT_DIR / 'summary.csv'}")
    print(f"Графики: {OUT_DIR}/*.png")
    print()
    print("=== Итог ===")
    for r in parsed:
        print(
            f"{r['scheme']:>10} N={r['n']:<3} "
            f"converged={r['converged']} "
            f"80%_crit={r['criterion_80pct']} "
            f"lookup={r['lookup_success_rate']*100:.1f}% "
            f"store={r['store_failure_rate']*100:.1f}% "
            f"relay={r['relay_recovery_rate']*100:.1f}% "
            f"msg={r['message_success_rate']*100:.1f}% "
            f"no_seed={r['no_seed_success_rate']*100 if r['no_seed_success_rate'] is not None else 0:.1f}%"
        )


if __name__ == "__main__":
    main()