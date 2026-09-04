# MA250 volatility-targeted challenger

Paper-only BTCUSDT perpetual challenger to the two incumbent MA250 accounts.

- Completed close above SMA(250): long. Otherwise flat.
- Position is sized by `VOL_TARGET / realised_vol` rather than held at a
  constant multiple, and capped at `MAX_LEVERAGE = 1.5`.
- Resizes only when realised exposure drifts more than `REBALANCE_BAND` (20%)
  from target, which keeps turnover close to the fixed-size rule.
- Uses perpetual candles, mark-price extremes, and real funding only.
- Fails closed if perpetual, mark, funding, or volatility data is unavailable.

## Why this exists

The incumbents hold a constant +2.0x. Constant leverage scales the return
stream linearly, so it adds drawdown without adding risk-adjusted return.
Measured over the full 4h history in
[STRATEGY_IMPROVEMENTS.md](../../docs/audits/STRATEGY_IMPROVEMENTS.md):

| | 1x | 1.5x | 2x frozen | this account |
|---|---:|---:|---:|---:|
| Sharpe | 1.12 | 1.12 | 1.12 | **1.20** |
| Sortino | 1.04 | 1.04 | 1.04 | **1.19** |
| Max drawdown | -73% | -88% | -95% | **-60%** |
| P(drawdown worse than 70%) | 14% | 68% | **96%** | **6.7%** |
| Survives -50% shock | yes | yes | **no** | **yes** |

It gives up headline return. That return was being bought with ruin risk, not
skill.

## Status

Frozen 2026-09-04, paper-only, with no live approval. It needs at least 12
months of prospective fills before it is comparable with the incumbents, and
its backtest evidence is in-sample on the same history the original rules were
selected from.
