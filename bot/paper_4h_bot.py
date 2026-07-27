"""Deployed paper bot for the two fixed MA250 4h paper candidates.

Paper only: this module contains no authenticated broker/order path.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import truststore

truststore.inject_into_ssl()

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/"src"))
sys.path.insert(0,str(ROOT/"bot"))
import dynamic_4h_config as C

VARIANT=os.getenv("FIXED_4H_VARIANT","").strip().lower()
VARIANTS={
    "long_flat":{"slug":"fixed_2x_long_flat_4h","short_exposure":0.0},
    "long_short":{"slug":"fixed_2x_long_short_05_4h","short_exposure":0.5},
}
if VARIANT not in VARIANTS:
    raise RuntimeError("FIXED_4H_VARIANT must be 'long_flat' or 'long_short'")
V=VARIANTS[VARIANT]
STATE=ROOT/"bot"/f"state_4h_{VARIANT}.json"
STATUS=ROOT/"bot"/f"status_4h_{VARIANT}.json"
TRADES=ROOT/"bot"/f"trades_4h_{VARIANT}.csv"
INITIAL_CAPITAL=10_000.0
MEAN_FUNDING_8H=0.0001066


def get_json(url,params):
    r=requests.get(url,params=params,timeout=15,headers={"User-Agent":"btc-4h-paper-bot"})
    r.raise_for_status()
    return r.json()


def parse_klines(rows):
    cols=["open_time","open","high","low","close","volume","close_time",
          "quote","trades","tb_base","tb_quote","ignore"]
    df=pd.DataFrame(rows,columns=cols)
    df["timestamp"]=pd.to_datetime(df["open_time"],unit="ms",utc=True)
    for col in ("open","high","low","close","volume"): df[col]=df[col].astype(float)
    return df.set_index("timestamp")[["open","high","low","close","volume"]].sort_index()


def market_data():
    """Prefer tradeable perp + mark; fall back explicitly to public spot klines."""
    now=pd.Timestamp.now("UTC")
    source="binance_perpetual"
    try:
        rows=get_json("https://fapi.binance.com/fapi/v1/klines",
                      {"symbol":C.SYMBOL,"interval":"4h","limit":500})
        df=parse_klines(rows)
        marks=parse_klines(get_json("https://fapi.binance.com/fapi/v1/markPriceKlines",
                                   {"symbol":C.SYMBOL,"interval":"4h","limit":500}))
        df["mark_low"]=marks["low"].reindex(df.index)
        df["mark_high"]=marks["high"].reindex(df.index)
        df["mark_open"]=marks["open"].reindex(df.index)
    except Exception as exc:
        source=f"binance_spot_proxy ({type(exc).__name__})"
        rows=get_json("https://data-api.binance.vision/api/v3/klines",
                      {"symbol":C.SYMBOL,"interval":"4h","limit":500})
        df=parse_klines(rows)
        df["mark_low"],df["mark_high"],df["mark_open"]=df["low"],df["high"],df["open"]
    closed=df[df.index+pd.Timedelta(hours=4)<=now].copy()
    if len(closed)<C.MA_BARS:
        raise RuntimeError(f"only {len(closed)} closed bars; need at least "
                           f"{C.MA_BARS}")
    current=df[df.index+pd.Timedelta(hours=4)>now]
    execution_price=float(current["open"].iloc[0]) if len(current) else float(closed["close"].iloc[-1])
    return closed,current,execution_price,source


def funding_since(last_time,now,source):
    if last_time is None: return 0.0,"none (initial run)"
    lo=pd.Timestamp(last_time)
    if source.startswith("binance_perpetual"):
        try:
            rows=get_json("https://fapi.binance.com/fapi/v1/fundingRate",{
                "symbol":C.SYMBOL,
                "startTime":int(lo.timestamp()*1000)+1,
                "endTime":int(now.timestamp()*1000),
                "limit":1000,
            })
            return sum(float(x["fundingRate"]) for x in rows),"real Binance"
        except Exception:
            pass
    # Count scheduled 00/08/16 UTC settlements strictly after the prior run.
    grid=pd.date_range(lo.floor("8h"),now.ceil("8h"),freq="8h")
    count=sum((x>lo and x<=now) for x in grid)
    return count*MEAN_FUNDING_8H,"mean-rate proxy"


def signal(closed):
    close=closed["close"]
    ma=float(close.rolling(C.MA_BARS).mean().iloc[-1])
    direction=1 if close.iloc[-1]>ma else (-1 if V["short_exposure"] else 0)
    exposure=2.0 if direction>0 else V["short_exposure"]
    return {
        "bar_time":closed.index[-1],"close":float(close.iloc[-1]),"sma250":ma,
        "direction":direction,"target_exposure":direction*exposure,
    }


def default_state():
    return {"wallet":INITIAL_CAPITAL,"qty":0.0,"entry_price":0.0,
            "initial_capital":INITIAL_CAPITAL,"peak_equity":INITIAL_CAPITAL,
            "last_bar":None,"last_direction":0,"runs":0,"liquidated":False}


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else default_state()


def equity(state,price):
    return state["wallet"]+state["qty"]*(price-state["entry_price"])


def check_liquidation(state,last_bar):
    if state["qty"]==0: return False
    adverse=float(last_bar["mark_low"] if state["qty"]>0 else last_bar["mark_high"])
    eq=state["wallet"]+state["qty"]*(adverse-state["entry_price"])
    maintenance=C.MAINTENANCE_MARGIN*abs(state["qty"])*adverse
    if eq<=maintenance:
        state.update(wallet=0.0,qty=0.0,entry_price=0.0,liquidated=True)
        return True
    return False


def run(dry=False):
    closed,current,price,data_source=market_data()
    sig=signal(closed);state=load_state()
    if state.get("liquidated"):
        raise RuntimeError("paper account is marked liquidated; manual reset required")
    if state.get("last_bar")==str(sig["bar_time"]):
        print(f"[4h] already processed {sig['bar_time']}");return

    before=dict(state)
    liquidated=check_liquidation(state,closed.iloc[-1])
    fund_rate,funding_source=funding_since(state.get("last_bar"),sig["bar_time"],data_source)
    funding_payment=0.0
    if state["qty"]!=0 and not liquidated:
        funding_payment=state["qty"]*price*fund_rate
        state["wallet"]-=funding_payment

    current_direction=int(np.sign(state["qty"]))
    direction_change=sig["direction"]!=current_direction
    trade=direction_change
    eq_before=max(equity(state,price),0.0)
    old_qty=state["qty"]
    target_qty=(sig["target_exposure"]*eq_before/price) if trade and eq_before>0 else old_qty
    changed_qty=target_qty-old_qty
    notional=abs(changed_qty)*price
    cost=notional*(C.FEE+C.SLIPPAGE) if trade else 0.0
    if trade:
        state["wallet"]=eq_before-cost
        state["qty"]=target_qty
        state["entry_price"]=price if target_qty else 0.0
    eq_after=max(equity(state,price),0.0)
    state["peak_equity"]=max(state.get("peak_equity",INITIAL_CAPITAL),eq_after)
    state["last_bar"]=str(sig["bar_time"]);state["last_direction"]=sig["direction"]
    state["runs"]=state.get("runs",0)+1
    action=("LIQUIDATED" if liquidated else
            "EXIT" if direction_change and sig["direction"]==0 else
            "REVERSE" if direction_change and old_qty!=0 else
            "ENTER" if direction_change else
            "HOLD")
    report={
        "strategy":V["slug"],"variant":VARIANT,
        "timestamp_utc":pd.Timestamp.now("UTC").isoformat(),
        "closed_bar_time":sig["bar_time"].isoformat(),"run_number":state["runs"],
        "dry_run":dry,"action":action,
        "side":"LONG" if sig["direction"]>0 else ("SHORT" if sig["direction"]<0 else "FLAT"),
        "data_source":data_source,"funding_source":funding_source,
        "btc_price":round(price,2),"closed_price":round(sig["close"],2),
        "sma250":round(sig["sma250"],2),
        "target_exposure":round(sig["target_exposure"],4),
        "previous_exposure":round((old_qty*price/eq_before) if eq_before else 0,4),
        "btc_units_traded":round(changed_qty,8),"trade_value_usd":round(notional,2),
        "cost_usd":round(cost,2),"funding_rate_sum":fund_rate,
        "funding_payment_usd":round(funding_payment,4),"wallet_usd":round(state["wallet"],2),
        "btc_contract_qty":round(state["qty"],8),"entry_price":round(state["entry_price"],2),
        "total_equity_usd":round(eq_after,2),
        "total_return_pct":round((eq_after/state["initial_capital"]-1)*100,2),
        "drawdown_pct":round((eq_after/state["peak_equity"]-1)*100,2),
        "liquidated":state.get("liquidated",False),
    }
    report["summary"]=(f"{action} {report['side']} target {sig['target_exposure']:+.2f}x; "
                       f"BTC ${price:,.0f}, SMA250 ${sig['sma250']:,.0f}; "
                       f"equity ${eq_after:,.2f}.")
    print(json.dumps(report,indent=2))
    if not dry:
        STATE.write_text(json.dumps(state,indent=2))
        STATUS.write_text(json.dumps(report,indent=2))
        if trade or liquidated:
            pd.DataFrame([report]).to_csv(TRADES,mode="a",header=not TRADES.exists(),index=False)
        try:
            from notify import notify
            compat={
                **report,"closed_bar_date":str(sig["bar_time"]),
                "agreement":f"close {'>' if sig['direction']>0 else '<='} SMA250",
                "previous_exposure_pct":report["previous_exposure"]*100,
                "new_target_exposure_pct":report["target_exposure"]*100,
                "current_exposure_pct":report["target_exposure"]*100,
                "side":"BUY/LONG" if sig["direction"]>0 else
                       ("SELL/SHORT" if sig["direction"]<0 else "CASH/FLAT"),
            }
            notify(compat)
        except Exception as exc:
            print(f"[notify] {exc}")


def status():
    if STATUS.exists(): print(STATUS.read_text())
    else: print(json.dumps(default_state(),indent=2))


def reset():
    for path in (STATE,STATUS,TRADES):
        if path.exists(): path.unlink()
    STATE.write_text(json.dumps(default_state(),indent=2))
    print("[4h] reset")


def main():
    try: sys.stdout.reconfigure(encoding="utf-8",errors="replace")
    except Exception: pass
    ap=argparse.ArgumentParser()
    ap.add_argument("command",choices=["run","status","reset"])
    ap.add_argument("--dry-run",action="store_true")
    args=ap.parse_args()
    {"run":lambda:run(args.dry_run),"status":status,"reset":reset}[args.command]()


if __name__=="__main__": main()
