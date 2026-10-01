"""Editable entry / exit rules of the Fast Test debug strategies.

The two debug strategies (``FastTest`` and ``FastTestV1``) are the same shell:
a deliberately frequent entry rule (every 1h candle produces a side) on top of
the standard stop / target / trailing / timeout plan. Historically both halves
were hardcoded — the entry rule always read RSI(14) and split it at 50, and
every protective rule was always live.

This module makes both halves *configurable* while keeping the shipped defaults
byte-for-byte identical:

Entry rule (``FastTestConfig`` fields, read by
:class:`~app.core.strategy.FastTestStrategyService`):

  * ``entry_rsi_period``    — the RSI period the rule reads (default 14),
  * ``entry_rsi_long_max``  — LONG while RSI is *below* this value (default 50),
  * ``entry_rsi_short_min`` — SHORT while RSI is *at or above* it (default 50),
  * ``trade_direction``     — the existing side filter (``both`` / ``long`` /
    ``short``); the debug rule now honours it too.

Exit rule — which protective rules exist (read by
:class:`~app.services.order_manager.OrderManager`):

  ``use_stop_loss`` / ``use_take_profit`` / ``use_trailing_stop`` /
  ``use_breakeven`` / ``use_timeout`` — all ``True`` by default, i.e. the plan
  as it always ran.

Exit rule — optional signal conditions, all OFF by default and judged on a
*completed* candle's values, after that candle's stop / target:

  * ``exit_on_opposite``      — the entry rule now points the other way,
  * ``exit_rsi_enabled`` + ``exit_rsi_level`` — RSI crossed back through the
    level (long exits at/above it, short at/below it),
  * ``exit_macd_flip_enabled`` — the MACD line crossed its signal line against
    the position.

Nothing here is read by the Kudos / Phantom strategy (its config does not
declare the fields, and every read goes through ``getattr(..., default)``), so
Phantom runs are untouched.
"""

import numpy as np

from ..services.order_manager import OrderManager
from .indicators import compute_indicators

# Exit reasons recorded on the trade (short codes, like SL / TSL / TP / MH).
REASON_OPPOSITE = "OPP"
REASON_RSI_EXIT = "RSIX"
REASON_MACD_FLIP = "MFLIP"


def fast_test_sides(config):
    """``(allow_long, allow_short)`` from the existing ``trade_direction`` field."""
    direction = str(getattr(config, "trade_direction", "both") or "both").strip().lower()
    if direction in ("long", "long_only", "buy"):
        return True, False
    if direction in ("short", "short_only", "sell"):
        return False, True
    return True, True


def entry_rsi_period(config) -> int:
    try:
        return max(2, int(getattr(config, "entry_rsi_period", 14) or 14))
    except (TypeError, ValueError):
        return 14


def entry_rsi_long_max(config) -> float:
    try:
        return float(getattr(config, "entry_rsi_long_max", 50.0))
    except (TypeError, ValueError):
        return 50.0


def entry_rsi_short_min(config) -> float:
    try:
        return float(getattr(config, "entry_rsi_short_min", 50.0))
    except (TypeError, ValueError):
        return 50.0


def exit_conditions_configured(config) -> bool:
    """True when at least one signal-condition exit is switched on."""
    return bool(getattr(config, "exit_on_opposite", False)
                or getattr(config, "exit_rsi_enabled", False)
                or getattr(config, "exit_macd_flip_enabled", False))


