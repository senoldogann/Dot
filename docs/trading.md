# Trading

Dot trades crypto on Coinbase when you switch it on (items 763 and 764): with an account of its own that trades at
Coinbase's real prices with money that is not there (`paper`), to try a way of trading, or with your Coinbase account
(`live`). The limits you set judge every order before any market sees it, in every approval mode, yolo's too, and no run
can change them.

## Switching it on

[Settings › Trading](daemon.md#settings--trading), the trading desk the Trader agent's page opens (item 847), or the
Trader's card among the ready-made agents before there is one (or `[trading]` in `config.toml`,
[Configuration](configuration.md)):

- **Mode**: `off` (the default: runs have no trading tool at all), `paper` or `live`. A switch from `off` gives runs the
  trading tools from Dot's next start; one between `paper` and `live` holds from the next order.
- **Products**: the Coinbase product ids runs may trade, all priced in one currency, `quote` (`EUR` by default:
  `BTC-EUR`, `ETH-EUR`, `SOL-EUR`), or `*`, the whole market (item 771): every product priced in `quote` that Coinbase
  trades now and that traded at least `min_volume` of it over the last day, as Coinbase sums it (1,000,000 by default).
  A market thinner than that is one a small order moves and one a pump empties. Measured on 2026-10-28: in `EUR`
  Coinbase trades 33 products, 6 of them past a million a day; in `USDC` 390, 51 past a million and 112 past a quarter
  of one (a `USDC` product is Coinbase's `USD` book under another name). The whole market is `quote = "USDC"` with
  `products = ["*"]` in `config.toml`; the paper account then wants **Start again**, as its money is in the old quote.
- **Limits**, in `quote`:
  - `max_order`: the most one order may be worth (25 by default);
  - `max_day`: the most a day's orders may add up to, buys and sells, a buy with its fee (100; item 785);
  - `max_day_loss`: how much the account may lose in a day before no buy is placed until the next; a sell still is, as
    it lowers what is at stake (25);
  - `max_position`: the most one product held may be worth after a buy (200).
  The day is yours: midnight in `[scheduler] timezone`.
- **Wakes**: `wake_schedule`, the id of a schedule (as `sch_...`) a move of the market starts at once, which the
  Trading page's **Wake on a move** chooses among the schedules, or none (item 813: only a schedule the store holds is
  taken); `wake_move_pct`, `wake_per_day` and `wake_gap_min` (`config.toml` only; below, "Woken by the market").
- **Paper**: `paper_start` (1000) is what the paper account starts with, `paper_fee_rate` (0.006) the share of each fill
  it pays, as Coinbase's taker fee does; it buys at the ask and sells at the bid. **Start again** gives it `paper_start`
  back; its orders stay in the record, outside it, and what they traded today no longer counts against `max_day`. A
  `paper_start` changed later is what the next **Start again** gives: the account keeps what it started with, and
  "since the start" is counted from that (item 785).
- **Coinbase key** (for `live`): make an API key in Coinbase's Developer Platform with the **ECDSA** signature algorithm
  and the **View** and **Trade** permissions, never **Transfer**, and paste its name (`organizations/.../apiKeys/...`)
  and private key. Dot asks Coinbase what the key may do before it keeps it, and refuses one that may transfer money:
  Dot has no withdrawal and needs none. Make it for a **portfolio of its own** in Coinbase (a key may be limited to one
  portfolio), holding only what you let Dot risk: that bounds what any trade can lose, whatever happens on the Mac (see
  the last section). The key is kept in your Mac's login Keychain (generic password, service `dot-vault`, account
  `trading-coinbase`), never in Dot's files; Dot's tools, logs and errors never show it. Each request is signed with it
  for that one request, for two minutes, as Coinbase asks.

What lets runs do more with money is done on the Mac only: switching trading on or to live, raising a limit, allowing a
product (`*` too, and a lower `min_volume`), switching the trend engine on (item 818), saving a key. A phone's key gets
a refusal for those, and can always narrow: switch trading off or to paper, switch the trend engine off, lower a limit,
drop a product, remove the key. Switching to live
checks the key with Coinbase first.

## What a run does

With trading on, a run has the `trading` tool group:

