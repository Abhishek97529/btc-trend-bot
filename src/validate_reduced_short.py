"""Rigorous validation for MA250: long 2x above MA, short 1x below MA."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parent))
import metrics as M
from corrected_ma_regime_2x import BPY, load
from extended_history_ma250 import load_spot_proxy
from indicators import sma
from test_ma250_long_short import simulate_ls, stat
from validate_ma250_prepaper import block_bootstrap


def make_rule(df,ma=250,short_lev=1.0):
    line=sma(df["close"],ma)
    target=pd.Series(np.where(df["close"]>line,1,np.where(df["close"]<line,-1,0)),
                     index=df.index)
    lev=pd.Series(np.where(target>0,2.0,np.where(target<0,short_lev,0.0)),index=df.index)
    return target,lev


def row(label,result):
    s=stat(result)
    return (f"{label:<34}{s['net']:>11.1f}{s['cagr']:>10.2f}{s['sharpe']:>9.2f}"
            f"{s['dd']:>10.1f}{s['calmar']:>9.2f}{result.entries:>9}"
            f"{str(result.liquidated):>7}")


def table(title,items):
    print(f"\n## {title}")
    print(f"{'test':<34}{'net%':>11}{'CAGR%':>10}{'Sharpe':>9}{'maxDD%':>10}"
          f"{'Calmar':>9}{'entries':>9}{'liq':>7}")
    print("-"*99)
    for label,result in items: print(row(label,result))


def fold_returns(df,target,lev,train_months,test_months):
    tr=int(train_months/12*BPY); te=int(test_months/12*BPY)
    cursor=tr;parts=[];entries=0;liq=False
    while cursor<len(df):
        end=min(cursor+te,len(df))
        r=simulate_ls(df,target,lev,df.index[cursor],df.index[end-1])
        parts.append(r.returns);entries+=r.entries;liq|=r.liquidated;cursor=end
    rr=pd.concat(parts)
    return rr,entries,liq


def spells(result):
    direction=np.sign(result.position)
    change=direction.ne(direction.shift(1,fill_value=0))
    starts=result.position.index[change & direction.ne(0)]
    returns=[];sides=[]
    for start in starts:
        loc=result.position.index.get_loc(start)
        side=int(np.sign(result.position.iloc[loc]))
        future=np.where(np.sign(result.position.iloc[loc+1:].to_numpy())!=side)[0]
        endloc=(loc+1+future[0]) if len(future) else len(result.position)-1
        before=result.equity.iloc[loc-1] if loc else 1.0
        returns.append(result.equity.iloc[endloc]/before-1)
        sides.append(side)
    return np.array(returns),np.array(sides)


def main():
    df=load();target,lev=make_rule(df)
    base=simulate_ls(df,target,lev)
    table("Baseline",[("long2x / short1x",base)])

    params=[]
    for ma in (150,175,200,225,250,275,300,325,350,400):
        t,l=make_rule(df,ma,1)
        params.append((f"MA{ma}",simulate_ls(df,t,l)))
    table("MA sensitivity",params)

    delays=[]
    for n in (0,1,2,3):
        delays.append((f"additional delay {n} bars",
                       simulate_ls(df,target.shift(n).fillna(0),lev.shift(n).fillna(0))))
    table("Execution-delay stress",delays)

    costs=[]
    for cost in (.0004,.0007,.0010,.0015,.0025):
        costs.append((f"{cost*100:.02f}% per side",
                      simulate_ls(df,target,lev,fee=cost,slippage=0)))
    table("Trading-cost stress",costs)

    funding=[]
    for mult in (0,1,1.25,1.5,2):
        funding.append((f"signed funding x{mult:g}",
                        simulate_ls(df,target,lev,funding_multiplier=mult)))
    table("Funding stress",funding)

    shortlev=[]
    for sl in (0,.5,.75,1,1.25,1.5,2):
        t,l=make_rule(df,250,sl)
        shortlev.append((f"long2x / short{sl:g}x",simulate_ls(df,t,l)))
    table("Short-leverage sensitivity",shortlev)

    shocks=[]
    for shock in (.20,.30,.40,.50):
        shocks.append((f"long adverse {shock:.0%}",
                       simulate_ls(df,target,lev,long_mark_shock=shock)))
    for shock in (.30,.50,.75,1.0):
        shocks.append((f"short adverse +{shock:.0%}",
                       simulate_ls(df,target,lev,short_mark_shock=shock)))
    table("Directional mark-price shocks",shocks)

    print("\n## Fixed-rule reset schedules")
    print(f"{'schedule':<16}{'CAGR%':>10}{'Sharpe':>9}{'maxDD%':>10}{'entries':>9}{'liq':>7}")
    fold_stats=[]
    for tr,te in ((12,3),(18,6),(24,6),(36,12)):
        rr,en,liq=fold_returns(df,target,lev,tr,te)
        values=(M.cagr(rr,BPY)*100,M.sharpe(rr,BPY),M.max_drawdown(rr)*100)
        fold_stats.append(values)
        print(f"{tr}m/{te}m{values[0]:>11.2f}{values[1]:>9.2f}{values[2]:>10.1f}{en:>9}{str(liq):>7}")

    dev=(df.index[0],pd.Timestamp("2022-12-31 23:59:59",tz="UTC"))
    val=(pd.Timestamp("2023-01-01",tz="UTC"),pd.Timestamp("2024-12-31 23:59:59",tz="UTC"))
    hold=(pd.Timestamp("2025-01-01",tz="UTC"),df.index[-1])
    table("Predetermined periods",[
        ("DEV 2019-2022",simulate_ls(df,target,lev,*dev)),
        ("VALIDATE 2023-2024",simulate_ls(df,target,lev,*val)),
        ("2025+ diagnostic",simulate_ls(df,target,lev,*hold)),
    ])

    proxy,_=load_spot_proxy(1);pt,pl=make_rule(proxy)
    extended=simulate_ls(proxy,pt,pl)
    table("Extended-history check",[("real perp 2019+",base),("proxy 2017+",extended)])

    trades,sides=spells(base)
    print("\n## Trade statistics")
    for name,mask in (("all",np.ones(len(trades),dtype=bool)),("long",sides>0),("short",sides<0)):
        x=trades[mask]
        print(f"{name:<7} n={len(x):>3} win={(x>0).mean()*100:>5.1f}% "
              f"median={np.median(x)*100:>7.2f}% mean={np.mean(x)*100:>8.2f}% "
              f"best={np.max(x)*100:>8.1f}% worst={np.min(x)*100:>7.1f}%")

    bt,bd=block_bootstrap(base.returns)
    print("\n## Block bootstrap — 2,000 one-week-block paths")
    print(f"P(terminal loss)={(bt<0).mean()*100:.1f}%")
    print(f"Median maxDD={np.median(bd)*100:.1f}%")
    print(f"P(maxDD<-50%)={(bd<-.5).mean()*100:.1f}%")
    print(f"P(maxDD<-70%)={(bd<-.7).mean()*100:.1f}%")

    s=stat(base)
    gates={
        "real-perp Sharpe > 1":s["sharpe"]>1,
        "extended Sharpe > 1":stat(extended)["sharpe"]>1,
        "all MA200-300 Sharpe positive":all(stat(r)["sharpe"]>0 for _,r in params[2:7]),
        "1-bar delay Sharpe > 1":stat(delays[1][1])["sharpe"]>1,
        "0.15%/side Sharpe > 1":stat(costs[3][1])["sharpe"]>1,
        "funding x1.5 Sharpe > 1":stat(funding[3][1])["sharpe"]>1,
        "all reset schedules Sharpe > 0.6":all(x[1]>.6 for x in fold_stats),
        "no historical liquidation":not base.liquidated and not extended.liquidated,
        "bootstrap P(DD<-70%) <= 25%":(bd<-.7).mean()<=.25,
    }
    print("\n## Gates")
    for name,passed in gates.items(): print(f"{'PASS' if passed else 'FAIL'}  {name}")
    print(f"OVERALL {sum(gates.values())}/{len(gates)}")


if __name__=="__main__":
    main()
