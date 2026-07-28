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
  "/live — live-trading status (disabled by audit)\n" +
  "/help — this message";

const HELP_4H =
  "\n\nFixed MA250 4-hour paper bots:\n" +
  "/4hflat - 2x long / flat portfolio\n" +
  "/4hls - 2x long / 0.5x short portfolio\n" +
  "/4hdual - spot dual-trend portfolio\n" +
  "/4hflattrades or /4hlstrades - recent trades\n" +
  "/4hhealth - scheduler health for both";

const SUITE_HELP =
  "\u{1F916} BTC four-strategy paper suite\n\n" +
  "/all - compact status for all four strategies\n\n" +
  "Daily spot ensemble\n" +
  "/daily - portfolio and current target\n" +
  "/daily_signal - seven trend votes\n" +
  "/daily_stats - performance statistics\n" +
  "/daily_trades - recent trades\n\n" +
  "Four-hour strategies\n" +
  "/dual4h - dual-trend spot portfolio\n" +
  "/dual4h_trades - recent dual-trend trades\n" +
  "/maflat - MA250 2x/flat portfolio\n" +
  "/maflat_trades - recent 2x/flat trades\n" +
  "/mashort - MA250 2x/-0.5x portfolio\n" +
  "/mashort_trades - recent long/short trades\n\n" +
  "/health - scheduler health\n" +
  "/price - current BTC price\n" +
  "/live - disabled; paper only\n" +
  "/help - this message";

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
  const txt = await ghFile("strategies/daily_spot_ensemble/runtime/status.json", env);
  return txt ? JSON.parse(txt) : null;
}

async function load4hStatus(variant, env) {
  const pkg = variant === "long_flat"
    ? "ma250_4h_long_flat"
    : variant === "long_short"
      ? "ma250_4h_long_short"
      : "spot_4h_dual_trend";
  const txt = await ghFile(`strategies/${pkg}/runtime/status.json`, env);
  return txt ? JSON.parse(txt) : null;
}

async function route(cmd, env) {
  if (cmd === "all" || cmd === "status") return replyAll(env);
  if (cmd === "daily" || cmd === "portfolio") return replyPortfolio(await loadStatus(env));
  if (cmd === "daily_signal" || cmd === "signal") return replySignal(await loadStatus(env));
  if (cmd === "daily_stats" || cmd === "stats") return replyStats(
    await loadStatus(env),
    await ghFile("strategies/daily_spot_ensemble/runtime/trades.csv", env));
  if (cmd === "daily_trades" || cmd === "trades") return replyTrades(env);
  if (cmd === "price") return replyPrice(await loadStatus(env));
  if (cmd === "health" || cmd === "4hhealth") return replySuiteHealth(env);
  if (cmd === "maflat" || cmd === "4hflat") return reply4hPortfolio(await load4hStatus("long_flat", env));
  if (cmd === "mashort" || cmd === "4hls") return reply4hPortfolio(await load4hStatus("long_short", env));
  if (cmd === "dual4h" || cmd === "4hdual") return replyDualPortfolio(await load4hStatus("dual", env));
  if (cmd === "maflat_trades" || cmd === "4hflattrades") return reply4hTrades("long_flat", env);
  if (cmd === "mashort_trades" || cmd === "4hlstrades") return reply4hTrades("long_short", env);
  if (cmd === "dual4h_trades") return reply4hTrades("dual", env);
  if (cmd === "help" || cmd === "start") return SUITE_HELP;
  return `Unknown command /${cmd}.\n\n${SUITE_HELP}`;
}

