// Cloudflare Worker — INSTANT Telegram command handler for the BTC trend bot.
//
// Telegram pushes each command here via webhook; this reads the committed
// bot/status.json (and bot/trades.csv) from the private GitHub repo and replies
// in ~1 second. Replaces the throttled GitHub-Actions poller.
//
// Secrets to set in: Worker -> Settings -> Variables and Secrets
//   TELEGRAM_BOT_TOKEN  from @BotFather
//   TELEGRAM_CHAT_ID    your chat id (ONLY this chat is answered)
//   GITHUB_TOKEN        fine-grained PAT, read-only "Contents" on Abhishek97529/btc-trend-bot
//   WEBHOOK_SECRET      any random string; must match setWebhook's secret_token

const OWNER = "Abhishek97529";
const REPO = "btc-trend-bot";
const THRESHOLD = 0.5;

const HELP =
  "\u{1F916} Trend Ensemble paper bot — commands:\n" +
  "/portfolio (or /status) — equity, cash, BTC, exposure, return + signal\n" +
  "/signal — the 7 trend votes and target exposure\n" +
  "/trades — your most recent paper trades\n" +
  "/help — this message";

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok");

    // Only accept requests Telegram signed with our secret header.
    if (env.WEBHOOK_SECRET &&
        request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try { update = await request.json(); } catch { return new Response("ok"); }

    const msg = update.message || update.edited_message;
    if (!msg || !msg.text) return new Response("ok");
    // TELEGRAM_CHAT_ID may be a comma-separated allow-list (you + friends).
    const allowed = String(env.TELEGRAM_CHAT_ID).split(",").map((s) => s.trim());
    if (!allowed.includes(String(msg.chat.id))) return new Response("ok");

    const text = msg.text.trim();
    if (!text.startsWith("/")) return new Response("ok");
    const cmd = text.slice(1).split(/\s+/)[0].split("@")[0].toLowerCase();

    let reply;
    try { reply = await route(cmd, env); }
    catch (e) { reply = "⚠️ Error: " + e.message; }

    // Reply to whoever sent the command (not a fixed chat).
    await sendMessage(env, msg.chat.id, reply);
    return new Response("ok");
  },
};

async function ghFile(path, env) {
  const r = await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/contents/${path}`, {
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github.raw",
      "User-Agent": "btc-bot-worker",
    },
  });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`GitHub ${r.status}`);
  return await r.text();
}

async function loadStatus(env) {
  const txt = await ghFile("bot/status.json", env);
  return txt ? JSON.parse(txt) : null;
}

async function route(cmd, env) {
  if (cmd === "portfolio" || cmd === "status") return replyPortfolio(await loadStatus(env));
  if (cmd === "signal") return replySignal(await loadStatus(env));
  if (cmd === "trades") return replyTrades(env);
  if (cmd === "help" || cmd === "start") return HELP;
  return `Unknown command /${cmd}.\n\n${HELP}`;
}

function replyPortfolio(r) {
  if (!r) return "\u{1F4CA} No snapshot yet — the daily bot hasn't run. It'll appear after the first run.";
  return (
    "\u{1F4CA} PORTFOLIO\n" +
    `Equity   : $${fmt(r.total_equity_usd)}  (${sign(r.total_return_pct)}%)\n` +
    `Cash     : $${fmt(r.cash_usd)}\n` +
    `BTC      : ${Number(r.btc_units).toFixed(6)}  ($${fmt(r.btc_value_usd)})\n` +
    `Exposure : ${Number(r.current_exposure_pct).toFixed(1)}%\n` +
    `BTC price: $${fmt(r.btc_price)}\n\n` +
    `Signal   : ${r.action}  (${r.agreement}, gate ${THRESHOLD})\n` +
    `Target   : ${Number(r.new_target_exposure_pct).toFixed(0)}% exposure\n` +
    `As of closed bar ${r.closed_bar_date}`
  );
}

function replySignal(r) {
  if (!r) return "\u{1F4F6} No snapshot yet — run the daily bot first.";
  const lines = ["\u{1F4F6} SIGNAL — 7 trend votes"];
  for (const [label, st] of Object.entries(r.signals || {})) {
    lines.push(`${st === "UP" ? "✅" : "❌"} ${label}`);
  }
  lines.push(`→ ${r.agreement}  (gate ${THRESHOLD})`);
  lines.push(`Target exposure: ${Number(r.new_target_exposure_pct).toFixed(0)}%`);
  lines.push(`BTC $${fmt(r.btc_price)} · bar ${r.closed_bar_date}`);
  return lines.join("\n");
}

async function replyTrades(env) {
  const csv = await ghFile("bot/trades.csv", env);
  if (!csv) return "\u{1F9FE} No paper trades yet — the bot has been flat (in cash).";
  const rows = csv.trim().split("\n");
  const header = rows[0].split(",");
  const col = (name) => header.indexOf(name);
  const last = rows.slice(1).slice(-10);
  const lines = [`\u{1F9FE} Last ${last.length} trades:`];
  for (const line of last) {
    const c = line.split(",");
    lines.push(
      `${c[col("closed_bar_date")]}  ${c[col("action")]} ${c[col("side")]}  ` +
      `${Math.abs(parseFloat(c[col("btc_units_traded")])).toFixed(4)} BTC @ ` +
      `$${Math.round(parseFloat(c[col("btc_price")]))}  → eq $${Math.round(parseFloat(c[col("total_equity_usd")]))}`
    );
  }
  return lines.join("\n");
}

function fmt(n) {
  return Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function sign(n) {
  return (Number(n) >= 0 ? "+" : "") + Number(n).toFixed(2);
}

async function sendMessage(env, chatId, text) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
}
