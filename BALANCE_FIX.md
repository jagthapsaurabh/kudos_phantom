# Balance Display & Margin Release Fix

## Problem Statement

The balance panel on the Live Trading page showed:

- **Wallet balance**: $27.29
- **Available**: $16.31
- **Used margin**: $0.00
- **Unrealised PnL**: —
- **Total**: —

`GET /live-account/balance` answered with the same picture:

```json
{
  "state": "ok", "broker": "Delta", "connection_id": 2, "testnet": false,
  "asset": "USD",
  "wallet_balance": 27.28607683,
  "available_balance": 16.31092465171727,
  "used_margin": 0.0, "order_margin": 0.0, "position_margin": 0.0,
  "unrealized_pnl": null, "total": null, "commission": 0.0,
  "balances": [{ "asset": "USD", "balance": 27.28607683,
                 "available": 16.31092465171727, "order_margin": 0.0,
                 "position_margin": 0.0, "commission": 0.0 }]
}
```

$10.98 of a $27.29 wallet was unavailable and nothing on screen accounted for
it, while two fields that should describe the account came back `null`.

## Root Cause

### 1. Delta blocks margin per MODE, and we only read one mode

`GET /v2/wallet/balances` returns a Wallet row per asset. Its margin fields are
**not** three numbers — they are three *sets*, one per margin mode:

| Bucket | Fields |
| :--- | :--- |
| Isolated | `order_margin`, `position_margin`, `commission` |
| Cross | `cross_order_margin`, `cross_position_margin`, `cross_commission`, `cross_locked_collateral` |
| Portfolio | `portfolio_margin` |
| **All modes** | **`blocked_margin`** — "Total blocked margin including commissions for all modes" |

and the venue's own identity is

```
available_balance = balance − blocked_margin
balance           = deposits − withdrawals + realised cashflows
```

This account trades in **cross** margin mode (the connection's saved
`account_settings.margin_mode` says `cross`, and Delta's own rejections on it
came back with `"margin_mode": "cross"`). On a cross-margin account every
isolated field is `0`, so the old normalizer — which summed
`order_margin + position_margin + commission` — reported `used_margin: 0.0`
while $10.98 sat in `cross_position_margin` + `cross_commission`, fields
nobody read.

> **Note on the first attempt at this fix.** An earlier revision of this
> document blamed the gap on `commission` and added it to `used_margin`. That
> was the right instinct and the wrong bucket: `commission` is *isolated-mode*
> commission only ("Commissions blocked in Isolated Mode"), so on this account
> it is `0.0` and the gap survived the fix — which is exactly what the payload
> above shows. It stays in the sum (it is one of the isolated buckets), it just
> was never the whole answer.

### 2. `unrealized_pnl` / `total` were Binance-only fields

`normalize_balance` filled `unrealized_pnl` and `total` in the Binance branch
(from `totalUnrealizedProfit` / `totalMarginBalance`) and left both `None` for
Delta. Delta does answer with the equivalent — `meta.net_equity` on the same
`/v2/wallet/balances` response — but the client's `_delta_result()` unwraps the
envelope and throws `meta` away, so the panel had nothing to show.

### 3. Delta positions carry no unrealised PnL

Delta's Position schema has `size`, `entry_price`, `margin`,
`liquidation_price`, `commission`, `realized_pnl`, `realized_funding` — no
`unrealized_pnl`. `portfolio_risk()` summed the per-position figure, so equity
read as the bare wallet and margin utilisation as 0% even with an open trade.

### 4. Stop did not release margin

Stopping a live trade stopped the worker and saved history, but cancelled no
orders and closed no positions, so the venue kept blocking margin after the
instance was gone. (Still true and still fixed — see Fix 4.)

## Solution

### Fix 1: read every margin bucket, prefer the venue's own total (`broker_account.py`)

`_delta_margin_breakdown()` maps one wallet row onto per-mode figures:

```python
DELTA_ISOLATED_FIELDS  = ("order_margin", "position_margin", "commission")
DELTA_CROSS_FIELDS     = ("cross_order_margin", "cross_position_margin",
                          "cross_commission", "cross_locked_collateral")
DELTA_PORTFOLIO_FIELDS = ("portfolio_margin",)
```

`used_margin` is now `blocked_margin` when the venue sends it, otherwise the sum
of all three modes, and the response names which of the two it is
(`blocked_margin_reported` is `null` when we had to sum). It also carries
`margin_mode` (`isolated` / `cross` / `portfolio` / `mixed`), the per-mode
subtotals (`isolated_margin`, `cross_margin`, `portfolio_blocked`) and every
individual bucket, per asset as well as for the primary one.

`margin_mode` is inferred from the buckets that are actually holding cash, and
`margin_mode_source` says so (`blocked_margin`). A **flat** wallet blocks
nothing and therefore cannot answer which mode the account trades in, so
`GET /live-account/balance` falls back to the connection's cached
`account_settings.margin_mode` (`margin_mode_source: "connection_settings"`) —
read from the database, so the 30-second balance poll costs no API weight. The
terminal's caption does the same from the snapshot's `account_settings`.

### Fix 2: reconcile instead of trusting (`broker_account.py`)

```
reserved_margin      = wallet_balance − available_balance      # what the venue withheld
unattributed_margin  = reserved_margin − used_margin           # what we cannot name
balances_reconciled  = |unattributed_margin| ≤ 0.01
```

