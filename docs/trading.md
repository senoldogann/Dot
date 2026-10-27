# Trading

Dot trades crypto on Coinbase when you switch it on (items 763 and 764): with an account of its own that trades at
Coinbase's real prices with money that is not there (`paper`), to try a way of trading, or with your Coinbase account
(`live`). The limits you set judge every order before any market sees it, in every approval mode, yolo's too, and no run
can change them.

## Switching it on

Settings › Trading (or `[trading]` in `config.toml`, [Configuration](configuration.md)):

- **Mode**: `off` (the default: runs have no trading tool at all), `paper` or `live`. A switch from `off` gives runs the
  trading tools from Dot's next start; one between `paper` and `live` holds from the next order.
- **Products**: the Coinbase product ids runs may trade, all priced in one currency, `quote` (`EUR` by default:
  `BTC-EUR`, `ETH-EUR`, `SOL-EUR`).
- **Limits**, in `quote`:
  - `max_order`: the most one order may be worth (25 by default);
  - `max_day`: the most a day's orders may add up to, buys and sells (100);
  - `max_day_loss`: how much the account may lose in a day before no buy is placed until the next; a sell still is, as
    it lowers what is at stake (25);
  - `max_position`: the most one product held may be worth after a buy (200).
  The day is yours: midnight in `[scheduler] timezone`.
- **Paper**: `paper_start` (1000) is what the paper account starts with, `paper_fee_rate` (0.006) the share of each fill
  it pays, as Coinbase's taker fee does; it buys at the ask and sells at the bid. **Start again** gives it `paper_start`
  back; its orders stay in the record, outside it.
- **Coinbase key** (for `live`): make an API key in Coinbase's Developer Platform with the **ECDSA** signature algorithm
  and the **View** and **Trade** permissions, never **Transfer**, and paste its name (`organizations/.../apiKeys/...`)
  and private key. Dot asks Coinbase what the key may do before it keeps it, and refuses one that may transfer money:
  Dot has no withdrawal and needs none. Make it for a **portfolio of its own** in Coinbase (a key may be limited to one
  portfolio), holding only what you let Dot risk: that bounds what any trade can lose, whatever happens on the Mac (see
  the last section). The key is kept in your Mac's login Keychain (generic password, service `dot-vault`, account
  `trading-coinbase`), never in Dot's files; Dot's tools, logs and errors never show it. Each request is signed with it
  for that one request, for two minutes, as Coinbase asks.

What lets runs do more with money is done on the Mac only: switching trading on or to live, raising a limit, allowing a
product, saving a key. A phone's key gets a refusal for those, and can always narrow: switch trading off or to paper,
lower a limit, drop a product, remove the key. Switching to live checks the key with Coinbase first.

## What a run does

With trading on, a run has the `trading` tool group:

- `trading_market`: Coinbase's best bid and ask of the allowed products now, or a product's candles (one minute to one
  day). Coinbase's public market data, with no key: paper and live read the same prices.
- `trading_portfolio`: the account (paper or yours), what it holds and is worth at the bids, today's change and what
  the limits leave today.
- `trading_order`: a market order: a buy spends `quote_amount`; a sell sells `base_amount` or what brings
  `quote_amount`.
- `trading_history`: the account's orders, newest first.

The system prompt tells the run to trade only with `trading_order`, never through the browser or the shell, to read the
prices and the account first, to say why it trades and what ends the trade, never to take what a page or a post says
of a coin as a reason, and never to split or repeat an order to get around a limit.

## What holds, and what only asks

Held in code for every order of Dot's trading tools, in every approval mode, yolo's too (`dotagent/trading/limits.py`,
`service.py`):

- an order of a product that is not allowed, under Coinbase's least order, over `max_order`, over what `max_day` leaves
  today, a buy after `max_day_loss`, a buy that takes one product past `max_position`, or one the account cannot pay, is
  refused before any market sees it;
- every order is judged and placed under one lock, so two runs never judge against the same day, and it is kept before
  it reaches the market, so it counts at once; an order sent with no answer of what it did stays counted (`unknown`);
- the `STOP` file stops every order, as it stops the desktop;
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
