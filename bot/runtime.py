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
                    max_gap_bars: int = 1,
                    allow_late_recovery: bool = False) -> int:
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
    if gap != bars * timeframe or (bars > max_gap_bars and not allow_late_recovery):
        raise RuntimeError(
            f"refusing unreconciled gap: state={previous}, data={current}, "
            f"gap={bars} bars (allowed {max_gap_bars})"
        )
    return bars


def reconcile_missed_holds(
    last_bar: str | None,
    new_bar,
    targets: pd.Series,
    held_target,
    strategy_label: str,
) -> tuple[int, pd.Timestamp | None]:
    """Identify missed bars that can be fast-forwarded without inventing a fill.

    The current/new bar is deliberately excluded from reconciliation because the
    runner can execute its signal at the current observable price. Every earlier
    missed bar must match the position already held; otherwise a historical trade
    was missed and recovery remains fail-closed.
    """
    if last_bar is None:
        return 0, None
    previous = pd.Timestamp(last_bar)
    current = pd.Timestamp(new_bar)
    candidates = targets.loc[(targets.index > previous) & (targets.index <= current)]
    if len(candidates) <= 1:
        return 0, None
    missed = candidates.iloc[:-1]
    if missed.isna().any():
        raise RuntimeError(f"missing {strategy_label} signal during gap")
    changed = missed[missed != held_target]
    if not changed.empty:
        raise RuntimeError(
            f"missed {strategy_label} trade at {changed.index[0]}; "
            "refusing to invent a historical execution"
        )
    return len(missed), pd.Timestamp(missed.index[-1])
