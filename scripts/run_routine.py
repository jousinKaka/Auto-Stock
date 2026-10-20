"""Run the scheduled morning or evening routine with live-trading safeguards."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List


def routine_arguments(
    routine: str,
    enable_live_trading: str,
    live_buy_limit_usd: str,
) -> List[str]:
    if routine not in {"morning", "evening"}:
        raise ValueError("Routine must be 'morning' or 'evening'.")

    if routine == "evening" and enable_live_trading == "true" and live_buy_limit_usd.strip():
        return ["--live", "--max-live-buy-notional-usd", live_buy_limit_usd.strip()]
    return []


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: run_routine.py morning|evening")

    routine = sys.argv[1]
    args = routine_arguments(
        routine,
        os.environ.get("ENABLE_LIVE_TRADING", ""),
        os.environ.get("LIVE_BUY_LIMIT_USD", ""),
    )

    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    os.chdir(repo_root)
    os.environ["AUTO_STOCK_ROUTINE"] = routine
    sys.argv = ["morning_routine.py", *args]

    from morning_routine import main as run_morning_routine

    run_morning_routine()


if __name__ == "__main__":
    main()
