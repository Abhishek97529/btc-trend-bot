"""
Develop and validate a volatility-controlled version of frozen MA250.

Selection protocol:
  DEV       2019-09 through 2022-12: reject fragile/ruined configurations.
  VALIDATE  2023-01 through 2024-12: rank survivors by Sharpe, then Calmar.
  DIAGNOSTIC 2025-01 onward: never used for ranking.

The simulator charges actual turnover whenever dynamic leverage is rebalanced.
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M
from corrected_ma_regime_2x import AccountResult, BPY, FEE, SLIPPAGE, MM, load, regime, simulate, stats


def volatility_leverage(df, window, target_vol, cap):
    realized = df["close"].pct_change().rolling(window, min_periods=window).std() * np.sqrt(BPY)
    return (target_vol / realized.replace(0, np.nan)).clip(0.25, cap).fillna(0.0)


def simulate_dynamic(
    df, signal, leverage_series, start=None, end=None, rebalance_bars=6,
    fee=FEE, slippage=SLIPPAGE, funding_multiplier=1.0, mm=MM,
    mark_shock=0.0,
):
    use = df.loc[start:end] if start is not None or end is not None else df
    wallet, qty, entry = 1.0, 0.0, 0.0
    previous_equity = 1.0
    entries = exits = 0
    fees_paid = funding_paid = 0.0
    liquidated = False
    liquidation_time = None
    out_ret, out_eq, out_pos = [], [], []
    full_index = df.index

    for j, (ts, row) in enumerate(use.iterrows()):
        loc = full_index.get_loc(ts)
        desired_on = loc > 0 and float(signal.iloc[loc - 1]) > 0.5
        desired_lev = float(leverage_series.iloc[loc - 1]) if desired_on else 0.0
        open_px = float(row["open"])
        mark_open = row.get("mark_open", open_px)
        mark_low = row.get("mark_low", row["low"])
        mark_open = open_px if pd.isna(mark_open) else float(mark_open)
        mark_low = float(row["low"]) if pd.isna(mark_low) else float(mark_low)
        if mark_shock > 0:
            mark_low = min(mark_low, mark_open * (1.0 - mark_shock))

        equity_open = wallet if qty == 0 else wallet + qty * (open_px - entry)
        # Anchor scheduled rebalances to the global candle grid so results do
        # not depend on where an evaluation window happens to begin.
        must_rebalance = ((qty == 0 and desired_on) or (qty != 0 and not desired_on)
                          or (qty != 0 and desired_on and loc % rebalance_bars == 0))
        if must_rebalance:
            was_on = qty != 0
            target_qty = 0.0 if not desired_on else desired_lev * equity_open / open_px
            turnover_notional = abs(target_qty - qty) * open_px
            charge = turnover_notional * (fee + slippage)
            # Mark the existing contract to market, then establish the new quantity.
            wallet = equity_open - charge
            fees_paid += charge
            qty = target_qty
            entry = open_px if qty != 0 else 0.0
            if not was_on and qty != 0:
                entries += 1
            elif was_on and qty == 0:
                exits += 1

        if qty != 0:
            payment = qty * mark_open * float(row["funding"]) * funding_multiplier
            wallet -= payment
            funding_paid += payment
            low_equity = wallet + qty * (mark_low - entry)
            maintenance = mm * abs(qty) * mark_low
            if low_equity <= maintenance:
                wallet = qty = entry = 0.0
                liquidated = True
                liquidation_time = ts

        close_px = float(row["close"])
        equity = wallet if qty == 0 else wallet + qty * (close_px - entry)
        equity = max(equity, 0.0)
        out_ret.append(equity / previous_equity - 1 if previous_equity > 0 else 0.0)
        out_eq.append(equity)
        out_pos.append(0.0 if qty == 0 or equity == 0 else qty * close_px / equity)
        previous_equity = equity
        if liquidated:
            remaining = len(use) - len(out_ret)
            out_ret.extend([0.0] * remaining)
            out_eq.extend([0.0] * remaining)
            out_pos.extend([0.0] * remaining)
            break

    idx = use.index
    return AccountResult(
        pd.Series(out_ret,index=idx), pd.Series(out_eq,index=idx),
        pd.Series(out_pos,index=idx), entries, exits, liquidated,
        liquidation_time, fees_paid, funding_paid,
    )


GRID = list(itertools.product(
    (30, 60, 90),       # realized-vol window
    (.40, .50, .60),    # annual target volatility
    (1.50, 1.75, 2.00), # maximum leverage
    (6, 42),             # rebalance daily or weekly
))


def result_row(name, result):
    s = stats(result)
    return (f"{name:<38}{s['net_%']:>10.1f}{s['cagr_%']:>10.2f}{s['sharpe']:>9.2f}"
            f"{s['maxdd_%']:>10.1f}{s['calmar']:>9.2f}{result.entries:>8}")


def main():
    df = load()
    signal = regime(df,250,0)
    dev = (df.index[0], pd.Timestamp("2022-12-31 23:59:59",tz="UTC"))
    val = (pd.Timestamp("2023-01-01",tz="UTC"), pd.Timestamp("2024-12-31 23:59:59",tz="UTC"))
    diag = (pd.Timestamp("2025-01-01",tz="UTC"), df.index[-1])

    survivors = []
    for window,target,cap,rebal in GRID:
        lev = volatility_leverage(df,window,target,cap)
        dr = simulate_dynamic(df,signal,lev,*dev,rebalance_bars=rebal)
        ds = stats(dr)
        if dr.liquidated or ds["sharpe"] <= 0 or ds["maxdd_%"] < -70:
            continue
        vr = simulate_dynamic(df,signal,lev,*val,rebalance_bars=rebal)
        vs = stats(vr)
        config = (window,target,cap,rebal)
        survivors.append((round(vs["sharpe"],6),round(vs["calmar"],6),config,dr,ds,vr,vs,lev))
    survivors.sort(reverse=True,key=lambda x:(x[0],x[1]))
    winner = survivors[0]
    _,_,(window,target,cap,rebal),dr,ds,vr,vs,lev = winner
    hr = simulate_dynamic(df,signal,lev,*diag,rebalance_bars=rebal)
    hs = stats(hr)
    full = simulate_dynamic(df,signal,lev,rebalance_bars=rebal)
    fs = stats(full)

    standard_full = simulate(df,signal,leverage=2)
    standard_diag = simulate(df,signal,*diag,leverage=2)

    print("# MA250 volatility-control research")
    print(f"\nGrid: {len(GRID)} predeclared configs; survivors: {len(survivors)}")
    print("Selected using DEV constraints and VALIDATE Sharpe/Calmar only.")
    print(f"\nWINNER: window={window} bars, target={target:.0%}, cap={cap:g}x, "
          f"rebalance={'daily' if rebal==6 else 'weekly'}")
    print(f"\n{'strategy/segment':<38}{'net%':>10}{'CAGR%':>10}{'Sharpe':>9}"
          f"{'maxDD%':>10}{'Calmar':>9}{'entries':>8}")
    print("-"*94)
    print(result_row("candidate DEV",dr))
    print(result_row("candidate VALIDATE",vr))
    print(result_row("candidate 2025+ diagnostic",hr))
    print(result_row("candidate FULL",full))
    print(result_row("standard MA250 FULL",standard_full))
    print(result_row("standard MA250 2025+",standard_diag))

    print("\nTop 10 validation-ranked configurations")
    for _,_,cfg,_,_,r,s,_ in survivors[:10]:
        w,t,c,rb=cfg
        print(result_row(f"w{w} t{t:.0%} cap{c:g} rb{rb}",r))

    print("\nStress tests for selected candidate (full history)")
    stresses = [
        ("base", dict()),
        ("cost 0.15%/side", dict(fee=.0015,slippage=0)),
        ("funding x1.5", dict(funding_multiplier=1.5)),
        ("funding x2", dict(funding_multiplier=2)),
    ]
    for name,kwargs in stresses:
        r=simulate_dynamic(df,signal,lev,rebalance_bars=rebal,**kwargs)
        print(result_row(name,r))

    print("\nYear by year: candidate vs standard")
    print(f"{'year':<6}{'candidate':>12}{'cand DD':>10}{'standard':>12}{'std DD':>10}")
    for year in sorted(set(df.index.year)):
        cr=full.returns[full.returns.index.year==year]
        sr=standard_full.returns[standard_full.returns.index.year==year]
        print(f"{year:<6}{M.total_return(cr)*100:>12.1f}{M.max_drawdown(cr)*100:>10.1f}"
              f"{M.total_return(sr)*100:>12.1f}{M.max_drawdown(sr)*100:>10.1f}")

    print("\nExecution-delay stress")
    for delay in (0,1,2,3):
        delayed = signal.shift(delay).fillna(0)
        r = simulate_dynamic(df,delayed,lev,rebalance_bars=rebal)
        print(result_row(f"additional delay {delay} bars",r))

    print("\nShock stress")
    for shock in (.20,.30,.40,.50):
        r = simulate_dynamic(df,signal,lev,rebalance_bars=rebal,mark_shock=shock)
        print(result_row(f"{shock:.0%} adverse mark shock",r))

    # Fixed-rule reset schedules: start after the named warmup and reset flat
    # every test fold, paying a fresh entry cost.
    print("\nFixed-candidate OOS/reset schedules")
    for train_months,test_months in ((12,3),(18,6),(24,6),(36,12)):
        trbars=int(train_months/12*BPY); tebars=int(test_months/12*BPY)
        cursor=trbars; parts=[]
        while cursor < len(df):
            end=min(cursor+tebars,len(df))
            r=simulate_dynamic(df,signal,lev,df.index[cursor],df.index[end-1],
                               rebalance_bars=rebal)
            parts.append(r.returns); cursor=end
        rr=pd.concat(parts)
        print(f"{train_months:>2}m/{test_months:>2}m  CAGR {M.cagr(rr,BPY)*100:>7.2f}%  "
              f"Sharpe {M.sharpe(rr,BPY):>5.2f}  DD {M.max_drawdown(rr)*100:>6.1f}%")

    # Same one-week block-bootstrap method used for standard MA250.
    from validate_ma250_prepaper import block_bootstrap
    ct,cd=block_bootstrap(full.returns)
    st,sd=block_bootstrap(standard_full.returns)
    print("\nBlock bootstrap comparison (2,000 one-week-block paths)")
    print(f"{'strategy':<24}{'P(loss)':>10}{'median DD':>12}{'P(DD<-50)':>12}{'P(DD<-70)':>12}")
    for name,t,d in (("vol-controlled MA250",ct,cd),("standard MA250",st,sd)):
        print(f"{name:<24}{(t<0).mean()*100:>9.1f}%{np.median(d)*100:>11.1f}%"
              f"{(d<-.50).mean()*100:>11.1f}%{(d<-.70).mean()*100:>11.1f}%")


if __name__ == "__main__":
    main()
