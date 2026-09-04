# Telegram status bot

The Cloudflare Worker is a read-only interface to the six paper accounts. It
reads committed runtime snapshots from GitHub and never submits orders.

| Group | Commands |
|---|---|
| Suite | `/all`, `/health`, `/price`, `/help` |
| Daily spot | `/daily`, `/daily_signal`, `/daily_stats`, `/daily_trades` |
| Dual-trend spot | `/dual4h`, `/dual4h_trades` |
| Dual-trend shadow | `/shadow4h`, `/shadow4h_trades` |
| MA250 long/flat | `/maflat`, `/maflat_trades` |
| MA250 long/short | `/mashort`, `/mashort_trades` |
| MA250 vol-targeted | `/mavol`, `/mavol_trades` |

Older commands remain aliases so existing bookmarks continue working. `/live`
only reports that live trading is disabled.

The canonical Telegram menu payload is
[`telegram-commands.json`](telegram-commands.json). Apply it through Telegram's
`setMyCommands` method during deployment. Keep the bot token in a secret store.
