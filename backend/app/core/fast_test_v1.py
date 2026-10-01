"""FastTest **V1.0** — the debug strategy plus a validation / profit-booking layer.

This module is deliberately a *copy* of the existing FastTest debug strategy
(``FastTestStrategyService`` in ``app/core/strategy.py``) with new trade
management rules on top. The original ``FastTest`` is not touched in any way:
its entry rule, its stop plan and its results stay exactly as they were.

What is identical to FastTest
-----------------------------
* Entry logic — RSI(14) on the 1h candles, long when RSI < 50, short when
  RSI >= 50, every bar, no other filter (no EMA, no Bollinger, no volatility
  or market-phase filter).
* Initial stop loss, trailing stop loss, breakeven, timeout (``MH``) and the
  post-exit cooldown — all from the same ``PhantomV2Config`` plan the engine
  and the paper / live workers already use.
* Position sizing, leverage, fees, FIFO booking, mark-price handling.

What V1.0 adds (only this)
--------------------------
1. **+0.90% profit booking (touch-based).** The moment price touches
   ``entry × 1.0090`` (long) / ``entry × 0.9910`` (short) the whole position is
   booked at that level. Checked on every live tick and, in a backtest, against
   the candle's high/low — so the wick that would have filled a resting limit
   order is honoured.

2. **2-hour validation (close-based).** After entry, the first two *completed*
   1h candles are observed. If either of their closes is at least +0.35% in
   favour (long: ``close >= entry × 1.0035``, short mirrored) the trade is
   marked ``VALIDATED`` and simply continues with the existing exits. If the
   second close is not there yet, the trade exits **at that 2h close**
   (``VALFAIL``).

Priority, exactly as specified::

    ENTRY → +0.90% price touch?  → BOOK PROFIT
          → otherwise 2h close ≥ +0.35%? → CONTINUE (validated)
          → otherwise → EXIT at the 2h close

Within one candle the resting stop keeps priority over the booking rule (the
engine's existing worst-case assumption: if a bar pierces both, the stop is
booked, never the better outcome). The +0.90% booking is evaluated *before*
the trailing stop / take-profit / timeout steps, so a profit touch and a
timeout on the same candle books the profit.

Audit fields
------------
Every closed V1 trade carries the seven audit fields the spec asks for, on the
backtest trade log, the CSV export and the paper / live history:

``validation_status``, ``validation_close``, ``validation_threshold``,
``tp090_hit``, ``validation_exit``, ``final_exit_reason``, ``final_net_pnl``.

Net P&L is always after the applicable entry and exit fees (the engine and the
workers already subtract them; V1 adds no fee of its own).
"""
from typing import Optional

import numpy as np
import pandas as pd
from pydantic import Field

from .indicators import compute_indicators
from .strategy import FastTestConfig, FastTestStrategyService, PhantomV2Config, StrategyService
from ..services.order_manager import OrderManager, _flag
from .fast_test_rules import FastTestOrderManager

#: Strategy id used by the API / dropdowns. Kept separate from ``FastTest``.
FAST_TEST_V1_ID = "FastTestV1"
FAST_TEST_V1_NAME = "Fast Test Strategy V1.0"
#: Short label used on the trade log when the run has no Phantom metadata.
FAST_TEST_V1_LABEL = "FASTTEST V1"

#: Exit reason codes introduced by the V1 layer (never used by other strategies).
REASON_PROFIT_BOOK = "TP090"     # +0.90% touch
REASON_VALIDATION_FAIL = "VALFAIL"  # 2h close never reached +0.35%

#: Validation status values recorded on every trade (see ``strategy_audit_fields``).
STATUS_PENDING = "PENDING"
STATUS_VALIDATED = "VALIDATED"
STATUS_FAILED = "FAILED"
STATUS_TP_HIT = "TP_090_HIT"
STATUS_NOT_REACHED = "NOT_REACHED"


def is_fast_test_v1(strategy_id) -> bool:
    """True for the V1 strategy id (and nothing else)."""
    return str(strategy_id or "").strip().lower() in (FAST_TEST_V1_ID.lower(), "fasttest_v1")


def fast_test_v1_name(strategy_id) -> Optional[str]:
    return FAST_TEST_V1_NAME if is_fast_test_v1(strategy_id) else None


class FastTestV1Config(FastTestConfig):
    """FastTest's config plus the three V1.0 rule parameters.

    Everything else — sizing, SL / trailing SL / breakeven, the ATR or price
    Risk & Exit model, cooldown / timeout, fees, mark price, trading windows —
    is inherited unchanged, so the existing parameters cannot drift. Extending
    :class:`~app.core.strategy.FastTestConfig` is also what marks a saved
    strategy as a V1.0 strategy.
    """
    #: Number of completed 1h candles the validation window spans (the spec's 2H).
    validation_bars: int = Field(default=2, ge=1, le=24)
    #: Favourable CLOSE required inside the window (0.0035 = +0.35%).
    validation_close_pct: float = Field(default=0.0035, gt=0.0, le=0.5)
    #: Favourable TOUCH that books the full position (0.009 = +0.90%).
    profit_book_pct: float = Field(default=0.009, gt=0.0, le=0.5)


