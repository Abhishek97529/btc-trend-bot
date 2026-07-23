"""
Telegram command handler (polling) for the paper bot.

Lets you type commands in your Telegram chat and get replies:
    /portfolio  (or /status)  -> equity, cash, BTC, exposure, return, live signal
    /signal                   -> the 7 trend votes + agreement + target exposure
    /trades                   -> your most recent paper trades
    /help                     -> this list

HOW IT WORKS (no always-on server needed):
  A GitHub Actions workflow runs this every ~15 min. Each run it asks Telegram
  "any new messages?" (getUpdates), answers the commands, and stores the last
  processed update id in bot/tg_offset.json (committed back so we don't reply
  twice). Replies therefore lag up to one polling interval.

SECURITY: only messages from TELEGRAM_CHAT_ID are answered; everyone else is
ignored (but still marked read so we don't reprocess).

Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (same secrets as notifications).
Run:  python bot/tg_commands.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # bot/
from paper_bot import (  # noqa: E402
    load_state, signal_detail, compute_rebalance, classify, equity,
    TRADES, SIGNAL_LABELS, THRESHOLD,
)

OFFSET_FILE = Path(__file__).resolve().parent / "tg_offset.json"
HELP = (
    "🤖 Trend Ensemble paper bot — commands:\n"
    "/portfolio (or /status) — equity, cash, BTC, exposure, return + live signal\n"
    "/signal — the 7 trend votes and target exposure\n"
    "/trades — your most recent paper trades\n"
    "/help — this message"
)


# ---- Reply builders -------------------------------------------------------- #
def reply_portfolio() -> str:
    s = load_state()
    sig = signal_detail()
    plan = compute_rebalance(s, sig["price"], sig["target"])
    action, _ = classify(s, plan, sig["target"])
    eq = equity(s, sig["price"])
    btc_val = s["btc"] * sig["price"]
    expo = (btc_val / eq * 100) if eq else 0.0
    ret = (eq / s["initial_capital"] - 1) * 100
    return (
        "📊 PORTFOLIO\n"
        f"Equity   : ${eq:,.2f}  ({ret:+.2f}%)\n"
        f"Cash     : ${s['cash']:,.2f}\n"
        f"BTC      : {s['btc']:.6f}  (${btc_val:,.2f})\n"
        f"Exposure : {expo:.1f}%\n"
        f"BTC price: ${sig['price']:,.2f}\n"
        "\n"
        f"Signal   : {action}  ({sig['votes_up']}/7 = {sig['agreement']:.2f}, gate {THRESHOLD})\n"
        f"Target   : {sig['target']*100:.0f}% exposure\n"
        f"As of closed bar {sig['bar_time'].date()}"
    )


def reply_signal() -> str:
    sig = signal_detail()
    lines = ["📶 SIGNAL — 7 trend votes"]
    for k, label in SIGNAL_LABELS.items():
        lines.append(f"{'✅' if sig['votes'][k] else '❌'} {label}")
    lines.append(f"→ {sig['votes_up']}/7 = {sig['agreement']:.2f}  (gate {THRESHOLD})")
    lines.append(f"Target exposure: {sig['target']*100:.0f}%")
    lines.append(f"BTC ${sig['price']:,.2f} · bar {sig['bar_time'].date()}")
    return "\n".join(lines)


def reply_trades(n: int = 10) -> str:
    if not TRADES.exists():
        return "🧾 No paper trades yet — the bot has been flat (in cash)."
    import pandas as pd
    df = pd.read_csv(TRADES).tail(n)
    lines = [f"🧾 Last {len(df)} trades:"]
    for _, r in df.iterrows():
        lines.append(
            f"{r['closed_bar_date']}  {r['action']} {r['side']}  "
            f"{abs(float(r['btc_units_traded'])):.4f} BTC @ ${float(r['btc_price']):,.0f}  "
            f"→ eq ${float(r['total_equity_usd']):,.0f}"
        )
    return "\n".join(lines)


def route(cmd: str) -> str:
    if cmd in ("portfolio", "status"):
        return reply_portfolio()
    if cmd == "signal":
        return reply_signal()
    if cmd == "trades":
        return reply_trades()
    if cmd in ("help", "start"):
        return HELP
    return f"Unknown command /{cmd}.\n\n{HELP}"


# ---- Telegram plumbing ----------------------------------------------------- #
def _load_offset() -> int | None:
    if OFFSET_FILE.exists():
        try:
            return int(json.loads(OFFSET_FILE.read_text())["offset"])
        except Exception:
            return None
    return None


def _save_offset(offset: int) -> None:
    OFFSET_FILE.write_text(json.dumps({"offset": offset}))


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        print("[tg] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — nothing to do.")
        return

    import requests
    api = f"https://api.telegram.org/bot{token}"
    params = {"timeout": 0}
    offset = _load_offset()
    if offset is not None:
        params["offset"] = offset

    r = requests.get(f"{api}/getUpdates", params=params, timeout=30)
    r.raise_for_status()
    updates = r.json().get("result", [])
    if not updates:
        print("[tg] no new messages.")
        return

    highest = offset - 1 if offset is not None else -1
    answered = 0
    for u in sorted(updates, key=lambda x: x["update_id"]):
        highest = max(highest, u["update_id"])
        msg = u.get("message") or u.get("edited_message")
        if not msg:
            continue
        if str(msg.get("chat", {}).get("id")) != str(chat_id):
            continue  # not you — ignore (still marked read via offset)
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue
        cmd = text[1:].split()[0].split("@")[0].lower()
        try:
            body = route(cmd)
        except Exception as e:
            body = f"⚠️ Error handling /{cmd}: {e}"
        try:
            requests.post(f"{api}/sendMessage",
                          json={"chat_id": chat_id, "text": body},
                          timeout=15).raise_for_status()
            answered += 1
            print(f"[tg] answered /{cmd}")
        except Exception as e:
            print(f"[tg] send failed for /{cmd}: {e}")

    _save_offset(highest + 1)
    print(f"[tg] processed {len(updates)} update(s), answered {answered}.")


if __name__ == "__main__":
    main()
