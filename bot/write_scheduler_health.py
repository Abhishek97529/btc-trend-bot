"""Write an auditable heartbeat after every complete 4H suite success."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.runtime import atomic_write_json  # noqa: E402


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
