"""
Paper-trading bot + scheduler for the LOCKED `trend_ensemble` strategy
(BTC/USDT spot, daily). No real money, no API keys.

Each run it:
  1. Pulls fresh daily klines from Binance's public API (closed candles only).
  2. Computes the 7 trend signals + target exposure from the last CLOSED bar.
  3. Classifies the action: ENTER / EXIT / ADD / TRIM / HOLD.
  4. Rebalances the virtual portfolio (0.10% fee + 5 bps slippage), unless --dry-run.
  5. Prints a FULL trade-detail report, emits JSON, and appends to bot/trades.csv.

Commands:
    python bot/paper_bot.py status               # portfolio + live signal detail
    python bot/paper_bot.py run                  # one cycle: decide, act, report
    python bot/paper_bot.py run --dry-run        # decide + report, DON'T change state
    python bot/paper_bot.py run --json           # also print the machine JSON blob
    python bot/paper_bot.py loop                 # run once per day forever (blocking)
    python bot/paper_bot.py reset                # wipe state, start fresh

Because the strategy is daily, run it once a day a few minutes after 00:00 UTC.
See SCHEDULING at the bottom of this file for cron / Windows Task Scheduler setup.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))  # bot/ (for notify.py)
sys.path.insert(0, str(ROOT / "src"))
from data_fetch import fetch_klines  # noqa: E402  (also injects truststore SSL fix)
from strategies_v2 import trend_ensemble, _trend_votes  # noqa: E402
import config as C  # noqa: E402  (LOCKED single source of truth)

# ---- Config (all values come from the LOCKED src/config.py) ---------------- #
SYMBOL = C.SYMBOL
THRESHOLD = C.THRESHOLD          # validated, robust across 0.3-0.8
INITIAL_CAPITAL = C.INITIAL_CAPITAL
FEE = C.FEE                      # 0.10% Binance spot taker
SLIPPAGE = C.SLIPPAGE            # 5 bps
MIN_TRADE_FRAC = C.MIN_TRADE_FRAC  # ignore rebalances smaller than 1% of equity
WARMUP_DAYS = C.WARMUP_DAYS      # >200 for the 200d MA

# Human-friendly labels for the 7 signals (order matches _trend_votes columns).
SIGNAL_LABELS = {
    "above_sma50": "close > SMA(50)",
    "above_sma100": "close > SMA(100)",
    "above_sma200": "close > SMA(200)",
    "ema20_50": "EMA(20) > EMA(50)",
    "ema50_100": "EMA(50) > EMA(100)",
    "donchian55": "Donchian(55) breakout",
    "mom90": "ROC(90) > 0",
}

STATE = Path(__file__).resolve().parent / "state.json"
TRADES = Path(__file__).resolve().parent / "trades.csv"
STATUS = Path(__file__).resolve().parent / "status.json"  # latest snapshot for /commands


def save_status(r: dict) -> None:
    """Persist the latest report so the Telegram command bot can read it WITHOUT
    fetching market data itself (Binance is geo-blocked on CI runners)."""
    STATUS.write_text(json.dumps(r, indent=2))


# ---- State ----------------------------------------------------------------- #
def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {
        "cash": INITIAL_CAPITAL,
        "btc": 0.0,
        "initial_capital": INITIAL_CAPITAL,
        "last_bar": None,          # timestamp of last bar we acted on
        "last_target": 0.0,
        "runs": 0,
    }


def save_state(s: dict) -> None:
    STATE.write_text(json.dumps(s, indent=2))


def log_trade(row: dict) -> None:
    df = pd.DataFrame([row])
    header = not TRADES.exists()
    df.to_csv(TRADES, mode="a", header=header, index=False)


# ---- Market data + signal detail ------------------------------------------- #
def signal_detail() -> dict:
    """Compute target exposure AND the per-signal breakdown from closed bars only."""
    start = (pd.Timestamp.now("UTC") - pd.Timedelta(days=WARMUP_DAYS + 30)).strftime("%Y-%m-%d")
    df = fetch_klines(SYMBOL, "1d", start=start, force=True)
    # Drop the in-progress candle: keep bars whose full day has elapsed.
    closed = df[df.index <= pd.Timestamp.now("UTC").normalize() - pd.Timedelta(days=1)]
    if closed.empty:
        closed = df.iloc[:-1]
    target = trend_ensemble(closed, threshold=THRESHOLD)
    votes = _trend_votes(closed).iloc[-1]
    up = int(votes.sum())
    return {
        "bar_time": closed.index[-1],
        "price": float(closed["close"].iloc[-1]),
        "target": float(target.iloc[-1]),
        "votes": {k: int(votes[k]) for k in SIGNAL_LABELS},
        "votes_up": up,
        "agreement": up / len(SIGNAL_LABELS),
    }


# ---- Portfolio ------------------------------------------------------------- #
def equity(s: dict, price: float) -> float:
    return s["cash"] + s["btc"] * price


def compute_rebalance(s: dict, price: float, target_frac: float) -> dict:
    """Compute what a rebalance WOULD do (no mutation). Returns a plan dict."""
    eq = equity(s, price)
    cur_frac = (s["btc"] * price) / eq if eq else 0.0
    target_units = (eq * target_frac) / price
    delta_units = target_units - s["btc"]
    trade_value = abs(delta_units) * price
    material = trade_value >= MIN_TRADE_FRAC * eq
    cost = trade_value * (FEE + SLIPPAGE) if material else 0.0
    return {
        "eq_before": eq,
        "cur_frac": cur_frac,
        "delta_units": delta_units if material else 0.0,
        "target_units": target_units if material else s["btc"],
        "trade_value": trade_value if material else 0.0,
        "cost": cost,
        "material": material,
    }


def classify(s: dict, plan: dict, target_frac: float) -> tuple[str, str]:
    """Return (action, side) among ENTER/EXIT/ADD/TRIM/HOLD."""
    was_holding = s["btc"] * 1.0 > 1e-9
    if not plan["material"]:
        return "HOLD", "NONE"
    if plan["delta_units"] > 0:                       # buying
        return ("ADD" if was_holding else "ENTER"), "BUY"
    else:                                             # selling
        return ("EXIT" if target_frac == 0 else "TRIM"), "SELL"


def apply_plan(s: dict, price: float, plan: dict) -> None:
    """Mutate state to execute the plan (buy/sell + costs)."""
    s["cash"] -= plan["delta_units"] * price   # buy (>0) reduces cash; sell adds
    s["cash"] -= plan["cost"]
    s["btc"] = plan["target_units"]


# ---- Report ---------------------------------------------------------------- #
def build_report(s_before: dict, s_after: dict, sig: dict, plan: dict,
                 action: str, side: str, dry: bool) -> dict:
    price = sig["price"]
    eq_after = equity(s_after, price)
    btc_value = s_after["btc"] * price
    cur_expo = btc_value / eq_after if eq_after else 0.0
    ret_pct = (eq_after / s_after["initial_capital"] - 1) * 100
    agr = sig["agreement"]
    summary = (
        f"{action}: {sig['votes_up']}/7 signals up → target {sig['target']*100:.0f}%. "
        + ("no material change (dust/HOLD)." if side == "NONE" else
           f"{side} {abs(plan['delta_units']):.6f} BTC @ ${price:,.2f}, cost ${plan['cost']:.2f}.")
        + f" Equity ${plan['eq_before']:,.2f} → ${eq_after:,.2f}."
        + (" [DRY-RUN — state unchanged]" if dry else "")
    )
    return {
        "timestamp_utc": pd.Timestamp.now("UTC").isoformat(),
        "closed_bar_date": str(sig["bar_time"].date()),
        "run_number": s_after["runs"],
        "dry_run": dry,
        "action": action,
        "side": side,
        "btc_price": round(price, 2),
        "agreement": f"{sig['votes_up']}/7 = {agr:.3f}",
        "signals": {SIGNAL_LABELS[k]: ("UP" if v else "down") for k, v in sig["votes"].items()},
        "previous_exposure_pct": round(plan["cur_frac"] * 100, 2),
        "new_target_exposure_pct": round(sig["target"] * 100, 2),
        "btc_units_traded": round(plan["delta_units"], 6),
        "trade_value_usd": round(plan["trade_value"], 2),
        "cost_usd": round(plan["cost"], 2),
        "cash_usd": round(s_after["cash"], 2),
        "btc_units": round(s_after["btc"], 6),
        "btc_value_usd": round(btc_value, 2),
        "total_equity_usd": round(eq_after, 2),
        "total_return_pct": round(ret_pct, 2),
        "current_exposure_pct": round(cur_expo * 100, 2),
        "summary": summary,
    }


def print_report(r: dict) -> None:
    ico = {"ENTER": "🟢", "EXIT": "🔴", "ADD": "🔺", "TRIM": "🔻", "HOLD": "⚪"}.get(r["action"], "•")
    print("\n" + "=" * 60)
    print(f" {ico} {r['action']}   |   closed bar {r['closed_bar_date']}   |   run #{r['run_number']}"
          + ("   (DRY-RUN)" if r["dry_run"] else ""))
    print("=" * 60)
    print("  --- SIGNALS (7 trend votes) ---")
    for name, st in r["signals"].items():
        print(f"    {'✅' if st == 'UP' else '❌'}  {name:<24} {st}")
    print(f"    → agreement {r['agreement']}   (gate {THRESHOLD})")
    print("  --- DECISION ---")
    print(f"    BTC price        : ${r['btc_price']:,.2f}")
    print(f"    Exposure         : {r['previous_exposure_pct']:.1f}%  →  target {r['new_target_exposure_pct']:.1f}%")
    print(f"    Action           : {r['action']}  ({r['side']})")
    if r["side"] != "NONE":
        print(f"    Trade            : {r['side']} {abs(r['btc_units_traded']):.6f} BTC  "
              f"(${r['trade_value_usd']:,.2f})   cost ${r['cost_usd']:.2f}")
    print("  --- PORTFOLIO AFTER ---")
    print(f"    Cash             : ${r['cash_usd']:,.2f}")
    print(f"    BTC              : {r['btc_units']:.6f}  (${r['btc_value_usd']:,.2f})")
    print(f"    Equity           : ${r['total_equity_usd']:,.2f}   (return {r['total_return_pct']:+.2f}%)")
    print(f"    Current exposure : {r['current_exposure_pct']:.1f}%")
    print("  --- SUMMARY ---")
    print(f"    {r['summary']}")
    print("=" * 60 + "\n")


# ---- Mobile push notify (Telegram / ntfy / Discord / webhook) --------------- #
def notify(r: dict) -> None:
    """Push the report to any configured channel. Best-effort — see bot/notify.py.

    Only actionable events (ENTER/EXIT/ADD/TRIM) notify by default; set
    BOT_NOTIFY_HEARTBEAT=1 to also receive daily HOLD messages.
    """
    try:
        from notify import notify as _push  # bot/notify.py (same dir, on sys.path)
        _push(r)
    except Exception as e:
        print(f"[notify] failed ({e})")


# ---- Commands -------------------------------------------------------------- #
def cmd_status(args):
    s = load_state()
    sig = signal_detail()
    plan = compute_rebalance(s, sig["price"], sig["target"])
    action, side = classify(s, plan, sig["target"])
    # status = a dry report against current state (no mutation)
    r = build_report(s, {**s, "runs": s["runs"]}, sig, plan, action, side, dry=True)
    save_status(r)
    print_report(r)
    if args.json:
        print(json.dumps(r, indent=2))


def cmd_run(args):
    s = load_state()
    sig = signal_detail()
    already = (s["last_bar"] == str(sig["bar_time"]))
    if already and not args.dry_run:
        print(f"[run] already acted on bar {sig['bar_time'].date()} — nothing to do.")
        return

    plan = compute_rebalance(s, sig["price"], sig["target"])
    action, side = classify(s, plan, sig["target"])

    s_before = dict(s)
    if not args.dry_run:
        if plan["material"]:
            apply_plan(s, sig["price"], plan)
        s["last_bar"] = str(sig["bar_time"])
        s["last_target"] = sig["target"]
        s["runs"] += 1
        save_state(s)

    r = build_report(s_before, s if not args.dry_run else s_before, sig, plan, action, side, args.dry_run)
    if not args.dry_run:
        save_status(r)   # snapshot for the Telegram command bot to read
    print_report(r)
    if not args.dry_run and side != "NONE":
        log_trade({k: r[k] for k in (
            "timestamp_utc", "closed_bar_date", "run_number", "action", "side",
            "btc_price", "agreement", "previous_exposure_pct", "new_target_exposure_pct",
            "btc_units_traded", "trade_value_usd", "cost_usd", "total_equity_usd",
            "total_return_pct")})
    if args.json:
        print(json.dumps(r, indent=2))
    if not args.dry_run:
        notify(r)


def cmd_reset(args):
    if STATE.exists():
        STATE.unlink()
    if TRADES.exists():
        TRADES.unlink()
    save_state(load_state())
    print("[reset] fresh paper portfolio created.")


def cmd_loop(args):
    print("[loop] running daily. Ctrl-C to stop.")
    while True:
        try:
            cmd_run(args)
        except Exception as e:
            print(f"[loop] error: {e}")
        time.sleep(24 * 3600)


def main():
    # Windows consoles default to cp1252 and choke on the report's emoji/✅ glyphs.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="trend_ensemble paper bot / scheduler")
    ap.add_argument("command", choices=["status", "run", "reset", "loop"])
    ap.add_argument("--dry-run", action="store_true",
                    help="compute + report WITHOUT changing state or logging")
    ap.add_argument("--json", action="store_true", help="also print the machine JSON blob")
    args = ap.parse_args()
    {"status": cmd_status, "run": cmd_run, "reset": cmd_reset, "loop": cmd_loop}[args.command](args)


if __name__ == "__main__":
    main()


# =============================================================================
# SCHEDULING — run this once a day, a few minutes AFTER 00:00 UTC (bar is final)
# =============================================================================
# Linux/macOS cron  (5 past midnight UTC; set CRON_TZ or use a UTC box):
#   5 0 * * *  cd /path/to/project && /usr/bin/python bot/paper_bot.py run >> bot/bot.log 2>&1
#
# Windows Task Scheduler (PowerShell, one-time setup) — runs 12:05 AM UTC daily:
#   $act = New-ScheduledTaskAction -Execute "python" `
#          -Argument "bot\paper_bot.py run" -WorkingDirectory "C:\Users\abhishek.a.tiwari\Downloads\New folder"
#   $trg = New-ScheduledTaskTrigger -Daily -At 12:05AM
#   Register-ScheduledTask -TaskName "TrendEnsembleBot" -Action $act -Trigger $trg
#   # (Task Scheduler uses LOCAL time; pick the local clock time equal to 00:05 UTC.)
#
# GitHub Actions (always-on, never misses) — .github/workflows/bot.yml:
#   on:
#     schedule: [{ cron: "5 0 * * *" }]   # 00:05 UTC daily
#   Commit state.json back to the repo (or use a Gist/DB) so state persists.
# =============================================================================