// --- /live : show mode; owner can toggle the repo flag that arms real trading. #
// Turning ON writes bot/live_flag.json = {live:true}. The daily GitHub Actions
// run still won't trade unless the COINDCX_LIVE_ARMED secret is ALSO set (the
// second factor) — so a leaked bot token alone cannot start real trading.
async function handleLive(parts, isOwner, env) {
  return "\u{1F6D1} Live trading is disabled by the 2026-07-28 audit. All four strategies are paper-only.";
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

async function replyAll(env) {
  const [daily, dual, flat, short] = await Promise.all([
    loadStatus(env),
    load4hStatus("dual", env),
    load4hStatus("long_flat", env),
    load4hStatus("long_short", env),
  ]);
  const row = (name, r, exposure) => r
    ? `${name}: $${fmt(r.total_equity_usd)} (${sign(r.total_return_pct)}%) | ${exposure(r)}`
    : `${name}: no snapshot`;
  return [
    "\u{1F4CA} FOUR-STRATEGY PAPER SUITE",
    row("Daily spot", daily, (r) => `${Number(r.current_exposure_pct).toFixed(0)}%`),
    row("Dual 4h spot", dual, (r) => `${sign(r.actual_exposure)}x`),
    row("MA250 flat", flat, (r) => `${sign(r.actual_exposure ?? r.target_exposure)}x`),
    row("MA250 short", short, (r) => `${sign(r.actual_exposure ?? r.target_exposure)}x`),
    "PAPER ONLY",
  ].join("\n");
}

async function replySuiteHealth(env) {
  const [daily, dual, flat, short] = await Promise.all([
    loadStatus(env),
    load4hStatus("dual", env),
    load4hStatus("long_flat", env),
    load4hStatus("long_short", env),
  ]);
  const age = (r) => r
    ? (Date.now() - new Date(r.timestamp_utc).getTime()) / 3.6e6
    : Infinity;
  const line = (name, r, limit) => {
    const hours = age(r);
    const value = Number.isFinite(hours) ? `${hours.toFixed(1)}h ago` : "no snapshot";
    return `${hours <= limit ? "\u{2705}" : "\u{1F534}"} ${name}: ${value}`;
  };
  return [
    "\u{1FA7A} SCHEDULER HEALTH",
    line("Daily spot", daily, 30),
    line("Dual 4h spot", dual, 6),
    line("MA250 flat", flat, 6),
    line("MA250 short", short, 6),
  ].join("\n");
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
  const csv = await ghFile("strategies/daily_spot_ensemble/runtime/trades.csv", env);
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

function reply4hPortfolio(r) {
  if (!r) return "\u{1F534} No 4-hour paper snapshot yet.";
  return [
    `\u{23F1}\u{FE0F} ${r.variant === "long_flat" ? "Fixed 2x long / flat" : "Fixed 2x long / 0.5x short"}`,
    `Equity   : $${fmt(r.total_equity_usd)}  (${sign(r.total_return_pct)}%)`,
    `Position : ${r.side} ${sign(r.actual_exposure ?? r.target_exposure)}x`,
    `BTC qty  : ${Number(r.btc_contract_qty).toFixed(6)}`,
    `BTC      : $${fmt(r.btc_price)}`,
    `Drawdown : ${sign(r.drawdown_pct)}%`,
    `Action   : ${r.action}`,
    `Last bar : ${String(r.closed_bar_time).slice(0, 16).replace("T", " ")} UTC`,
    "PAPER ONLY - no real orders.",
  ].join("\n");
}

function replyDualPortfolio(r) {
  if (!r) return "\u{1F534} No four-hour dual-trend snapshot yet.";
  return [
    "\u{23F1}\u{FE0F} Spot 4h dual trend",
    `Equity   : $${fmt(r.total_equity_usd)}  (${sign(r.total_return_pct)}%)`,
    `Position : ${r.side} ${sign(r.actual_exposure)}x`,
    `BTC      : $${fmt(r.btc_price)}`,
    `Filters  : ${Object.values(r.conditions || {}).filter(Boolean).length}/3`,
    `Drawdown : ${sign(r.drawdown_pct)}%`,
    `Action   : ${r.action}`,
    `Last bar : ${String(r.closed_bar_time).slice(0, 16).replace("T", " ")} UTC`,
    "PAPER ONLY - no real orders.",
  ].join("\n");
}

async function reply4hTrades(variant, env) {
  const pkg = variant === "long_flat"
    ? "ma250_4h_long_flat"
    : variant === "long_short"
      ? "ma250_4h_long_short"
      : "spot_4h_dual_trend";
  const csv = await ghFile(`strategies/${pkg}/runtime/trades.csv`, env);
  if (!csv) return "\u{1F4ED} No 4-hour paper trades yet.";
  const rows = csv.trim().split(/\r?\n/);
  if (rows.length < 2) return "\u{1F4ED} No 4-hour paper trades yet.";
  const headers = rows[0].split(",");
  const ix = (name) => headers.indexOf(name);
  const lines = rows.slice(-5).reverse().map((row) => {
    const c = row.split(",");
    return `${String(c[ix("closed_bar_time")]).slice(0, 10)}  ${c[ix("action")]} ${c[ix("side")]} ${sign(c[ix("target_exposure")])}x @ $${fmt(c[ix("btc_price")])}`;
  });
  return ["\u{1F4DC} Recent 4-hour paper trades", ...lines].join("\n");
}

function reply4hHealth(flat, ls) {
  if (!flat || !ls) return "\u{1F534} One or both fixed 4-hour bots have no snapshot yet.";
  const flatAge = (Date.now() - new Date(flat.timestamp_utc).getTime()) / 3.6e6;
  const lsAge = (Date.now() - new Date(ls.timestamp_utc).getTime()) / 3.6e6;
  const ageH = Math.max(flatAge, lsAge);
  const head = ageH < 6 ? "\u{2705} Healthy - both 4-hour runs are on schedule."
    : ageH < 10 ? "\u{26A0}\u{FE0F} Warning - a fixed 4-hour run may be late."
    : "\u{1F534} STALE - check the fixed 4-hour GitHub workflow.";
  return [
    head,
    `Long/flat : ${flatAge.toFixed(1)}h ago - ${flat.side}`,
    `Long/short: ${lsAge.toFixed(1)}h ago - ${ls.side}`,
    "Runs at 00:10, 04:10, 08:10, 12:10, 16:10 and 20:10 UTC.",
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
