"""Compare practical MA250 long2x/short0.5x and long2x/short0.75x variants."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parent))
import metrics as M
from corrected_ma_regime_2x import BPY, load
from extended_history_ma250 import load_spot_proxy
from test_ma250_long_short import simulate_ls, stat
from validate_ma250_prepaper import block_bootstrap
from validate_reduced_short import make_rule, fold_returns


SHORT_LEVELS=(0.5,0.75)


def show(label,result):
    s=stat(result)
    print(f"{label:<34}{s['net']:>11.1f}{s['cagr']:>10.2f}{s['sharpe']:>9.2f}"
          f"{s['dd']:>10.1f}{s['calmar']:>9.2f}{result.entries:>9}")


def main():
    df=load();proxy,_=load_spot_proxy(1)
    real={};extended={}
    for sl in SHORT_LEVELS:
        t,l=make_rule(df,250,sl);real[sl]=simulate_ls(df,t,l)
        pt,pl=make_rule(proxy,250,sl);extended[sl]=simulate_ls(proxy,pt,pl)

    print("# Practical reduced-short sizing comparison")
    print(f"\n{'strategy':<34}{'net%':>11}{'CAGR%':>10}{'Sharpe':>9}"
          f"{'maxDD%':>10}{'Calmar':>9}{'entries':>9}")
    print("-"*92)
    for sl in SHORT_LEVELS: show(f"real: long2 / short{sl}",real[sl])
    for sl in SHORT_LEVELS: show(f"extended: long2 / short{sl}",extended[sl])

    periods=[
        ("DEV 2019-2022",df.index[0],pd.Timestamp("2022-12-31 23:59:59",tz="UTC")),
        ("VALIDATE 2023-2024",pd.Timestamp("2023-01-01",tz="UTC"),pd.Timestamp("2024-12-31 23:59:59",tz="UTC")),
        ("2025+ diagnostic",pd.Timestamp("2025-01-01",tz="UTC"),df.index[-1]),
    ]
    print("\n## Predetermined periods")
    for name,lo,hi in periods:
        print(f"\n{name}")
        for sl in SHORT_LEVELS:
            t,l=make_rule(df,250,sl);show(f"long2 / short{sl}",simulate_ls(df,t,l,lo,hi))

    print("\n## Year by year")
    print(f"{'year':<6}{'short0.5 return':>17}{'DD':>9}{'short0.75 return':>19}{'DD':>9}")
    for year in sorted(set(df.index.year)):
        vals=[]
        for sl in SHORT_LEVELS:
            r=real[sl].returns[real[sl].returns.index.year==year]
            vals.extend((M.total_return(r)*100,M.max_drawdown(r)*100))
        print(f"{year:<6}{vals[0]:>17.1f}{vals[1]:>9.1f}{vals[2]:>19.1f}{vals[3]:>9.1f}")

    print("\n## Execution delay")
    for sl in SHORT_LEVELS:
        print(f"\nshort {sl}x")
        t,l=make_rule(df,250,sl)
        for delay in (0,1,2,3):
            show(f"delay {delay} bars",simulate_ls(df,t.shift(delay).fillna(0),
                                                   l.shift(delay).fillna(0)))

    print("\n## Cost and funding stress")
    for sl in SHORT_LEVELS:
        print(f"\nshort {sl}x")
        t,l=make_rule(df,250,sl)
        show("base",simulate_ls(df,t,l))
        show("cost 0.15%/side",simulate_ls(df,t,l,fee=.0015,slippage=0))
        show("funding x1.5",simulate_ls(df,t,l,funding_multiplier=1.5))
        show("funding x2",simulate_ls(df,t,l,funding_multiplier=2))

    print("\n## Fixed reset schedules")
    print(f"{'short':<8}{'schedule':<12}{'CAGR%':>9}{'Sharpe':>9}{'maxDD%':>10}")
    folds={}
    for sl in SHORT_LEVELS:
        t,l=make_rule(df,250,sl);folds[sl]=[]
        for tr,te in ((12,3),(18,6),(24,6),(36,12)):
            rr,_,_=fold_returns(df,t,l,tr,te)
            values=(M.cagr(rr,BPY)*100,M.sharpe(rr,BPY),M.max_drawdown(rr)*100)
            folds[sl].append(values)
            print(f"{sl:<8}{str(tr)+'m/'+str(te)+'m':<12}{values[0]:>9.2f}{values[1]:>9.2f}{values[2]:>10.1f}")

    print("\n## Bootstrap — 2,000 one-week-block paths")
    print(f"{'short':<8}{'P(loss)':>10}{'median DD':>12}{'P(DD<-50)':>13}{'P(DD<-70)':>13}")
    boots={}
    for sl in SHORT_LEVELS:
        total,dd=block_bootstrap(real[sl].returns);boots[sl]=(total,dd)
        print(f"{sl:<8}{(total<0).mean()*100:>9.1f}%{np.median(dd)*100:>11.1f}%"
              f"{(dd<-.5).mean()*100:>12.1f}%{(dd<-.7).mean()*100:>12.1f}%")

    print("\n## Decision metrics")
    for sl in SHORT_LEVELS:
        s=stat(real[sl]);es=stat(extended[sl]);_,dd=boots[sl]
        score=sum((
            s["sharpe"]>1,
            es["sharpe"]>1,
            s["dd"]>-60,
            all(v[1]>.6 for v in folds[sl]),
            (dd<-.7).mean()<=.25,
        ))
        print(f"short {sl}x: {score}/5 risk gates; full Calmar {s['calmar']:.2f}; "
              f"extended DD {es['dd']:.1f}%")


if __name__=="__main__":
    main()