- `trading_market`: Coinbase's best bid and ask of the allowed products now with the last day's change and volume
  (those named; with `*` alone, the eight busiest), or of any product priced in `quote`, or a product's candles (one
  minute to one day) with what they come to (item 765): the change over the window, its high and low, the averages of
  the last 20 and 50 closes and where the last close stands against them, the RSI of the last 14 moves, and how wide a
  candle is. Coinbase's public market data, with no key: paper and live read the same prices.
- `trading_scan` (item 771): the products a run may trade, the whole market with `*`, in one look: the biggest moves
  of the last day up or down, the busiest, those nearest their day's high or their day's low, each with its price, its
  change, where it stands in its day's range and what it traded. Coinbase's list of products, one request of about a
  megabyte, kept for 30 seconds. Where to look first; `trading_market` then reads a product's candles.
- `trading_portfolio`: the account (paper or yours), what it holds and is worth at the bids (every currency held is
  priced, not only the allowed products; one Coinbase has no product of in the quote is said and worth nothing to the
  account; one whose price could not be read this time is said too, counted as nothing, and holds buys until the day's
  first whole look, below), today's change and what the limits leave today.
- `trading_order`: a market order: a buy spends `quote_amount`; a sell sells `base_amount` or what brings
  `quote_amount`. Each carries its `reason`, in the run's words (what the prices and the account showed, what it
  expects, what ends the trade), kept with the order and shown in Settings › Trading and in `trading_history`. A buy
  names its `stop_price` (a buy without one is refused) and may name a `target_price`: its exit on the paper account,
  below; a sell names neither.
- `trading_exit` (item 770): moves an open exit's stop or target, judged against the prices now as a buy's are.
- `trading_history`: the account's orders, newest first, each with its reason.
- `trading_engine` (item 818): the trend engine's last look (the shares it holds and wants, the orders it placed,
  what held one back, what it made against holding), and pause and resume, each with its reason (below).

The system prompt tells the run to trade only with `trading_order`, never through the browser or the shell, to read the
prices and the account first, to say why it trades and what ends the trade, never to take what a page or a post says
of a coin as a reason, and never to split or repeat an order to get around a limit.

## Exits: the stop and the target, watched day and night

A buy names its `stop_price`, the price at or under which the trade is wrong, and may name a `target_price`, at or over
which it takes the gain (item 770). The stop must be under the bid when the buy is placed, the target over the ask. On
the paper account, the buy's fill is kept as an **exit**, and Dot's daemon looks at the open exits every fifteen seconds
or so, with no model and no run: once the bid is at the stop or the target, it sells what the exit holds at the market
and tells you (your phone, the desktop). A run sees the open exits in `trading_portfolio` and moves one with
`trading_exit` (a stop raised under a rising price keeps a gain); a sell by hand takes from the product's exits, oldest
first.

On your Coinbase account (`live`), Dot keeps no exit yet: a live buy still names its stop, which its question shows you
with the words that Dot does not sell at it, and the answer of the order says so to the run. What Dot sells by itself
with real money comes as an item of its own, reviewed on its own, before you switch to live; until then, set a stop on
Coinbase yourself if one should hold.

