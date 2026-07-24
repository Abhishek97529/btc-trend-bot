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
  "/stats — since-inception performance (return, trades, win rate, drawdown)\n" +
  "/trades — your most recent paper trades\n" +
  "/price — LIVE BTC price vs the last daily snapshot\n" +
  "/health — is the daily run alive? (warns on a missed/stale run)\n" +
  "/live — show live/paper mode  (owner: /live on CONFIRM · /live off)\n" +
  "/help — this message";

const FLAG_PATH = "bot/live_flag.json";

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
    const parts = text.slice(1).split(/\s+/);
    const cmd = parts[0].split("@")[0].toLowerCase();

    let reply;
    try {
      if (cmd === "live") {
        // Only the OWNER (first entry in the allow-list) may TOGGLE live mode.
        const isOwner = String(msg.chat.id) === allowed[0];
        reply = await handleLive(parts, isOwner, env);
      } else {
        reply = await route(cmd, env);
      }
    } catch (e) { reply = "⚠️ Error: " + e.message; }

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
  if (cmd === "stats") return replyStats(await loadStatus(env), await ghFile("bot/trades.csv", env));
  if (cmd === "trades") return replyTrades(env);
  if (cmd === "price") return replyPrice(await loadStatus(env));
  if (cmd === "health") return replyHealth(await loadStatus(env));
  if (cmd === "help" || cmd === "start") return HELP;
  return `Unknown command /${cmd}.\n\n${HELP}`;
}

// --- /live : show mode; owner can toggle the repo flag that arms real trading. #
// Turning ON writes bot/live_flag.json = {live:true}. The daily GitHub Actions
// run still won't trade unless the COINDCX_LIVE_ARMED secret is ALSO set (the
// second factor) — so a leaked bot token alone cannot start real trading.
async function handleLive(parts, isOwner, env) {
  const sub = (parts[1] || "").toLowerCase();

  if (!sub) return await liveStatus(env);            // anyone may READ the mode
  if (!isOwner) return "\u{26D4} Only the bot owner can change live mode.";

  if (sub === "off") {
    await setLive(false, env);
    return "\u{1F6D1} LIVE turned OFF. The bot paper-trades on the next daily run (5:50 AM IST).";
  }
  if (sub === "on") {
    if ((parts[2] || "") !== "CONFIRM") {
      return (
        "\u{26A0}\u{FE0F} This arms REAL-MONEY trading on CoinDCX.\n" +
        "It takes effect on the NEXT daily run (5:50 AM IST), and ONLY if the " +
        "COINDCX_LIVE_ARMED secret is set in the repo (second factor).\n\n" +
        "To confirm, send exactly:\n/live on CONFIRM"
      );
    }
    await setLive(true, env);
    return (
      "\u{2705} LIVE flag SET. Real orders will run at the next daily cycle " +
      "(5:50 AM IST) IF COINDCX_LIVE_ARMED=1 is set in the repo.\n" +
      "Per-order size is still capped by COINDCX_MAX_ORDER_QUOTE.\n" +
      "Send /live to check · /live off to stop."
    );
  }
  return "Usage:\n/live — show mode\n/live on CONFIRM — go live\n/live off — stop";
}

async function liveStatus(env) {
  const txt = await ghFile(FLAG_PATH, env);
  const on = txt ? JSON.parse(txt).live === true : false;
  return on
    ? "\u{1F7E2} Live flag: ON — real trading on the next daily run (if armed). /live off to stop."
    : "\u{26AA} Live flag: OFF — paper trading. /live on CONFIRM to go live.";
}

async function setLive(on, env) {
  const body = { live: on };
  // GitHub contents API needs the current sha to update an existing file.
  let sha;
  const cur = await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/contents/${FLAG_PATH}`, {
    headers: ghHeaders(env, "application/vnd.github+json"),
  });
  if (cur.ok) sha = (await cur.json()).sha;
  const content = btoa(JSON.stringify(body, null, 2) + "\n");
  const r = await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/contents/${FLAG_PATH}`, {
    method: "PUT",
    headers: { ...ghHeaders(env, "application/vnd.github+json"), "Content-Type": "application/json" },
    body: JSON.stringify({ message: `bot: live=${on} via telegram [skip ci]`, content, sha }),
  });
  if (!r.ok) throw new Error(`GitHub PUT ${r.status}: ${(await r.text()).slice(0, 160)}`);
}

