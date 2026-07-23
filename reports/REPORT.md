# BTC/USDT Hourly Strategy Research

All returns net of 0.10% fee + 5 bps slippage per side. Long/flat spot only.

## Out-of-sample TEST (params chosen on 2019-2022 train, evaluated on 2023+)

| strategy           |   total_return |   cagr |   ann_vol |   sharpe |   sortino |   max_drawdown |   calmar |   exposure |   trades | params                                               |
|:-------------------|---------------:|-------:|----------:|---------:|----------:|---------------:|---------:|-----------:|---------:|:-----------------------------------------------------|
| buy_and_hold       |          299   |   47.5 |      46.5 |     1.07 |      1.37 |          -53.7 |     0.88 |      100   |        1 |                                                      |
| ema_crossover      |          152   |   29.7 |      31.9 |     0.97 |      0.95 |          -30.2 |     0.98 |       53.8 |       97 | {'fast': 72, 'slow': 336}                            |
| donchian_breakout  |           72.5 |   16.6 |      26.2 |     0.72 |      0.56 |          -34.3 |     0.48 |       35.5 |       81 | {'entry': 336, 'exit_n': 96}                         |
| rsi_mean_reversion |          -26.5 |   -8.3 |      12.6 |    -0.62 |     -0.19 |          -37.4 |    -0.22 |        5.6 |       20 | {'n': 21, 'lower': 30, 'upper': 65, 'trend': 300}    |
| momentum           |          -34.9 |  -11.4 |      30.2 |    -0.25 |     -0.23 |          -59.2 |    -0.19 |       45.7 |      851 | {'lookback': 168, 'trend': 400}                      |
| trend_vol_target   |          -46.6 |  -16.1 |      32.8 |    -0.37 |     -0.37 |          -71.7 |    -0.23 |       52.4 |     1081 | {'trend': 200, 'atr_n': 96, 'target_atr_pct': 0.015} |

## In-sample TRAIN (for overfit comparison)

| strategy           |   total_return |   cagr |   ann_vol |   sharpe |   sortino |   max_drawdown |   calmar |   exposure |   trades |
|:-------------------|---------------:|-------:|----------:|---------:|----------:|---------------:|---------:|-----------:|---------:|
| buy_and_hold       |          348.4 |   45.5 |      73.2 |     0.88 |      1.07 |          -77.2 |     0.59 |      100   |        1 |
| ema_crossover      |          541.5 |   59.2 |      48.9 |     1.2  |      1.08 |          -53.9 |     1.1  |       52.8 |      118 |
| donchian_breakout  |          431.5 |   51.9 |      39.1 |     1.26 |      0.88 |          -41.9 |     1.24 |       31.2 |       82 |
| rsi_mean_reversion |           18.5 |    4.3 |      18.4 |     0.32 |      0.09 |          -21.4 |     0.2  |        5.4 |       28 |
| momentum           |          154.9 |   26.4 |      44.6 |     0.75 |      0.64 |          -54.6 |     0.48 |       44.1 |      882 |
| trend_vol_target   |           40   |    8.8 |      45.8 |     0.41 |      0.4  |          -80.3 |     0.11 |       52.3 |     2682 |

## Walk-forward (rolling re-optimization, the honest number)

| strategy           |   total_return |   cagr |   ann_vol |   sharpe |   sortino |   max_drawdown |   calmar |   exposure |   trades |
|:-------------------|---------------:|-------:|----------:|---------:|----------:|---------------:|---------:|-----------:|---------:|
| ema_crossover      |          405.2 |   28.7 |      40.3 |     0.83 |      0.77 |          -64   |     0.45 |        100 |        0 |
| donchian_breakout  |          184.5 |   17.7 |      36.3 |     0.63 |      0.51 |          -54.7 |     0.32 |        100 |        0 |
| rsi_mean_reversion |          -57.6 |  -12.5 |      28.1 |    -0.34 |     -0.15 |          -66.8 |    -0.19 |        100 |        0 |
| momentum           |           19.5 |    2.8 |      37.1 |     0.26 |      0.23 |          -72.3 |     0.04 |        100 |        0 |
| trend_vol_target   |          -55.4 |  -11.8 |      38.9 |    -0.13 |     -0.13 |          -88.2 |    -0.13 |        100 |        0 |
| buy_and_hold       |          937.1 |   44   |      61.7 |     0.9  |      1.1  |          -77.2 |     0.57 |        100 |        0 |
