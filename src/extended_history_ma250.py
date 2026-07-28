"""Extend standard MA250 2x to Binance spot inception with synthetic perp assumptions.

Real BTCUSDT perpetual/mark/funding data begins in September 2019. Before that:
  * Binance spot OHLC is used as the trade and mark-price proxy;
  * funding is charged every 8h at the mean real Binance funding rate;
  * liquidation uses spot lows as the mark-low proxy.

Outputs explicitly separate real-perp evidence from synthetic extension evidence.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics as M
from corrected_ma_regime_2x import BPY, load as load_perp, regime, simulate, stats

ROOT = Path(__file__).resolve().parent.parent
SPOT = ROOT / "data" / "BTCUSDT_4h_2017-08-17_2026-07-27.parquet"
FUND = ROOT / "data" / "BTCUSDT_funding.parquet"
PERP_START = pd.Timestamp("2019-09-08 16:00:00",tz="UTC")


def load_spot_proxy(pre_funding_multiplier=1.0):
    df=pd.read_parquet(SPOT).sort_index()
    df=df[~df.index.duplicated()].copy()
    df["mark_open"]=df["open"]
    df["mark_low"]=df["low"]
    df["mark_high"]=df["high"]
    funding=pd.read_parquet(FUND)["fundingRate"].sort_index()
    funding=funding[~funding.index.duplicated()]
    mean_rate=float(funding.mean())
    funding.index=funding.index.floor("4h")
    funding=funding.groupby(level=0).sum()
    df["funding"]=funding.reindex(df.index).fillna(0.0)
    pre=df.index<PERP_START
    settlement=(df.index.hour%8==0)
    df.loc[pre & settlement,"funding"]=mean_rate*pre_funding_multiplier
    return df,mean_rate


def rolling_table(ret):
    log=np.log1p(ret)
    rows=[]
    for years in (2,3,5,7):
        window=years*BPY
        roll=np.expm1(log.rolling(window,min_periods=window).sum()).dropna()*100
        if roll.empty:
            continue
        annualized=(np.power(1+roll/100,1/years)-1)*100
        rows.append((years,roll.iloc[-1],annualized.iloc[-1],roll.min(),
                     roll.median(),roll.max(),(roll>0).mean()*100))
    return rows


def show(label,result):
    s=stats(result)
    print(f"{label:<34}{s['net_%']:>11.1f}{s['cagr_%']:>10.2f}{s['sharpe']:>9.2f}"
          f"{s['maxdd_%']:>10.1f}{s['calmar']:>9.2f}{result.entries:>9}")


def main():
    spot,mean_rate=load_spot_proxy(1)
    perp=load_perp()
    print("# Extended-history MA250 2x")
    print(f"\nSpot proxy: {spot.index[0]} -> {spot.index[-1]} ({len(spot):,} bars)")
    print(f"Real perp:  {perp.index[0]} -> {perp.index[-1]} ({len(perp):,} bars)")
    print(f"Mean real funding: {mean_rate*100:.5f}% per 8h "
          f"(simple annual rate {mean_rate*3*365*100:.2f}%)")

    real=simulate(perp,regime(perp,250,0),leverage=2)
    proxy_overlap=spot.loc[PERP_START:]
    proxy_overlap_result=simulate(
        spot,regime(spot,250,0),PERP_START,spot.index[-1],leverage=2
    )
    print("\n## Overlap validation")
    print(f"{'model':<34}{'net%':>11}{'CAGR%':>10}{'Sharpe':>9}{'maxDD%':>10}"
          f"{'Calmar':>9}{'entries':>9}")
    print("-"*92)
    show("Real perpetual",real)
    show("Spot-price proxy",proxy_overlap_result)

    scenarios=[]
    for mult in (0,1,1.5,2):
        frame,_=load_spot_proxy(mult)
        result=simulate(frame,regime(frame,250,0),leverage=2)
        scenarios.append((mult,frame,result))
    print("\n## Full 2017-2026 synthetic-extension sensitivity")
    print(f"{'model':<34}{'net%':>11}{'CAGR%':>10}{'Sharpe':>9}{'maxDD%':>10}"
          f"{'Calmar':>9}{'entries':>9}")
    print("-"*92)
    for mult,_,result in scenarios:
        show(f"pre-2019 funding x{mult:g}",result)

    base_frame,base=scenarios[1][1],scenarios[1][2]
    print("\n## Year by year — base synthetic extension")
    print(f"{'year':<6}{'return%':>11}{'Sharpe':>9}{'maxDD%':>10}{'entries':>9}")
    active=base.position>0
    starts=active & ~active.shift(1,fill_value=False)
    for year in sorted(set(base.returns.index.year)):
        r=base.returns[base.returns.index.year==year]
        entries=int(((starts.index.year==year)&starts).sum())
        print(f"{year:<6}{M.total_return(r)*100:>11.1f}{M.sharpe(r,BPY):>9.2f}"
              f"{M.max_drawdown(r)*100:>10.1f}{entries:>9}")

    print("\n## Rolling returns — base synthetic extension")
    print(f"{'horizon':<9}{'latest total':>14}{'latest CAGR':>14}{'worst':>12}"
          f"{'median':>12}{'best':>12}{'positive':>11}")
    for years,latest,lcagr,worst,median,best,positive in rolling_table(base.returns):
        print(f"{years} years {latest:>13.1f}%{lcagr:>13.1f}%{worst:>11.1f}%"
              f"{median:>11.1f}%{best:>11.1f}%{positive:>10.1f}%")

    pre=simulate(base_frame,regime(base_frame,250,0),base_frame.index[0],
                 PERP_START-pd.Timedelta(hours=4),leverage=2)
    print("\n## Incremental pre-perpetual evidence")
    show("2017-08 to 2019-09 proxy",pre)

    print("\n## Extended-history leverage sensitivity")
    print(f"{'leverage':<12}{'net%':>11}{'CAGR%':>10}{'Sharpe':>9}{'maxDD%':>10}"
          f"{'Calmar':>9}{'entries':>9}")
    print("-"*70)
    for lev in (1,1.25,1.5,1.75,2):
        result=simulate(base_frame,regime(base_frame,250,0),leverage=lev)
        s=stats(result)
        print(f"{lev:<11.2f}x{s['net_%']:>11.1f}{s['cagr_%']:>10.2f}{s['sharpe']:>9.2f}"
              f"{s['maxdd_%']:>10.1f}{s['calmar']:>9.2f}{result.entries:>9}")


if __name__=="__main__":
    main()
