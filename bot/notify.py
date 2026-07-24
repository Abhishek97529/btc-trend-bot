"""
Mobile push notifications for the paper bot.

Configure ONE (or more) of these via environment variables — the notifier
auto-detects whatever is set and sends to all of them. All sends are best-effort:
a failure is logged and never crashes the bot.

  Telegram (recommended)
      TELEGRAM_BOT_TOKEN   from @BotFather
      TELEGRAM_CHAT_ID     your chat id (get it from @userinfobot)

  ntfy.sh (simplest — just install the ntfy app and subscribe to your topic)
      NTFY_TOPIC           e.g. "my-btc-bot-9271"  (uses https://ntfy.sh/<topic>)
      NTFY_URL             optional full URL if self-hosting

  Discord
      DISCORD_WEBHOOK_URL  a channel webhook URL

  Generic
      BOT_WEBHOOK_URL      POSTs the raw report JSON to any endpoint

By default only actionable events (ENTER/EXIT/ADD/TRIM) notify. Set
      BOT_NOTIFY_HEARTBEAT=1
to also get the daily HOLD "still watching" message.

Test it:  python bot/notify.py       # sends a sample notification to all configured channels
"""
from __future__ import annotations

import os

ICON = {"ENTER": "🟢", "EXIT": "🔴", "ADD": "🔺", "TRIM": "🔻", "HOLD": "⚪"}
NTFY_TAGS = {"ENTER": "green_circle", "EXIT": "red_circle", "ADD": "chart_with_upwards_trend",
             "TRIM": "chart_with_downwards_trend", "HOLD": "white_circle"}


def _title(r: dict) -> str:
    """ASCII-safe one-liner (used for ntfy/Telegram/Discord headers)."""
    a = r["action"]
    if r["side"] == "NONE":
        return f"{a} - BTC ${r['btc_price']:,.0f} ({r['new_target_exposure_pct']:.0f}% target)"
    return f"{a} {r['new_target_exposure_pct']:.0f}% - BTC ${r['btc_price']:,.0f}"


def _body(r: dict) -> str:
    lines = [
        f"{ICON.get(r['action'], '')} {r['action']}   (bar {r['closed_bar_date']})",
        f"Signals: {r['agreement']}",
        f"Exposure: {r['previous_exposure_pct']:.0f}% -> {r['new_target_exposure_pct']:.0f}%",
    ]
    if r["side"] != "NONE":
        lines.append(f"{r['side']} {abs(r['btc_units_traded']):.6f} BTC "
                     f"(${r['trade_value_usd']:,.2f}), cost ${r['cost_usd']:.2f}")
    lines.append(f"Equity: ${r['total_equity_usd']:,.2f} ({r['total_return_pct']:+.2f}%)")
    return "\n".join(lines)


def _send_telegram(title: str, body: str) -> str | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return None
    import requests
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    text = f"<b>{title}</b>\n<pre>{body}</pre>"
    # TELEGRAM_CHAT_ID may be a comma-separated list (you + friends) — send to each.
    chat_ids = [c.strip() for c in chat.split(",") if c.strip()]
    for cid in chat_ids:
        requests.post(url, json={"chat_id": cid, "text": text, "parse_mode": "HTML",
                                 "disable_web_page_preview": True}, timeout=15).raise_for_status()
    return "telegram"


def _send_ntfy(title: str, body: str, action: str) -> str | None:
    topic = os.environ.get("NTFY_TOPIC")
    url = os.environ.get("NTFY_URL") or (f"https://ntfy.sh/{topic}" if topic else None)
    if not url:
        return None
    import requests
    prio = "high" if action in ("ENTER", "EXIT") else "default"
    requests.post(url, data=body.encode("utf-8"),
                  headers={"Title": title, "Priority": prio, "Tags": NTFY_TAGS.get(action, "")},
                  timeout=15).raise_for_status()
    return "ntfy"


def _send_discord(title: str, body: str) -> str | None:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return None
    import requests
    requests.post(url, json={"content": f"**{title}**\n```\n{body}\n```"}, timeout=15).raise_for_status()
    return "discord"


def _send_generic(report: dict) -> str | None:
    url = os.environ.get("BOT_WEBHOOK_URL")
    if not url:
        return None
    import requests
    requests.post(url, json=report, timeout=15).raise_for_status()
    return "webhook"


def notify(report: dict, force: bool = False) -> list[str]:
    """Send the report to every configured channel. Returns list of channels hit."""
    actionable = report.get("action") != "HOLD"
    heartbeat = os.environ.get("BOT_NOTIFY_HEARTBEAT") in ("1", "true", "True")
    if not (force or actionable or heartbeat):
        return []
    title, body = _title(report), _body(report)
    sent = []
    for fn in (lambda: _send_telegram(title, body),
               lambda: _send_ntfy(title, body, report.get("action", "")),
               lambda: _send_discord(title, body),
               lambda: _send_generic(report)):
        try:
            ch = fn()
            if ch:
                sent.append(ch)
        except Exception as e:
            print(f"[notify] channel failed: {e}")
    if sent:
        print(f"[notify] sent to: {', '.join(sent)}")
    elif force:
        print("[notify] no channels configured (set TELEGRAM_*, NTFY_TOPIC, DISCORD_WEBHOOK_URL, "
              "or BOT_WEBHOOK_URL).")
    return sent


if __name__ == "__main__":
    sample = {
        "action": "ENTER", "side": "BUY", "btc_price": 64756.00,
        "agreement": "4/7 = 0.571", "closed_bar_date": "2026-07-22",
        "previous_exposure_pct": 0.0, "new_target_exposure_pct": 57.0,
        "btc_units_traded": 0.088012, "trade_value_usd": 5700.00, "cost_usd": 8.55,
        "total_equity_usd": 9991.45, "total_return_pct": -0.09,
    }
    print("Sending a TEST notification to all configured channels...")
    notify(sample, force=True)
