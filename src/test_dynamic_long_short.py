"""Develop and validate volatility-sized MA250 long/short BTC 4h strategies."""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parent))
import metrics as M
from corrected_ma_regime_2x import AccountResult, BPY, FEE, SLIPPAGE, MM, load
from extended_history_ma250 import load_spot_proxy
from indicators import sma
from test_ma250_long_short import simulate_ls, stat
from validate_ma250_prepaper import block_bootstrap
from validate_reduced_short import make_rule


def direction_signal(df):
    line=sma(df["close"],250)
    return pd.Series(np.where(df["close"]>line,1,np.where(df["close"]<line,-1,0)),
                     index=df.index)


def sized_leverage(df,direction,window,target_vol,long_cap,short_cap):
    vol=df["close"].pct_change().rolling(window,min_periods=window).std()*np.sqrt(BPY)
    raw=(target_vol/vol.replace(0,np.nan)).clip(lower=.25).fillna(0)
    cap=pd.Series(np.where(direction>0,long_cap,np.where(direction<0,short_cap,0.0)),
                  index=df.index)
    return pd.concat([raw,cap],axis=1).min(axis=1).where(direction!=0,0.0)


def simulate_dynamic_ls(df,direction,leverage,start=None,end=None,rebalance_bars=42,
                        fee=FEE,slippage=SLIPPAGE,funding_multiplier=1.0,
                        long_mark_shock=0.0,short_mark_shock=0.0):
    use=df.loc[start:end] if start is not None or end is not None else df
    wallet,qty,entry=1.0,0.0,0.0
    prev_eq=1.0;prior_dir=0
    entries=exits=0;fees=funding_paid=0.0
    liquidated=False;liq_time=None
    rr,ee,pp=[],[],[]
    full=df.index
    for ts,row in use.iterrows():
        loc=full.get_loc(ts)
        desired=0 if loc==0 else int(np.sign(direction.iloc[loc-1]))
        lev=0.0 if desired==0 else float(leverage.iloc[loc-1])
        op=float(row["open"]);close=float(row["close"])
        mo=row.get("mark_open",op);ml=row.get("mark_low",row["low"]);mh=row.get("mark_high",row["high"])
        mo=op if pd.isna(mo) else float(mo)
        ml=float(row["low"]) if pd.isna(ml) else float(ml)
        mh=float(row["high"]) if pd.isna(mh) else float(mh)
        if qty>0 and long_mark_shock: ml=min(ml,mo*(1-long_mark_shock))
        if qty<0 and short_mark_shock: mh=max(mh,mo*(1+short_mark_shock))

        scheduled=qty!=0 and desired==prior_dir and loc%rebalance_bars==0
        change=desired!=prior_dir
        if change or scheduled:
            equity_open=wallet if qty==0 else wallet+qty*(op-entry)
            target_qty=0.0 if desired==0 else desired*lev*equity_open/op
            charge=abs(target_qty-qty)*op*(fee+slippage)
            wallet=equity_open-charge;fees+=charge
            if change:
                if prior_dir!=0: exits+=1
                if desired!=0: entries+=1
            qty=target_qty;entry=op if qty!=0 else 0.0;prior_dir=desired

        if qty!=0:
            payment=qty*mo*float(row["funding"])*funding_multiplier
            wallet-=payment;funding_paid+=payment
            adverse=ml if qty>0 else mh
            adverse_equity=wallet+qty*(adverse-entry)
            if adverse_equity<=MM*abs(qty)*adverse:
                wallet=qty=entry=0.0;liquidated=True;liq_time=ts
        equity=wallet if qty==0 else wallet+qty*(close-entry)
        equity=max(equity,0.0)
        rr.append(equity/prev_eq-1 if prev_eq>0 else 0.0)
        ee.append(equity);pp.append(0 if qty==0 or equity==0 else qty*close/equity)
        prev_eq=equity
        if liquidated:
            n=len(use)-len(rr);rr.extend([0.0]*n);ee.extend([0.0]*n);pp.extend([0.0]*n);break
    idx=use.index
    return AccountResult(pd.Series(rr,index=idx),pd.Series(ee,index=idx),
                         pd.Series(pp,index=idx),entries,exits,liquidated,liq_time,
                         fees,funding_paid)


GRID=list(itertools.product(
    (30,60,90),(.30,.40,.50),(1.5,2.0),(.25,.50,.75),(6,42)
))


def show(label,result):
    s=stat(result)
    print(f"{label:<42}{s['net']:>11.1f}{s['cagr']:>10.2f}{s['sharpe']:>9.2f}"
          f"{s['dd']:>10.1f}{s['calmar']:>9.2f}{result.entries:>9}")


