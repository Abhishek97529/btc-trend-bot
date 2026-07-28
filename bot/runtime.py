"""Small fail-safe persistence helpers shared by all paper runners."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pandas as pd


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_write_json(path: Path, value: dict) -> None:
    atomic_write_text(path, json.dumps(value, indent=2) + "\n")


def load_json(path: Path, default: dict) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else dict(default)


def append_csv_dedup(path: Path, row: dict, unique_columns: tuple[str, ...]) -> None:
    incoming = pd.DataFrame([row])
    if path.exists():
        existing = pd.read_csv(path)
        combined = pd.concat([existing, incoming], ignore_index=True)
        combined = combined.drop_duplicates(list(unique_columns), keep="last")
    else:
        combined = incoming
    atomic_write_text(path, combined.to_csv(index=False))


def require_new_bar(last_bar: str | None, new_bar, timeframe: pd.Timedelta,
                    max_gap_bars: int = 1) -> int:
    if last_bar is None:
        return 1
    previous = pd.Timestamp(last_bar)
    current = pd.Timestamp(new_bar)
    if current <= previous:
        if current == previous:
            return 0
        raise RuntimeError(f"refusing non-monotonic bar: state={previous}, data={current}")
    gap = current - previous
    bars = int(gap / timeframe)
    if gap != bars * timeframe or bars > max_gap_bars:
        raise RuntimeError(
            f"refusing unreconciled gap: state={previous}, data={current}, "
            f"gap={bars} bars (allowed {max_gap_bars})"
        )
    return bars