def fast_test_v1_config(params=None, fees=None) -> FastTestV1Config:
    """Build a V1 config from a request/params object (or from nothing).

    Only fields the config actually declares are copied, so a Phantom params
    block posted by the UI can never inject an unexpected value. The V1 rule
    parameters may be overridden from the params block when a client sends
    them; otherwise the spec defaults (2 bars / 0.35% / 0.90%) apply.
    """
    payload = {}
    if params is not None:
        try:
            dump = params.model_dump() if hasattr(params, "model_dump") else dict(params)
        except Exception:
            dump = {}
        allowed = set(FastTestV1Config.model_fields)
        payload = {k: v for k, v in (dump or {}).items() if k in allowed}
    cfg = FastTestV1Config(**payload)
    if fees is not None:
        taker = float(getattr(fees, "taker_fee_bps", 0.0) or 0.0)
        maker = float(getattr(fees, "maker_fee_bps", 0.0) or 0.0)
        cfg = cfg.model_copy(update={"taker_fee_bps": taker, "maker_fee_bps": maker})
    return cfg


class FastTestV1StrategyService(FastTestStrategyService):
    """Signal service — the FastTest entry rule, copied verbatim.

    Identical to ``FastTestStrategyService``: RSI on the 1h candles, long below
    the long threshold and short at/above the short threshold, one signal every
    bar. The period and both thresholds are configurable per saved strategy
    (shipped defaults 14 / 50 / 50 = the original rule). It exposes no Phantom
    condition metadata, exactly like FastTest, so the trade log never reports
    RSI reversal / MACD conditions this strategy does not evaluate.
    """
    #: Kept for parity with the other debug strategy on the trade log.
    label = FAST_TEST_V1_LABEL

    def __init__(self, config: FastTestV1Config = None):
        super().__init__(config or FastTestV1Config())


def strategy_service_for(config, strategy_id=None):
    """The signal service that belongs to a config (or a strategy id).

    One place decides which entry rule a run uses, so a saved Fast Test / V1.0
    strategy, the built-in ids and a plain Phantom config all resolve without
    the caller having to know the family:

      * ``FastTestV1Config`` / ``FastTestV1`` → the V1.0 service (FastTest's
        entry rule + the validation layer on the order manager),
      * ``FastTestConfig`` / ``FastTest``     → the FastTest debug service,
      * anything else                         → the standard Phantom service.
    """
    if isinstance(config, FastTestV1Config) or is_fast_test_v1(strategy_id):
        return FastTestV1StrategyService(config if isinstance(config, FastTestV1Config)
                                         else FastTestV1Config(**_config_payload(config)))
    if isinstance(config, FastTestConfig) or str(strategy_id) == 'FastTest':
        return FastTestStrategyService(config)
    return StrategyService(config)


def order_manager_for(config, strategy_id=None, oms=None):
    """The order manager that belongs to a config.

    * V1.0 → the validation / +0.90% booking layer (which also carries the
      configurable signal-condition exits, inherited),
    * the plain debug strategy → the configurable signal-condition exits,
    * anything else → the standard Phantom order manager (``oms`` when one was
      supplied by the caller).
    """
    if isinstance(config, FastTestV1Config) or is_fast_test_v1(strategy_id):
        return FastTestV1OrderManager(config)
    if isinstance(config, FastTestConfig) or str(strategy_id) == 'FastTest':
        return FastTestOrderManager(config)
    return oms or OrderManager(config)


def _config_payload(config):
    """The declared fields of a config (or of a params dict)."""
    try:
        dump = config.model_dump() if hasattr(config, 'model_dump') else dict(config or {})
    except Exception:
        dump = {}
    allowed = set(FastTestV1Config.model_fields)
    return {k: v for k, v in (dump or {}).items() if k in allowed}