def _number(value):
    """A finite float, or ``None`` for None / NaN / inf / rubbish."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if np.isfinite(out) else None


def fast_test_bar_state(df_1h, bar_time, config):
    """``{'rsi', 'macd_line', 'macd_signal'}`` for the candle stamped ``bar_time``.

    Used by the paper / live workers: the signal conditions are judged on a
    *completed* candle, so the worker hands this the candle the clock just moved
    past (the same one whose close the V1 validation layer reads). Returns
    ``None`` when no condition is configured — nothing is computed, and the
    order manager skips the whole check.
    """
    if df_1h is None or bar_time is None or not exit_conditions_configured(config):
        return None
    try:
        ind = compute_indicators(
            df_1h,
            macd_fast=int(getattr(config, "macd_fast", 12) or 12),
            macd_slow=int(getattr(config, "macd_slow", 26) or 26),
            macd_signal=int(getattr(config, "macd_signal", 9) or 9),
            rsi_period=entry_rsi_period(config),
        )
        index = getattr(df_1h, "index", None)
        positions = np.where(np.asarray(index) == bar_time)[0]
        if len(positions) == 0:
            return None
        i = int(positions[-1])
        return {
            "rsi": float(ind["rsi14"][i]),
            "macd_line": float(ind["macd_line"][i]),
            "macd_signal": float(ind["macd_signal"][i]),
        }
    except Exception:
        # A condition must never be able to break a live position's price
        # management: without a state the check is simply skipped this candle.
        return None


def fast_test_exit_condition(trade, state, config):
    """The configured signal-condition exit for one candle.

    Returns ``(reason, detail)`` or ``None``. The order is fixed and explained
    to the client in the panel: opposite signal → RSI level → MACD flip.

    Judged on the candle the state describes (its own RSI / MACD values), so a
    condition fires on that candle's close — never intra-candle, never on a
    still-forming bar.
    """
    if not state:
        return None
    direction = int(getattr(trade, "direction", 0) or 0)
    if direction not in (1, -1):
        return None
    long_side = direction == 1
    rsi_v = _number(state.get("rsi"))

    # 1. The entry rule now points the other way.
    if getattr(config, "exit_on_opposite", False) and rsi_v is not None:
        now = 1 if rsi_v < entry_rsi_long_max(config) else (
            -1 if rsi_v >= entry_rsi_short_min(config) else 0)
        if now and now != direction:
            side = "LONG" if now == 1 else "SHORT"
            return (REASON_OPPOSITE,
                    f"Opposite signal — the entry rule now points {side} "
                    f"(RSI {rsi_v:.2f}); closed the {('LONG' if long_side else 'SHORT')} position.")

    # 2. RSI crossed back through the exit level.
    if getattr(config, "exit_rsi_enabled", False) and rsi_v is not None:
        try:
            level = float(getattr(config, "exit_rsi_level", 50.0))
        except (TypeError, ValueError):
            level = 50.0
        if (long_side and rsi_v >= level) or ((not long_side) and rsi_v <= level):
            sign = ">=" if long_side else "<="
            return (REASON_RSI_EXIT,
                    f"RSI exit — RSI {rsi_v:.2f} {sign} the exit level {level:.2f}.")

    # 3. MACD line crossed its signal line against the position.
    if getattr(config, "exit_macd_flip_enabled", False):
        line_v = _number(state.get("macd_line"))
        sig_v = _number(state.get("macd_signal"))
        if line_v is not None and sig_v is not None:
            flipped = (line_v < sig_v) if long_side else (line_v > sig_v)
            if flipped:
                sign = "<" if long_side else ">"
                return (REASON_MACD_FLIP,
                        f"MACD line exit — line {line_v:,.2f} {sign} signal {sig_v:,.2f}.")
    return None


class FastTestOrderManager(OrderManager):
    """OrderManager + the configurable signal-condition exits.

    Every other exit stays where it always was (the base class); this subclass
    only fills the close-based hook with the client's conditions. The candle's
    values arrive through ``self._bar_state``, which
    :meth:`~app.services.order_manager.OrderManager.update_trade` sets for every
    call — a strategy without conditions never sees a state.
    """

    def strategy_bar_close_exit(self, trade, bar_close_usd, bar_time):
        if bar_close_usd is None:
            return None
        rule = fast_test_exit_condition(trade, getattr(self, "_bar_state", None), self.config)
        if rule is None:
            return None
        reason, detail = rule
        return (float(bar_close_usd), reason, detail)