function ghHeaders(env, accept) {
  return {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: accept,
    "User-Agent": "btc-bot-worker",
  };
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

// --- /stats : cumulative performance since inception ------------------------ #
function replyStats(r, csv) {
  if (!r) return "\u{1F4C8} No snapshot yet — the daily bot hasn't run.";
  const eq = Number(r.total_equity_usd);
  const ret = Number(r.total_return_pct);
  const init = eq / (1 + ret / 100);           // back out the starting capital
  const lines = [
    "\u{1F4C8} STATS (since inception)",
    `Equity   : $${fmt(eq)}  (${sign(ret)}%)`,
    `Start    : $${fmt(init)}`,
    `Runs     : ${r.run_number ?? 0} daily evaluations`,
  ];

  const rows = csv ? csv.trim().split("\n") : [];
  if (rows.length <= 1) {
    lines.push("Trades   : 0 — the bot has been flat (in cash) the whole time.");
    return lines.join("\n");
  }
  const header = rows[0].split(",");
  const col = (n) => header.indexOf(n);
  const data = rows.slice(1).map((l) => l.split(","));

  // Action breakdown + equity path (equity AFTER each logged trade, plus current).
  const counts = {};
  const equities = [];
  for (const c of data) {
    const a = c[col("action")];
    counts[a] = (counts[a] || 0) + 1;
    equities.push(parseFloat(c[col("total_equity_usd")]));
  }
  equities.push(eq);
  const peak = Math.max(...equities);
  const ddFromPeak = (eq / peak - 1) * 100;

  // Round-trip win rate: ENTER ... EXIT (target back to 0). Win = equity up over the trip.
  let trips = 0, wins = 0, entryEq = null;
  for (const c of data) {
    const a = c[col("action")];
    const e = parseFloat(c[col("total_equity_usd")]);
    if (a === "ENTER") entryEq = e;
    else if (a === "EXIT" && entryEq !== null) {
      trips += 1;
      if (e > entryEq) wins += 1;
      entryEq = null;
    }
  }

  const first = data[0][col("closed_bar_date")];
  const last = data[data.length - 1][col("closed_bar_date")];
  lines.push(
    `Trades   : ${data.length}  (` +
      Object.entries(counts).map(([k, v]) => `${k} ${v}`).join(", ") + ")",
    `Round trips: ${trips}${trips ? `  ·  ${wins}/${trips} winners (${Math.round((wins / trips) * 100)}%)` : ""}`,
    `Peak eq  : $${fmt(peak)}  ·  now ${sign(ddFromPeak)}% from peak`,
    `Traded   : ${first} → ${last}`,
  );
  return lines.join("\n");
}

// --- /price : LIVE BTC price vs the last daily snapshot --------------------- #
async function replyPrice(r) {
  let live = null;
  try {
    const resp = await fetch("https://api.coinbase.com/v2/prices/BTC-USD/spot", {
      headers: { "User-Agent": "btc-bot-worker" },
    });
    if (resp.ok) live = parseFloat((await resp.json()).data.amount);
  } catch { /* fall through to snapshot-only */ }

  if (live === null && !r) return "\u{20BF} Couldn't fetch a live price right now — try again.";
  const lines = ["\u{20BF} BTC price"];
  if (live !== null) lines.push(`Live     : $${fmt(live)}`);
  if (r) {
    const snap = Number(r.btc_price);
    lines.push(`Snapshot : $${fmt(snap)}  (bar ${r.closed_bar_date})`);
    if (live !== null) lines.push(`Since bar: ${sign((live / snap - 1) * 100)}%`);
  }
  lines.push("(the bot only acts on the daily CLOSE, not the live price)");
  return lines.join("\n");
}

// --- /health : is the daily run alive? -------------------------------------- #
function replyHealth(r) {
  if (!r || !r.timestamp_utc)
    return "\u{1F534} No run recorded yet — the daily job hasn't produced a snapshot.";
  const last = new Date(r.timestamp_utc);
  const ageH = (Date.now() - last.getTime()) / 3.6e6;
  const ageStr = ageH < 48 ? `${ageH.toFixed(1)}h ago` : `${(ageH / 24).toFixed(1)}d ago`;
  let head;
  if (ageH < 30) head = "\u{2705} Healthy — daily run is on schedule.";
  else if (ageH < 50) head = "\u{26A0}\u{FE0F} Warning — today's run looks late or missed.";
  else head = "\u{1F534} STALE — the bot likely stopped running. Check GitHub Actions.";
  return [
    head,
    `Last run : ${ageStr}  (${r.timestamp_utc.slice(0, 16).replace("T", " ")} UTC)`,
    `Last bar : ${r.closed_bar_date}`,
    `Last act : ${r.action}  (${r.agreement})`,
    "Daily run fires ~00:20 UTC (5:50 AM IST).",
  ].join("\n");
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