def main():
    df=load();direction=direction_signal(df)
    dev=(df.index[0],pd.Timestamp("2022-12-31 23:59:59",tz="UTC"))
    val=(pd.Timestamp("2023-01-01",tz="UTC"),pd.Timestamp("2024-12-31 23:59:59",tz="UTC"))
    hold=(pd.Timestamp("2025-01-01",tz="UTC"),df.index[-1])
    ranked=[]
    for w,t,lc,sc,rb in GRID:
        lev=sized_leverage(df,direction,w,t,lc,sc)
        dr=simulate_dynamic_ls(df,direction,lev,*dev,rebalance_bars=rb)
        ds=stat(dr)
        if dr.liquidated or ds["sharpe"]<=0 or ds["dd"] < -70: continue
        vr=simulate_dynamic_ls(df,direction,lev,*val,rebalance_bars=rb)
        vs=stat(vr)
        ranked.append((round(vs["sharpe"],6),round(vs["calmar"],6),
                       (w,t,lc,sc,rb),lev,dr,vr))
    ranked.sort(reverse=True,key=lambda x:(x[0],x[1]))
    _,_,cfg,lev,dr,vr=ranked[0]
    w,t,lc,sc,rb=cfg
    hr=simulate_dynamic_ls(df,direction,lev,*hold,rebalance_bars=rb)
    full=simulate_dynamic_ls(df,direction,lev,rebalance_bars=rb)

    fixed_t,fixed_l=make_rule(df,250,.5)
    fixed=simulate_ls(df,fixed_t,fixed_l)
    longflat_t,longflat_l=make_rule(df,250,0)
    longflat=simulate_ls(df,longflat_t,longflat_l)

    print("# Dynamic MA250 long/short sizing")
    print(f"\nGrid {len(GRID)} configs; survivors {len(ranked)}")
    print(f"WINNER selected before diagnostic: window={w}, target={t:.0%}, "
          f"long cap={lc:g}x, short cap={sc:g}x, rebalance={rb} bars")
    print(f"\n{'strategy/period':<42}{'net%':>11}{'CAGR%':>10}{'Sharpe':>9}"
          f"{'maxDD%':>10}{'Calmar':>9}{'entries':>9}")
    print("-"*100)
    show("dynamic DEV",dr);show("dynamic VALIDATE",vr);show("dynamic 2025+",hr)
    show("dynamic FULL",full);show("fixed long2 / short0.5",fixed)
    show("fixed long2 / flat",longflat)

    print("\nTop validation configurations")
    for _,_,c,_,_,r in ranked[:10]:
        ww,tt,ll,ss,rbb=c
        show(f"w{ww} t{tt:.0%} L{ll:g} S{ss:g} rb{rbb}",r)

    proxy,_=load_spot_proxy(1);pdirection=direction_signal(proxy)
    plev=sized_leverage(proxy,pdirection,w,t,lc,sc)
    extended=simulate_dynamic_ls(proxy,pdirection,plev,rebalance_bars=rb)
    print("\nExtended history")
    show("dynamic 2017+",extended)

    print("\nYear by year: dynamic vs fixed short0.5")
    print(f"{'year':<6}{'dynamic%':>12}{'dyn DD':>10}{'fixed%':>12}{'fix DD':>10}")
    for year in sorted(set(df.index.year)):
        a=full.returns[full.returns.index.year==year]
        b=fixed.returns[fixed.returns.index.year==year]
        print(f"{year:<6}{M.total_return(a)*100:>12.1f}{M.max_drawdown(a)*100:>10.1f}"
              f"{M.total_return(b)*100:>12.1f}{M.max_drawdown(b)*100:>10.1f}")

    print("\nStress tests")
    stresses=[
        ("base",{}),("delay 1 bar",{"delay":1}),
        ("cost 0.15%/side",{"fee":.0015,"slippage":0}),
        ("funding x1.5",{"funding_multiplier":1.5}),
        ("funding x2",{"funding_multiplier":2}),
    ]
    for name,kw in stresses:
        delay=kw.pop("delay",0)
        d=direction.shift(delay).fillna(0);lv=lev.shift(delay).fillna(0)
        show(name,simulate_dynamic_ls(df,d,lv,rebalance_bars=rb,**kw))

    print("\nFixed reset schedules")
    fold_values=[]
    for tr,te in ((12,3),(18,6),(24,6),(36,12)):
        trb=int(tr/12*BPY);teb=int(te/12*BPY);cur=trb;parts=[]
        while cur<len(df):
            end=min(cur+teb,len(df))
            r=simulate_dynamic_ls(df,direction,lev,df.index[cur],df.index[end-1],
                                  rebalance_bars=rb)
            parts.append(r.returns);cur=end
        rr=pd.concat(parts)
        vals=(M.cagr(rr,BPY)*100,M.sharpe(rr,BPY),M.max_drawdown(rr)*100)
        fold_values.append(vals)
        print(f"{tr}m/{te}m CAGR {vals[0]:.2f}% Sharpe {vals[1]:.2f} DD {vals[2]:.1f}%")

    print("\nBootstrap comparison")
    print(f"{'strategy':<28}{'P(loss)':>10}{'median DD':>12}{'P(DD<-50)':>13}{'P(DD<-70)':>13}")
    for name,result in (("dynamic long/short",full),("fixed short0.5",fixed),("long/flat",longflat)):
        total,dd=block_bootstrap(result.returns)
        print(f"{name:<28}{(total<0).mean()*100:>9.1f}%{np.median(dd)*100:>11.1f}%"
              f"{(dd<-.5).mean()*100:>12.1f}%{(dd<-.7).mean()*100:>12.1f}%")


if __name__=="__main__":
    main()
