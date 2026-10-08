# What not to change

Each line below exists because changing it would either destroy the records or
destroy money. They are not style preferences.

## Do not disable safe mode to force a trade

Safe mode turns on when Sterling cannot establish its own state — an unexplained
broker position, a missing stop-loss, a failed reconciliation. Those are exactly
the moments when opening another position is most dangerous. Fix the condition,
then release it.

## Do not resend an order whose outcome is unknown

An `UNKNOWN` outcome puts the account into recovery and Sterling will not resend.
Do not resend by hand or by script. Ask the broker what happened and let
reconciliation record it. A duplicate order is a second real position.

## Do not raise size to recover a loss

Size is set by the day-one caps (`STERLING_LIVE_MINIMUM_*`), `risk_pct`,
`max_lots` and `execution_lots`. Raise them only by a decision made in advance,
never after a loss and never because a few trades went well.

## Do not turn UNKNOWN into a default

Everywhere Sterling does not know something — a missing price, a missing
timestamp, a missing contract, a missing broker confirmation — it says UNKNOWN and
stops. Replacing any of those with a guess, a last-known value, or a model
estimate makes the system confident instead of correct.

## Do not change Snapback's parameters mid-flight

Not the delta, the days to expiry, the assumed volatility, the position size, the
entry rule or the exit rule. A changed rule is a different strategy; give it its
own name and let it run alongside, rather than editing the running one.

## Do not turn NOT_LISTED into the nearest strike

Sterling calculates which option contract to buy. Sometimes that exact contract
does not exist, and Sterling records NOT_LISTED and skips the trade. Buying the
nearest one instead would be a different strategy wearing the same name.

## Do not label modelled results as real

Historical results used *estimated* option prices, because real historical prices
for expired contracts cannot be bought. They show what a rule would have done under
assumptions, not what it will do.

## Do not delete records because a trade lost

Losing trades are data. A record that keeps only the good days measures nothing.

## Do not move release tags

A release tag points at one exact version of the code. Moving one makes every
record written under it unverifiable.

## Do not change the execution vehicle of a running strategy

Bought options and futures are two different economic propositions for the same
signal. A vehicle change is a new strategy with a new identity, never an edit to a
running one.

## Do not reassign a fill from one broker account to another

After an account migration the strategy identity is unchanged, but the execution
record segment is not. Old fills stay with the old account forever.

## Do not read a good week as proof

Snapback loses on most individual trades by design and makes its money on a small
number of larger winners. A profitable month means little, and so does a losing
one. Backtests in this repository, including long-option Supertrend studies that
were not net-positive out of sample, are the reason the first days are capped at
one lot.