Both venues run through it (Binance adds its open-order margin, which
`availableBalance` has already paid for but `used_margin` does not include). If
Delta ever withholds cash for a reason this schema does not know about — a
pending withdrawal, a spot order, a new bucket — the panel says so instead of
quietly reporting money as free. That is the failure mode that made this look
like a display bug for so long.

### Fix 3: equity and PnL for Delta (`broker_client.py`, `broker_account.py`, `main.py`)

`get_account_balance()` keeps the envelope's `meta` on the client
(`last_balance_meta`) before unwrapping, `normalize_balance(..., meta=...)`
turns `net_equity` into `total` / `net_equity` and derives
`unrealized_pnl = net_equity − wallet_balance`, and both
`GET /live-account/balance` and `account_snapshot()` pass it through — no extra
signed call. `portfolio_risk()` uses the venue's equity when it reports one and
falls back to the account-level PnL when positions report none (Delta).

### Fix 4: cancel orders & close positions on stop (`main.py`)

`/live-trade/stop` cancels every open order (releasing order margin) and closes
every open position (releasing position margin and the commission blocked
against closing it), logging a warning and continuing if the venue refuses.

### Fix 5: the panels say where the money is (`LiveTrade.jsx`, `LiveTerminal.jsx`)

`BalancePanel` lists whichever buckets actually hold cash — on this account
"Cross position margin $10.87" and "Cross commission $0.11" — plus
"Reserved (unattributed)" when something cannot be named, and the footer reads
`USD · Delta · cross margin`. The terminal's Wallet & Margin panel shows the
margin mode with the per-mode blocked totals, and an amber line when the venue
withholds cash no bucket explains.

## Verification

The same account now reads:

```
wallet_balance      27.28607683
available_balance   16.31092465171727
used_margin         10.97515217828273     ← was 0.0
margin_mode         cross
reserved_margin     10.97515217828273
unattributed_margin 0.0
balances_reconciled true
total / net_equity  27.9                  ← was null
unrealized_pnl      0.61392317            ← was null

27.28607683 = 16.31092465171727 + 10.97515217828273  ✓
```

Margin utilisation on the risk panel goes from 0% to the real ~39% for the same
open position.

Regression coverage:

- `backend/test_live_account.py` (272 checks) — cross / isolated / mixed /
  portfolio wallets, `blocked_margin` preferred over the component sum, the sum
  used when `blocked_margin` is absent, unattributed cash reported rather than
  hidden, `meta.net_equity` → `total` + `unrealized_pnl`, Binance
  reconciliation, risk utilisation per mode, a flat wallet reconciling at zero
  with the mode taken from the connection's cached settings, and the HTTP
  endpoint + snapshot against a mock exchange that now answers like a real
  cross-margin account.
- `frontend` (`npm test`, 380 checks) — `BalancePanel` names the cross buckets,
  hides empty ones, shows the margin mode, and surfaces unattributed cash; the
  terminal's Wallet & Margin panel does the same and falls back to the
  snapshot's configured mode when the wallet is flat.

## Impact

### Before
- ❌ "Used margin $0.00" next to an available balance $10.98 below the wallet
- ❌ Cross-margin accounts looked flat: 0% utilisation, no equity, no PnL
- ❌ `unrealized_pnl` / `total` always `null` on Delta
- ❌ Stopping a strategy left margin blocked on the exchange

### After
- ✅ `used_margin` is whatever the venue blocks, in every mode
- ✅ The panel names the bucket holding the cash, and says when it cannot
- ✅ Equity and unrealised PnL come from the venue's own `net_equity`
- ✅ Risk percentages are computed on real equity and real blocked margin
- ✅ Stopping a strategy cancels orders and flattens positions

## Files Modified

### Backend
- `backend/app/services/broker_account.py` — per-mode margin buckets, `blocked_margin`,
  reconciliation, `meta.net_equity`, risk on venue equity
- `backend/app/services/broker_client.py` — keep the wallet envelope's `meta`
- `backend/app/main.py` — pass `meta` into the balance endpoint; cancel orders &
  close positions on stop
- `backend/test_live_account.py` — cross-margin mock wallet + 14 new checks

### Frontend
- `frontend/src/pages/LiveTrade.jsx` — per-bucket balance rows, margin mode,
  unattributed cash, updated stop confirmation dialog
- `frontend/src/components/LiveTerminal.jsx` — margin mode + per-mode blocked
  totals, unattributed-margin warning
- `frontend/tests/broker_keys_ui.jsx`, `frontend/tests/terminal_ui.jsx` — coverage

### Docs
- `api_docs.md` — `GET /live-account/balance` response contract
- `docs/delta_guardrails_audit.md` — `blocked_margin` row
- `README.md` — test counts

## Notes

- Margin bucketing is Delta-specific. Binance reports account-level
  `totalPositionInitialMargin` / `totalOpenOrderInitialMargin` and reconciles
  through the same `reserved_margin` / `unattributed_margin` fields.
- `used_margin` now means "everything the venue is holding back". Anything that
  treated it as isolated position margin specifically should read
  `position_margin` / `cross_position_margin` instead.
- Margin pre-flight sizing (`services/margin_preflight.py`) was already reading
  `available_balance`, which is the venue's own post-`blocked_margin` figure —
  so affordability checks were right all along; only the display and the risk
  percentages were blind.
- Paper trade stop was already correct (no real money at risk).
