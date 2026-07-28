# Daily spot ensemble

Primary defensive paper benchmark.

- Signal: seven fixed trend votes on completed daily BTCUSDT candles.
- Exposure: agreement fraction when at least half the votes are positive;
  otherwise cash.
- Execution: first observed executable price after the daily candle closes.
- Costs: configured fee and slippage on actual changed notional.
- Live trading: disabled by frozen configuration.

Runtime files live in `runtime/`. The corrected audit is
`../../docs/audits/LOCKED_SPOT_RIGOROUS_AUDIT.md`.
