from __future__ import annotations

import json
import time
from pathlib import Path


def new_run_dir(base: str | Path, prefix: str = "run") -> Path:
    base_path = Path(base)
    base_path.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    base_name = f"{prefix}-{ts}"
    candidate = base_path / base_name
    counter = 0
    while candidate.exists():
        counter += 1
        candidate = base_path / f"{base_name}-{counter}"
    candidate.mkdir(parents=True)
    return candidate


def write_json(path: str | Path, data: dict | list) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def write_jsonl(path: str | Path, rows: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")