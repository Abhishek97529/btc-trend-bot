# Four-hour spot dual trend

Independent paper candidate; it does not replace the daily strategy.

Hold BTC only when all conditions are true on the completed four-hour candle:

1. EMA(24) is above EMA(168);
2. 120-bar momentum is positive;
3. close is above SMA(300).

Otherwise hold cash. There is no leverage or shorting. The runner fails closed
on stale data, source substitution, or a gap larger than one bar.