class FastTestV1OrderManager(FastTestOrderManager):
    """OrderManager + the V1.0 validation / profit-booking layer.

    Extends the debug order manager, so a V1.0 strategy also runs the
    configurable signal-condition exits (opposite signal / RSI / MACD flip)
    on a completed candle — after its own 2H validation verdict.

    The base class keeps every existing exit (SL, trailing SL, take profit,
    timeout) untouched; this subclass only fills in the two optional hooks the
    base class exposes:

    * :meth:`strategy_touch_exit` — touch-based +0.90% profit booking.
    * :meth:`strategy_bar_close_exit` — close-based 2h validation.
    * :meth:`strategy_audit_fields` — the seven audit fields on every close.
    """

    def __init__(self, config: FastTestV1Config = None):
        super().__init__(config or FastTestV1Config())

    # ------------------------------------------------------------------
    # Trade bookkeeping
    # ------------------------------------------------------------------
    def create_order(self, symbol, direction, price_usd, atr_usd, timestamp, margin_inr,
                     conversion_rate=85.0, trade_price_usd=None, mark_price_usd=None,
                     mark_price_basis=None):
        trade = super().create_order(
            symbol, direction, price_usd, atr_usd, timestamp, margin_inr,
            conversion_rate=conversion_rate, trade_price_usd=trade_price_usd,
            mark_price_usd=mark_price_usd, mark_price_basis=mark_price_basis,
        )
        if trade is not None:
            self._v1_init_state(trade)
        return trade

    def _v1_init_state(self, trade):
        """Levels and counters for the freshly opened trade."""
        direction = int(trade.direction)
        sign = 1.0 if direction == 1 else -1.0
        entry = float(trade.entry_price)
        book_pct = float(getattr(self.config, 'profit_book_pct', 0.009) or 0.009)
        val_pct = float(getattr(self.config, 'validation_close_pct', 0.0035) or 0.0035)
        trade.v1_entry = entry
        trade.v1_profit_level = entry * (1.0 + sign * book_pct)
        trade.v1_validate_level = entry * (1.0 + sign * val_pct)
        trade.v1_status = STATUS_PENDING
        trade.v1_closes = 0                    # completed candles observed
        trade.v1_last_bar_time = None          # de-duplicates bar-close calls
        trade.v1_last_close = None
        trade.v1_validation_close = None
        trade.v1_tp_hit = False
        trade.v1_validation_exit = False

    # ------------------------------------------------------------------
    # Hook 1 — +0.90% profit booking (touch)
    # ------------------------------------------------------------------
    def strategy_touch_exit(self, trade, seen_high, seen_low):
        """Book the full position when the +0.90% level is touched.

        ``seen_high`` / ``seen_low`` are the candle's extremes on the pricing
        basis (the live tick price when no candle extremes were supplied), so
        this is a genuine touch test, not a close test.
        """
        level = getattr(trade, 'v1_profit_level', None)
        if level is None:
            return None
        if trade.direction == 1 and seen_high is not None and seen_high >= level:
            trade.v1_tp_hit = True
            return (float(level), REASON_PROFIT_BOOK,
                    f"+0.90% profit booking — price rose to {float(seen_high):,.2f} ≥ "
                    f"{float(level):,.2f} (entry {trade.v1_entry:,.2f} × 1.0090). "
                    f"Full position booked.")
        if trade.direction == -1 and seen_low is not None and seen_low <= level:
            trade.v1_tp_hit = True
            return (float(level), REASON_PROFIT_BOOK,
                    f"+0.90% profit booking — price fell to {float(seen_low):,.2f} ≤ "
                    f"{float(level):,.2f} (entry {trade.v1_entry:,.2f} × 0.9910). "
                    f"Full position booked.")
        return None

    # ------------------------------------------------------------------
    # Hook 2 — 2H close validation
    # ------------------------------------------------------------------
    def strategy_bar_close_exit(self, trade, bar_close_usd, bar_time):
        """Judge a completed 1h candle's close against the 2h validation rule.

        Returns ``None`` while the trade may continue, or ``(price, reason,
        detail)`` when the validation window has ended below the threshold.

        The *first* favourable close inside the window validates the trade and
        it then simply continues with the existing exits. The deadline is the
        close of the ``validation_bars``-th completed candle after entry (the
        second candle = the 2H point); missing it exits at that very close.
        """
        if bar_close_usd is None or bar_time is None:
            return None
        status = getattr(trade, 'v1_status', None)
        if status is None or status in (STATUS_FAILED,):
            return None
        # One judgement per candle, in order — a repeated tick for the same
        # candle (or an out-of-order replay) must never double-count, and the
        # entry candle is the only candle ever seen before the entry.
        last = getattr(trade, 'v1_last_bar_time', None)
        if last is not None and bar_time <= last:
            return None
        entry_time = getattr(trade, 'entry_time', None)
        if entry_time is not None and bar_time < entry_time:
            trade.v1_last_bar_time = bar_time
            return None

        close = float(bar_close_usd)
        trade.v1_last_bar_time = bar_time
        trade.v1_last_close = close
        trade.v1_closes = int(getattr(trade, 'v1_closes', 0) or 0) + 1

        long_side = trade.direction == 1
        level = float(trade.v1_validate_level)
        favourable = (close >= level) if long_side else (close <= level)
        if favourable:
            trade.v1_status = STATUS_VALIDATED
            trade.v1_validation_close = close
            # Validated → the trade continues, and the client's own exit
            # conditions (if any) still get their say on this candle.
            return super().strategy_bar_close_exit(trade, bar_close_usd, bar_time)

        window = int(getattr(self.config, 'validation_bars', 2) or 2)
        if trade.v1_closes >= window:
            trade.v1_status = STATUS_FAILED
            trade.v1_validation_close = close
            trade.v1_validation_exit = True
            pct = float(getattr(self.config, 'validation_close_pct', 0.0035) or 0.0035) * 100.0
            sign = "≥" if long_side else "≤"
            detail = (f"2H validation failed — {trade.v1_closes} completed candle close(s) "
                      f"without a +{pct:.2f}% favourable close: close {close:,.2f} vs required "
                      f"{sign} {level:,.2f} (entry {trade.v1_entry:,.2f}). "
                      f"Exited at the {trade.v1_closes}H candle close.")
            return (close, REASON_VALIDATION_FAIL, detail)
        # Inside the window and nothing fired: the configurable conditions
        # judge this candle's close last.
        return super().strategy_bar_close_exit(trade, bar_close_usd, bar_time)

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------
    def bracket_take_profit(self, direction, price_usd, planned_tp):
        """The venue-side TP is the +0.90% booking level, not the plan's TP.

        The exchange target must be the level this strategy actually books at;
        the protective stop and trailing distance sent with the bracket are
        unchanged.
        """
        if not _flag(self.config, 'use_take_profit'):
            return None
        sign = 1.0 if int(direction) == 1 else -1.0
        pct = float(getattr(self.config, 'profit_book_pct', 0.009) or 0.009)
        try:
            return float(price_usd) * (1.0 + sign * pct)
        except (TypeError, ValueError):
            return planned_tp

    def strategy_summary(self, trades=None):
        """Counters for the run header: validated / failed / booked / skipped."""
        counts = {}
        for t in trades or []:
            status = t.get('validation_status')
            if status:
                counts[status] = counts.get(status, 0) + 1
        booked = sum(1 for t in trades or [] if t.get('tp090_hit'))
        failed = sum(1 for t in trades or [] if t.get('validation_exit'))
        return {
            "rule": "FastTest V1.0 — 2H +0.35% validation, +0.90% touch booking",
            "validation_bars": int(getattr(self.config, 'validation_bars', 2) or 2),
            "validation_close_pct": float(getattr(self.config, 'validation_close_pct', 0.0035) or 0.0035),
            "profit_book_pct": float(getattr(self.config, 'profit_book_pct', 0.009) or 0.009),
            "booked_at_090": booked,
            "validation_failed": failed,
            "status_counts": counts,
        }

    def strategy_audit_fields(self, trade, net_pnl_inr=None):
        """The seven audit fields for a closed (or open) V1 trade.

        ``validation_close`` is the close the rule judged: the close that
        validated the trade, the deadline close that failed it, or the last
        close seen before one of the existing exits closed it early.
        ``validation_threshold`` is the price level the close had to reach
        (``entry × 1.0035`` long / ``entry × 0.9965`` short).
        """
        status = getattr(trade, 'v1_status', None)
        if status is None:
            return {}
        if status == STATUS_PENDING:
            # No close ever reached the +0.35% level: either the +0.90% booking
            # fired first, or one of the existing exits closed the trade before
            # the 2h window was judged.
            status = STATUS_TP_HIT if getattr(trade, 'v1_tp_hit', False) else STATUS_NOT_REACHED
        final_reason = getattr(trade, 'exit_reason', None) or None
        validation_close = getattr(trade, 'v1_validation_close', None)
        if validation_close is None:
            validation_close = getattr(trade, 'v1_last_close', None)
        return {
            "validation_status": status,
            "validation_close": (None if validation_close is None else float(validation_close)),
            "validation_threshold": (None if getattr(trade, 'v1_validate_level', None) is None
                                     else float(trade.v1_validate_level)),
            "tp090_hit": int(bool(getattr(trade, 'v1_tp_hit', False))),
            "validation_exit": int(bool(getattr(trade, 'v1_validation_exit', False))),
            "final_exit_reason": final_reason,
            "final_net_pnl": (None if net_pnl_inr is None else float(net_pnl_inr)),
        }


def completed_bar_close(df_1h, bar_time):
    """``(close, high, low)`` of the candle stamped ``bar_time``, or ``None``.

    Used by the paper / live workers: the 2h validation is judged on the close
    of a *completed* candle, so the worker hands the layer the candle the
    clock just moved past instead of the still-forming one.
    """
    if df_1h is None or bar_time is None:
        return None
    try:
        rows = df_1h[df_1h.index == bar_time]
        if rows.empty:
            return None
        last = rows.iloc[-1]
        return (float(last['close']), float(last['high']), float(last['low']))
    except Exception:
        return None
