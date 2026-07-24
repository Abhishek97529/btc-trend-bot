# Going LIVE on CoinDCX

Paper trading, the signal, and Telegram all run fine on GitHub Actions + Cloudflare
(they use Binance/Coinbase data). **Live CoinDCX orders are different** — read the
constraint below before wiring real money.

---

## 0. The hard constraint: WHERE the live bot can run

CoinDCX will almost certainly **block live API calls from GitHub-hosted runners**, for
two independent reasons:

1. **India-only policy.** CoinDCX API access is for Indian citizens/entities and the
   exchange geo-restricts non-India access.
2. **Datacenter-ASN blocking (the bigger one).** CoinDCX sits behind Cloudflare, which
   blocks AWS/Azure/GCP "hosting" IP ranges at the network layer — *regardless of
   country*. GitHub-hosted runners use Azure datacenter IPs, so they'd be blocked
   **even if they were in India**. (Symptom: HTTP 403 / Cloudflare Error 1020.)

**Therefore the live bot must run from a residential India IP — not any datacenter.**

| Where to run live | Works? | Notes |
|---|---|---|
| GitHub-hosted runner (default) | ❌ | Azure datacenter IP → blocked |
| Home device on India broadband | ✅ | Residential IP → passes geo + ASN checks. **Recommended.** |
| India-region cloud (AWS Mumbai / VPS) | ⚠️ | Still a datacenter ASN — may STILL hit Error 1020. Test first, don't assume. |
| Corporate laptop | ❌ | Corporate proxy blocks CoinDCX; also leaks into corporate network. Never. |

Paper mode is unaffected everywhere (it uses Binance/Coinbase).

---

## 1. Prove the IP works (read-only, places NO orders)

On the machine you intend to run live from (a home device in India), with your
CoinDCX API key/secret:

```bash
COINDCX_KEY=...  COINDCX_SECRET=...  python bot/coindcx.py
```

- ✅ Prints the BTC price + your balances → that IP can trade. Continue.
- ❌ 403 / Cloudflare 1020 → that IP is blocked. Use a genuine residential India
  connection (home broadband). Do not proceed until this passes.

---

## 2. Get CoinDCX API keys

1. Complete KYC on CoinDCX → **Profile → API Dashboard** → create a key.
2. Enable **spot trading** permission.
3. Optional but recommended: **"Bind IP Address to API key"** → bind it to your home
   IP. (Home IPs are often dynamic — if yours changes you'll re-bind. A static-IP
   broadband plan avoids this.)
4. Keep the **key + secret** safe. They go in GitHub repo *secrets* (below), never in
   code or chat.

---

## 3. Run the daily live bot from your India box

Use a **self-hosted GitHub Actions runner** so your existing `.github/workflows/bot.yml`
runs unchanged — it just executes on your box instead of GitHub's:

1. GitHub repo → **Settings → Actions → Runners → New self-hosted runner** → follow the
   Linux/Windows steps on your home device. Leave it running (install it as a service so
   it survives reboots).
2. In `bot.yml`, change `runs-on: ubuntu-latest` → `runs-on: self-hosted` (only when you
   want the daily run to execute live from home).

**Simpler alternative (no runner):** a local scheduled task on the home box —
`python bot/paper_bot.py run --live` once daily a few minutes after 00:00 UTC
(05:30 AM IST). See the SCHEDULING notes at the bottom of `bot/paper_bot.py`.

---

## 4. Secrets & variables (GitHub repo → Settings → Secrets and variables → Actions)

**Secrets:**

| Name | Value |
|---|---|
| `COINDCX_KEY` | your API key |
| `COINDCX_SECRET` | your API secret |
| `COINDCX_LIVE_ARMED` | `1` — the **second factor**. Without this, the bot stays paper even if the Telegram flag is ON. |

**Variables (optional):**

| Name | Default | Meaning |
|---|---|---|
| `COINDCX_MARKET` | `BTCINR` | trading pair. Use `BTCUSDT` if you fund in USDT. |
| `COINDCX_MAX_ORDER_QUOTE` | `1000` | hard per-order cap in the quote currency. Start SMALL. |

---

## 5. The Telegram `/live` toggle

Once secrets are set and the box is running:

- `/live` — show current mode (anyone in the allow-list can read)
- `/live on CONFIRM` — arm live (**owner only**)
- `/live off` — back to paper instantly

**Two factors must BOTH be true to trade real money:**
1. Telegram flag `live:true` (set via `/live on CONFIRM`)
2. `COINDCX_LIVE_ARMED=1` secret present

So a leaked bot token alone can't start trading. Every order is also capped by
`COINDCX_MAX_ORDER_QUOTE`, and blocked entirely unless `paper_bot.py` was invoked with
`--live` (which `bot.yml` only does when both factors are true).

> The `/live on` command needs the Cloudflare Worker's GitHub token upgraded from
> **read-only** to **Contents: Read AND write** (it commits `bot/live_flag.json`).
> Otherwise `/live on` replies with a GitHub error.

---

## 6. Rollout ladder — do NOT skip

1. **Paper track record first.** Let the bot run a few real ENTER/EXIT cycles on paper.
   It's never actually traded yet (sits flat/HOLD until the signal fires).
2. **Read-only smoke test** from the India box (§1) passes.
3. **One tiny live round-trip.** `COINDCX_MAX_ORDER_QUOTE=1000` (or less). Confirm a real
   ENTER and a real EXIT execute and reconcile against your CoinDCX balances.
4. **Scale** only after you've seen a full round-trip work end to end.

Reality check on returns: after Indian tax the strategy modelled ~**32% CAGR** (vs
~25% after-tax buy & hold) — and live fees (~0.4–0.5%/side vs the 0.10% backtested) +
real slippage will shave that further. Size expectations accordingly.
