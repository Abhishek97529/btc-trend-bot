"""
Honest multi-family search for a BTCUSDT 4h strategy at fixed 2x leverage.

Protocol:
  DEV       2019-09 .. 2022-12: choose one configuration per family.
  VALIDATE  2023-01 .. 2024-12: choose among family finalists.
  HOLDOUT   2025-01 .. latest : one untouched final evaluation.

All simulations use the corrected stateful futures account. The holdout is never
used to tune or rank configurations.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M
from corrected_ma_regime_2x import BPY, load, regime, simulate, stats
from indicators import sma
from legacy_strategies import ema_crossover, momentum, donchian_breakout, rsi_mean_reversion


def ma_hysteresis(df, ma=250, entry_buffer=0.02, exit_buffer=0.02):
    """Enter above MA+buffer and exit below MA-buffer; hold state in between."""
    line = sma(df["close"], ma)
    state = pd.Series(index=df.index, dtype=float)
    state[df["close"] > line * (1 + entry_buffer)] = 1.0
    state[df["close"] < line * (1 - exit_buffer)] = 0.0
    return state.ffill().fillna(0.0).where(line.notna(), 0.0)


def candidates():
    out = []
    for ma, buf in itertools.product((150, 200, 250, 300, 350, 400), (0, .01, .02, .03)):
        out.append(("ma_regime", f"ma={ma},buffer={buf:.0%}", regime(df_global, ma, buf)))
    for ma, eb, xb in itertools.product((150, 200, 250, 300, 350, 400),
                                         (0, .02, .04), (0, .02, .04)):
        out.append(("ma_hysteresis", f"ma={ma},entry={eb:.0%},exit={xb:.0%}",
                    ma_hysteresis(df_global, ma, eb, xb)))
    for fast, slow in itertools.product((12, 24, 36, 48, 72), (96, 144, 168, 240, 336)):
        if fast < slow:
            out.append(("ema", f"fast={fast},slow={slow}",
                        ema_crossover(df_global, fast, slow)))
    for lb, trend in itertools.product((60, 90, 120, 180, 240), (150, 200, 300, 400)):
        out.append(("momentum", f"lookback={lb},trend={trend}",
                    momentum(df_global, lb, trend)))
    for entry, exit_n in itertools.product((60, 90, 120, 180, 240), (30, 60, 90, 120)):
        out.append(("donchian", f"entry={entry},exit={exit_n}",
                    donchian_breakout(df_global, entry, exit_n)))
    for n, lower, upper, trend in itertools.product((7, 14), (25, 30, 35),
                                                     (50, 55, 65), (150, 250, 350)):
        out.append(("rsi_reversion", f"n={n},lo={lower},hi={upper},trend={trend}",
                    rsi_mean_reversion(df_global, n, lower, upper, trend)))
    return out


def score(result):
    s = stats(result)
    # Reject ruin, excessively sparse samples, and training drawdowns beyond 75%.
    if result.liquidated or result.entries < 5 or s["maxdd_%"] < -75:
        return None
    return (round(s["sharpe"], 6), round(s["calmar"], 6), s)


def benchmark(signal, start, end):
    result = simulate(df_global, signal, start, end, leverage=2)
    return result, stats(result)


def line(label, s, result):
    return (f"{label:<46}{s['net_%']:>10.1f}{s['cagr_%']:>10.2f}{s['sharpe']:>9.2f}"
            f"{s['maxdd_%']:>10.1f}{s['calmar']:>9.2f}{result.entries:>8}")


def main():
    global df_global
    df_global = load()
    dev = (df_global.index[0], pd.Timestamp("2022-12-31 23:59:59", tz="UTC"))
    val = (pd.Timestamp("2023-01-01", tz="UTC"), pd.Timestamp("2024-12-31 23:59:59", tz="UTC"))
    hold = (pd.Timestamp("2025-01-01", tz="UTC"), df_global.index[-1])

    all_candidates = candidates()
    best_by_family = {}
    for family, config, signal in all_candidates:
        result = simulate(df_global, signal, *dev, leverage=2)
        ranked = score(result)
        if ranked is None:
            continue
        key = ranked[:2]
        if family not in best_by_family or key > best_by_family[family][0]:
            best_by_family[family] = (key, config, signal, result, ranked[2])

    finalists = []
    for family, (_, config, signal, dev_result, dev_stats) in best_by_family.items():
        val_result, val_stats = benchmark(signal, *val)
        finalists.append((val_stats["sharpe"], val_stats["calmar"], family, config, signal,
                          dev_result, dev_stats, val_result, val_stats))
    finalists.sort(reverse=True)
    winner = finalists[0]

    print(f"\nData {df_global.index[0]} -> {df_global.index[-1]} | {len(df_global)} bars")
    print(f"Candidates={len(all_candidates)}; fixed leverage=2x; corrected stateful engine")
    print("\nFamily finalists: tuned on DEV, ranked only on VALIDATE")
    print(f"{'family/config':<46}{'net %':>10}{'CAGR %':>10}{'Sharpe':>9}"
          f"{'maxDD %':>10}{'Calmar':>9}{'entries':>8}")
    print("-" * 102)
    for _, _, family, config, _, _, _, vr, vs in finalists:
        print(line(f"{family}({config})", vs, vr))

    _, _, family, config, signal, dev_result, dev_stats, val_result, val_stats = winner
    hold_result, hold_stats = benchmark(signal, *hold)
    ma_signal = regime(df_global, 250, 0)
    ma_dev, ma_dev_s = benchmark(ma_signal, *dev)
    ma_val, ma_val_s = benchmark(ma_signal, *val)
    ma_hold, ma_hold_s = benchmark(ma_signal, *hold)
    bh = df_global["close"].pct_change().fillna(0.0)

    print(f"\nSELECTED BEFORE HOLDOUT: {family}({config}) x2")
    print("\nSegment results")
    print(f"{'strategy/segment':<46}{'net %':>10}{'CAGR %':>10}{'Sharpe':>9}"
          f"{'maxDD %':>10}{'Calmar':>9}{'entries':>8}")
    print("-" * 102)
    print(line("winner DEV 2019-2022", dev_stats, dev_result))
    print(line("winner VALIDATE 2023-2024", val_stats, val_result))
    print(line("winner HOLDOUT 2025-latest", hold_stats, hold_result))
    print(line("MA250 HOLDOUT 2025-latest", ma_hold_s, ma_hold))

    hold_bh = bh.loc[hold[0]:hold[1]]
    print(f"{'B&H HOLDOUT 2025-latest':<46}{M.total_return(hold_bh)*100:>10.1f}"
          f"{M.cagr(hold_bh, BPY)*100:>10.2f}{M.sharpe(hold_bh, BPY):>9.2f}"
          f"{M.max_drawdown(hold_bh)*100:>10.1f}{M.calmar(hold_bh, BPY):>9.2f}{0:>8}")

    print("\nDiagnostic only — all preselected finalists on HOLDOUT (not used for selection)")
    print(f"{'family/config':<46}{'net %':>10}{'CAGR %':>10}{'Sharpe':>9}"
          f"{'maxDD %':>10}{'Calmar':>9}{'entries':>8}")
    print("-" * 102)
    for _, _, fam, cfg, sig, *_ in finalists:
        hr, hs = benchmark(sig, *hold)
        print(line(f"{fam}({cfg})", hs, hr))

    print("\nDecision:")
    if hold_stats["sharpe"] > ma_hold_s["sharpe"] and hold_stats["cagr_%"] > ma_hold_s["cagr_%"]:
        print("  The selected strategy beat MA250 on untouched CAGR and Sharpe.")
    else:
        print("  It did NOT beat MA250 on both untouched CAGR and Sharpe; keep MA250 as benchmark.")


df_global: pd.DataFrame
if __name__ == "__main__":
    main()
