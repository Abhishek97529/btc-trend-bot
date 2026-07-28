"""Corrected long/short tests around the frozen 4h MA250 BTC strategy."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parent))
import metrics as M
from corrected_ma_regime_2x import AccountResult, BPY, FEE, SLIPPAGE, MM, load, regime
from extended_history_ma250 import load_spot_proxy
from indicators import sma


def simulate_ls(df,target,leverage,start=None,end=None,fee=FEE,slippage=SLIPPAGE,
                funding_multiplier=1.0,long_mark_shock=0.0,short_mark_shock=0.0):
    use=df.loc[start:end] if start is not None or end is not None else df
    wallet,qty,entry=1.0,0.0,0.0
    prev_eq=1.0
    entries=exits=0
    fees=funding_paid=0.0
    liquidated=False; liq_time=None
    rr,ee,pp=[],[],[]
    full=df.index
    prior_direction=0
    for ts,row in use.iterrows():
        loc=full.get_loc(ts)
        direction=0 if loc==0 else int(np.sign(target.iloc[loc-1]))
        lev=0.0 if direction==0 else float(leverage.iloc[loc-1])
        op=float(row["open"]); close=float(row["close"])
        mo=row.get("mark_open",op); ml=row.get("mark_low",row["low"]); mh=row.get("mark_high",row["high"])
        mo=op if pd.isna(mo) else float(mo)
        ml=float(row["low"]) if pd.isna(ml) else float(ml)
        mh=float(row["high"]) if pd.isna(mh) else float(mh)
        if qty>0 and long_mark_shock>0:
            ml=min(ml,mo*(1-long_mark_shock))
        if qty<0 and short_mark_shock>0:
            mh=max(mh,mo*(1+short_mark_shock))

        if direction != prior_direction:
            equity_open=wallet if qty==0 else wallet+qty*(op-entry)
            target_qty=0.0 if direction==0 else direction*lev*equity_open/op
            charge=abs(target_qty-qty)*op*(fee+slippage)
            wallet=equity_open-charge; fees+=charge
            if prior_direction==0 and direction!=0: entries+=1
            elif prior_direction!=0 and direction==0: exits+=1
            elif prior_direction!=direction: exits+=1; entries+=1
            qty=target_qty; entry=op if qty!=0 else 0.0
            prior_direction=direction

        if qty!=0:
            payment=qty*mo*float(row["funding"])*funding_multiplier
            wallet-=payment; funding_paid+=payment
            adverse=ml if qty>0 else mh
            adverse_equity=wallet+qty*(adverse-entry)
            maintenance=MM*abs(qty)*adverse
            if adverse_equity<=maintenance:
                wallet=qty=entry=0.0
                liquidated=True; liq_time=ts

        equity=wallet if qty==0 else wallet+qty*(close-entry)
        equity=max(equity,0.0)
        rr.append(equity/prev_eq-1 if prev_eq>0 else 0.0)
        ee.append(equity)
        pp.append(0.0 if qty==0 or equity==0 else qty*close/equity)
        prev_eq=equity
        if liquidated:
            remain=len(use)-len(rr)
            rr.extend([0.0]*remain);ee.extend([0.0]*remain);pp.extend([0.0]*remain)
            break
    idx=use.index
    return AccountResult(pd.Series(rr,index=idx),pd.Series(ee,index=idx),
                         pd.Series(pp,index=idx),entries,exits,liquidated,liq_time,
                         fees,funding_paid)


def signals(df):
    c=df["close"]; line=sma(c,250)
    above=c>line; below=c<line
    slope=line>line.shift(30)
    mom=c.pct_change(90)
    sig={}
    sig["long_flat_2x"]=(above.astype(float),pd.Series(2.0,index=df.index))
    sig["symmetric_2x_2x"]=(pd.Series(np.where(above,1,np.where(below,-1,0)),index=df.index),
                            pd.Series(2.0,index=df.index))
    reduced_target=pd.Series(np.where(above,1,np.where(below,-1,0)),index=df.index)
    reduced_lev=pd.Series(np.where(reduced_target>0,2.0,np.where(reduced_target<0,1.0,0.0)),index=df.index)
    sig["reduced_short_2x_1x"]=(reduced_target,reduced_lev)
    buffered=pd.Series(np.where(c>line*1.02,1,np.where(c<line*.98,-1,0)),index=df.index)
    buffered_lev=pd.Series(np.where(buffered>0,2.0,np.where(buffered<0,1.0,0.0)),index=df.index)
    sig["buffered_2x_1x"]=(buffered,buffered_lev)
    confirmed_short=below & (~slope) & (mom<0)
    confirmed=pd.Series(np.where(above,1,np.where(confirmed_short,-1,0)),index=df.index)
    confirmed_lev=pd.Series(np.where(confirmed>0,2.0,np.where(confirmed<0,1.0,0.0)),index=df.index)
    sig["confirmed_short_2x_1x"]=(confirmed,confirmed_lev)
    return sig


def stat(result):
    r=result.returns
    return dict(net=M.total_return(r)*100,cagr=M.cagr(r,BPY)*100,
                sharpe=M.sharpe(r,BPY),dd=M.max_drawdown(r)*100,
                calmar=M.calmar(r,BPY))


def show(label,result):
    s=stat(result)
    print(f"{label:<28}{s['net']:>11.1f}{s['cagr']:>10.2f}{s['sharpe']:>9.2f}"
          f"{s['dd']:>10.1f}{s['calmar']:>9.2f}{result.entries:>8}"
          f"{str(result.liquidated):>7}{str(result.liquidation_time or '-'):>27}")


def run_set(df,title):
    print(f"\n## {title}")
    print(f"{'strategy':<28}{'net%':>11}{'CAGR%':>10}{'Sharpe':>9}{'maxDD%':>10}"
          f"{'Calmar':>9}{'entries':>8}{'liq':>7}{'liq time':>27}")
    print("-"*119)
    results={}
    for name,(target,lev) in signals(df).items():
        result=simulate_ls(df,target,lev)
        results[name]=result;show(name,result)
    return results


def main():
    perp=load()
    proxy,_=load_spot_proxy(1)
    real=run_set(perp,"Real Binance perpetual 2019-2026")
    extended=run_set(proxy,"Extended Binance spot proxy 2017-2026")

    name="confirmed_short_2x_1x"
    target,lev=signals(perp)[name]
    long_only=simulate_ls(perp,target.where(target>0,0),lev.where(target>0,0))
    short_only=simulate_ls(perp,target.where(target<0,0),lev.where(target<0,0))
    print("\n## Confirmed variant direction attribution — real perp")
    show("long side only",long_only)
    show("short side only",short_only)
    show("combined",real[name])

    print("\n## Confirmed variant year by year — real perp")
    print(f"{'year':<6}{'combined%':>12}{'long only%':>12}{'short only%':>13}")
    for year in sorted(set(perp.index.year)):
        vals=[]
        for result in (real[name],long_only,short_only):
            r=result.returns[result.returns.index.year==year]
            vals.append(M.total_return(r)*100)
        print(f"{year:<6}{vals[0]:>12.1f}{vals[1]:>12.1f}{vals[2]:>13.1f}")

    reduced_name="reduced_short_2x_1x"
    rt,rl=signals(perp)[reduced_name]
    reduced_short_only=simulate_ls(perp,rt.where(rt<0,0),rl.where(rt<0,0))
    print("\n## Reduced-short variant attribution and predetermined periods")
    show("reduced short side only",reduced_short_only)
    periods=[
        ("DEV 2019-2022",perp.index[0],pd.Timestamp("2022-12-31 23:59:59",tz="UTC")),
        ("VALIDATE 2023-2024",pd.Timestamp("2023-01-01",tz="UTC"),pd.Timestamp("2024-12-31 23:59:59",tz="UTC")),
        ("2025+ diagnostic",pd.Timestamp("2025-01-01",tz="UTC"),perp.index[-1]),
    ]
    base_t,base_l=signals(perp)["long_flat_2x"]
    for label,lo,hi in periods:
        print(f"\n{label}")
        show("long/flat baseline",simulate_ls(perp,base_t,base_l,lo,hi))
        show("long2x/short1x",simulate_ls(perp,rt,rl,lo,hi))

    print("\n## Reduced-short year by year — real perp")
    print(f"{'year':<6}{'combined%':>12}{'baseline%':>12}{'short only%':>13}")
    for year in sorted(set(perp.index.year)):
        vals=[]
        for result in (real[reduced_name],real["long_flat_2x"],reduced_short_only):
            r=result.returns[result.returns.index.year==year]
            vals.append(M.total_return(r)*100)
        print(f"{year:<6}{vals[0]:>12.1f}{vals[1]:>12.1f}{vals[2]:>13.1f}")


if __name__=="__main__":
    main()
