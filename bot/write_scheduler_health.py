"""Write an auditable heartbeat after the complete 4H suite succeeds."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from bot.runtime import atomic_write_json


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = (
    ROOT / "strategies" / "spot_4h_dual_trend" / "runtime" /
    "scheduler_health.json"
)


def main() -> None:
    atomic_write_json(OUTPUT, {
        "status": "success",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "workflow": os.getenv("GITHUB_WORKFLOW", "local"),
        "run_id": os.getenv("GITHUB_RUN_ID"),
        "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        "commit_sha": os.getenv("GITHUB_SHA"),
        "runner_region_path": "github_to_cloudflare_apac_durable_object",
    })
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