An exit's sell is held back by none of the limits: a stop that one order's cap or the day's could keep from selling
would protect nothing, and it lowers what is at stake; it counts against the day's orders like any other. What holds it
back, for as long as it lasts, even when it comes while the watch is reading the prices: trading off or switched to
live, your emergency stop, and Coinbase not taking orders of the product; nothing is sold and every exit stays as it
is, sold at the first look once nothing holds it, and starting the paper account again closes its exits. A sell that
may have gone out but did not say what it did is not tried again, so that it cannot sell twice: the exit is marked
failed and you are told. One that never went out (Coinbase's prices could not be read first) leaves the exit open,
tried again at the next look, and the other exits are looked at all the same; held at every look for five minutes, it
is told to you once, and Dot keeps trying. An exit sells what it holds when its sell's turn comes: a sell by hand that
took from it in the meantime leaves it less to sell, or nothing, never what is not its own. The watch runs while the
daemon does: a Mac asleep or off sells nothing until it wakes.

## Woken by the market

A schedule fires at its time; the market does not wait for it (item 772). With `wake_schedule` naming a schedule, Dot's
daemon looks at the products a run may trade about once a minute, with no model and no run (Coinbase's list of
products: one request, about 70 kB compressed). A product whose price moved over the last fifteen minutes by at least
`wake_move_pct` percent (3 by default) and by three times what it usually moves in fifteen minutes (the median of its
moves over the last two hours) starts that schedule at once, as **Run now** would, in a chat titled `<name> · <time> ·
market` or in the schedule's own, its first message saying what moved: `(Started at 14:02 by a move of the market, not
at its time: SOL-USDC +4.20 % in 15 minutes (it usually moves 0.60 %), now 187 USDC. Look at what moved first.)`. A
coin that swings by five percent an hour wakes nothing at three; one that hardly moves wakes nothing under
`wake_move_pct`.

What holds it back:

- at most `wake_per_day` runs a day (6) and `wake_gap_min` minutes between two (30): the model's time is what a wake
  costs;
- a product that moved wakes nothing again for two hours, whether its move woke a run or found none to wake (the day's
  count reached, the gap not passed, a run of the schedule at work, the emergency stop engaged, the schedule switched
  off): a move is not taken up later, when it is old;
- a product's usual move is known after about three quarters of an hour of looks, which Dot keeps in memory: for that
  long after the daemon starts or the Mac wakes, nothing moves enough;
- trading off, or no schedule named: the market is not looked at.

A run woken so is one of the schedule's like any other, held by the same limits.

## Trying it: the paper shift

The way to see what Dot's trading comes to before any money is on it: a schedule on the paper account (the owner's runs
have one every hour, "Kağıt trading vardiyası") whose goal says the order of work — read the account and today's
limits, read the prices, the last day and the hourly and daily candles of the allowed products, check the news of the
day when something moved, check the exit conditions of the positions opened before, then at most one order with its
reason and its exit (a stop and a target), or none and why — and ends in a four-line summary for the next shift. Its
runs go on in one chat with the last three summaries in view; with `products = ["*"]` in `USDC` and the shift as
`wake_schedule`, it covers the whole market and is woken between its hours when a product moves. After a couple of
weeks, Settings › Trading shows what it made of the paper account since its start; set beside what simply holding
would have made, that, not a feeling, is what a switch to live rests on. Since item 818 the owner's Dot runs the trend engine
instead (below), with a run once a day that supervises it.

## The trend engine: a rule instead of a model (item 818)

What was measured on the owner's Dot before it (2026-10-28 and 29): the hourly paper shift read about 510,000 tokens a
run, about 0.16 USD of DeepSeek's at OpenRouter's price, so some 115 USD a month for a paper account of 1,000; its first
day's three trades were all stopped out. What the evidence on crypto holds up best once costs are counted is
time-series momentum traded seldom; the live contests of language models trading (Nof1's Alpha Arena, 2025: four of
six models lost) and the studies of them (StockBench, LiveTradeBench) find buy-and-hold hard to beat.

So `[trading] engine = "trend"` trades `engine_coins` (BTC, ETH and SOL in `quote` by default) with no model at all, by
a rule written down before it was tried (`dotagent/trading/trend.py`): a coin is held while its daily close is over its
close 20, 50, 100 and 200 days before (the share of the four that are), sized so that it moves the account as a coin of
`engine_target_vol` (50%) yearly volatility would, an equal share for each coin, and cash when its closes fall. Long or
out, never short, never on credit. A share less than `engine_band` (10 points) off its target stays, as every trade pays
its fee twice over a round trip; a trade the band starts goes on to the target, within a point of it, over as many looks
as the limits take (item 819), as the backtest's does in one step. Once an hour the daemon's scheduler reads the daily
closes (only the days that ended), the shares the rule wants and what the paper account holds, and places the steps
toward them through the same limits as a run's order, the emergency stop included: no order over `max_order`, no day
over `max_day`, no coin over `max_position`, so that with the default limits the engine reaches its shares over days.
Each of `engine_coins` in `quote` must be allowed by `products` too (named there, or trading `min_volume` a day with
`*`): one that is not is refused at every look. A sell names what it brings, so that a bid a little higher when it is
placed cannot take it over `max_order`; a coin Coinbase gave fewer than 201 daily closes of is not traded at that look
(one held is kept, not sold for want of closes). What held an order back is said on the page. It trades on paper only:
an order of your Coinbase account by the engine waits for an item of its own (the live exits of item 770 come first) and
your word.

`dotx trading backtest [--years 7] [--cost 0.007]` plays the same code over Coinbase's daily closes, each side of a
trade costing `--cost` (the fee and the slippage), against holding the first coin. On BTC, ETH and SOL in USD from July
2019 to October 2026 at 0.7% a side: the rule made 26.5% a year (Sharpe 0.90, deepest fall -38%) against holding
Bitcoin's 32.0% (Sharpe 0.77, deepest fall -77%), turning the account over 5.4 times a year. It made less than holding
in the years Bitcoin rose most (2020, 2023, 2024) and lost far less in the years it fell (2022: -23% against -64%). A
band of 5 points made 22.1% at a turnover of 12.5; a volatility of 30% made 21.1% with a deepest fall of -24%: the
numbers move with the settings, none of which was chosen for its result. Past closes are no promise.

The page shows the engine under Trend engine: its switch (on from the Mac only, off from a phone too), the shares the
rule wants beside those held at its last look, what held an order back, and why it is paused; and what the account made
since the engine's first look on it beside what holding the first of `engine_coins` would have made over the same time
(item 819: kept from that look, and again when the paper account is started again; it is the whole paper account's, a
run's paper orders too). That line, after some weeks, is what a switch to live rests on. A run has `trading_engine`:
what the engine holds, wants, did and made, and pause and resume, each with its reason; a run is told never to trade the
engine's coins itself nor change its rule, and to pause it only for what its daily closes cannot show (an exchange
halted, a coin delisted or hacked). The owner's daily supervisor is such a run, once a day.

## What holds, and what only asks

Held in code for every order of Dot's trading tools, in every approval mode, yolo's too (`dotagent/trading/limits.py`,
`service.py`):

- an order of a product that is not allowed (with `*`, one not priced in `quote` or that traded under `min_volume` over
  the last day, as Coinbase's product says when the order is judged), under Coinbase's least order, over `max_order`,
  over what `max_day` leaves today, a buy after `max_day_loss`, a buy that takes one product past `max_position`, or one
  the account cannot pay, is refused before any market sees it;
- every order is judged and placed under one lock, so two runs never judge against the same day, and it is kept before
  it reaches the market, so it counts at once; an order sent with no answer of what it did stays counted (`unknown`);
- a day's start, which the day's loss is counted from, is the account's worth at its first whole look of the day: while
  the price of a currency held cannot be read (Coinbase out of reach for it, or more than 100 currencies held), no buy
  is placed, a sell still is, and the account says so; a currency Coinbase has no product of in the quote is worth
  nothing to the account at every look alike and holds nothing (a product that answers that it does not exist is
  checked against Coinbase's list of what it trades: one on the list is a price not read this time; without the list,
  none is taken at its word);
- the `STOP` file stops every order, an exit's sell too, as it stops the desktop;
- every buy of a run names its stop, kept as an exit the daemon sells at (above);
- an order is placed on the account it was approved for and judged against that account's day and holdings, the
  settings read once: one approved while the account was paper is not placed if you switched to live while it waited,
  nor the reverse; and an order Coinbase took stays counted even when what it did cannot be read afterwards (the key
  refused or removed);
- no tool withdraws or transfers, and a key that may is refused.

What asks (the policy, [Safety](safety.md#safety)): a `live` order carries `trade`, so it waits for your yes in every
mode but `yolo`, never the review model's or a rule's to answer, and Dot places it on your yes; a `yolo` run asks
nothing within the limits, but one that has read outside content (a page, a mail, a post) has the review model check
each live order first (item 525), against an instruction planted to make it trade. A `paper` order moves no money and is
not asked about.

What does not hold: the limits bind Dot's trading tools, not every program on your Mac. A run that runs code of its own
with your rights (a script it wrote, in `yolo` where nothing is asked) can ask the Keychain for the key as it can for
Dot's other keys ([Safety](safety.md#safety), "What keeps a value from a run, and what does not") and trade past the
limits; so can a run that drives coinbase.com in a browser where you are signed in. The shell guard refuses the
`security` command and asks about the name `dot-vault`, and the prompt tells a run to trade only with `trading_order`,
but neither is a wall. A question of macOS at each read of the key would be one, and would ask you at every order: it
is not made. A key limited to a portfolio that holds only what Dot may lose is the bound that holds.

What it does not do: it does not decide for you whether to trade, and its trades can lose money. Limit and stop orders,
derivatives and other exchanges are not in it; Coinbase's own sandbox answers with set replies, so `paper` is Dot's own
account at the real prices instead.
