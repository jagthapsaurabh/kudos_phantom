"""Persistent history for paper-trading sessions.

``PaperTradeService`` instances live in a process-memory dict, so a stopped (or
server-restarted) session used to lose its trades, logs and equity curve. This
module mirrors each session into the ``paper_sessions`` table:

* :func:`start_session`  — insert the row when an instance is created
* :func:`persist_snapshot` — refresh equity, closed trades, logs, equity curve
* :func:`finalize_session` — same, but stamp ``stopped_at`` and the end status
* :func:`delete_record`  — purge one saved session (History → Delete)
* :func:`mark_interrupted_sessions` — flag rows left ``running`` by a restart

Every function is defensive: a database problem is logged and swallowed so the
trading loop itself never dies because of bookkeeping.
"""
import json
from datetime import datetime, timezone

from app.database.models import SessionLocal, PaperSession

STATUS_RUNNING = 'running'
STATUS_STOPPED = 'stopped'
STATUS_INTERRUPTED = 'interrupted'

# Only the tail of the log buffer is worth keeping per session.
MAX_SAVED_LOG_LINES = 500


def _utc_now() -> datetime:
    return datetime.utcnow()


def _parse_ist(value):
    """Convert a backend IST ISO string (or datetime) to naive UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None \
            else value.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _f(value):
    """Coerce numpy/pandas scalars to plain floats (JSON-safe)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize(closed_trades, initial_capital=None, final_equity=None, equity_curve=None):
    """Roll up a session's closed trades into headline numbers."""
    closed = [t for t in (closed_trades or []) if isinstance(t, dict)]
    count = len(closed)
    pnls = [_f(t.get('pnl')) or 0.0 for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = sum(losses)
    if gross_loss > 0:
        profit_factor = gross_win / gross_loss
    else:
        profit_factor = 99.0 if gross_win > 0 else 0.0
    net_pnl = sum(pnls)
    fees = sum([_f(t.get('fees')) or 0.0 for t in closed])
    initial = _f(initial_capital)
    final = _f(final_equity)
    roi = ((final - initial) / initial * 100.0) if (initial and final is not None) else None

    # Peak / max drawdown from the equity curve when available, else from the
    # capital + net PnL of the closed trades.
    curve_points = [_f(p.get('equity')) for p in (equity_curve or []) if isinstance(p, dict)]
    series = [v for v in curve_points if v is not None]
    if not series and initial is not None:
        series = [initial, initial + net_pnl]
    peak = max(series) if series else None
    max_dd = None
    if series:
        running_peak = series[0]
        max_dd = 0.0
        for value in series:
            running_peak = max(running_peak, value)
            if running_peak > 0:
                max_dd = max(max_dd, (running_peak - value) / running_peak * 100.0)
    return {
        'closed_trade_count': count,
        'win_rate': round(len(wins) / count * 100.0, 2) if count else 0.0,
        'profit_factor': round(profit_factor, 2),
        'net_pnl': round(net_pnl, 2),
        'total_fees': round(fees, 2),
        'roi': round(roi, 2) if roi is not None else None,
        'peak_equity': round(peak, 2) if peak is not None else None,
        'max_drawdown_pct': round(max_dd, 2) if max_dd is not None else None,
    }


def _open_positions(service):
    """Snapshot of the positions still open when the session ended."""
    positions = []
    try:
        for symbol, trade in service.oms.active_trades.items():
            current = _f(getattr(service, 'last_price', None)) or _f(trade.entry_price)
            entry = _f(trade.entry_price) or 0.0
            direction = int(trade.direction)
            lots = _f(trade.lots) or 0.0
            positions.append({
                'symbol': symbol,
                'direction': direction,
                'entry': entry,
                'current': current,
                'pnl': ((current - entry) * direction * lots * _f(service.conversion_rate or 85.0)),
                'entry_time': getattr(trade, 'entry_time', None).isoformat()
                if isinstance(getattr(trade, 'entry_time', None), datetime) else str(getattr(trade, 'entry_time', None)),
                'bars_held': int(getattr(trade, 'bars_held', 0) or 0),
                'sl': _f(getattr(trade, 'sl', None)),
                'tp': _f(getattr(trade, 'tp', None)),
                'trail_stop': _f(getattr(trade, 'trail_stop', None)),
                'lots': lots,
                'margin_inr': _f(getattr(trade, 'margin_inr', None)),
                'unrealised': True,
            })
    except Exception as exc:  # pragma: no cover - snapshot is best effort
        print(f"[paper-history] open-position snapshot failed: {exc}")
    return positions


def _payload(service, status=None):
    """Build the column values for one session snapshot."""
    closed = list(getattr(service, 'closed_trades', []) or [])
    curve = list(getattr(service, 'equity_history', []) or [])
    initial = _f(getattr(service, 'initial_capital_inr', None))
    equity = _f(getattr(service, 'equity_inr', None))
    stats = summarize(closed, initial, equity, curve)
    config = getattr(service, 'config', None)
    try:
        config_json = config.model_dump_json() if hasattr(config, 'model_dump_json') \
            else (config.json() if hasattr(config, 'json') else None)
    except Exception:
        config_json = None
    values = {
        'status': status or getattr(service, 'history_status', STATUS_RUNNING),
        'symbol': getattr(service, 'symbol', None) or 'BTCUSDT',
        'strategy_id': str(getattr(service, 'strategy_id', '') or ''),
        'strategy_name': getattr(service, 'strategy_name', None) or str(getattr(service, 'strategy_id', '')),
        'data_source': getattr(service, 'market_source', None) or 'Binance',
        'broker_name': getattr(service, 'broker_name', None),
        'initial_capital': initial,
        'final_equity': equity,
        'margin_pct': _f(getattr(service, 'margin_pct', None)),
        'leverage': int(getattr(config, 'leverage', 1) or 1) if config is not None else None,
        'taker_fee_bps': _f(getattr(config, 'taker_fee_bps', None)) if config is not None else None,
        'maker_fee_bps': _f(getattr(config, 'maker_fee_bps', None)) if config is not None else None,
        'conversion_rate': _f(getattr(service, 'conversion_rate', None)),
        'config_json': config_json,
        'last_price': _f(getattr(service, 'last_price', None)),
        'last_checked': _parse_ist(getattr(service, 'last_checked', None)),
        'equity_curve': curve[-5000:],
        'closed_trades': closed,
        'logs': list(getattr(service, 'logs', []) or [])[-MAX_SAVED_LOG_LINES:],
        **stats,
    }
    if status in (STATUS_STOPPED, STATUS_INTERRUPTED):
        values['open_positions'] = _open_positions(service)
        values['stopped_at'] = _utc_now()
    return values


def _upsert(instance_key, values, user_id=None):
    db = SessionLocal()
    try:
        row = db.query(PaperSession).filter(PaperSession.instance_key == instance_key).first()
        if row is None:
            row = PaperSession(instance_key=instance_key, user_id=user_id,
                               created_at=_utc_now(), started_at=_utc_now())
            db.add(row)
        for key, value in values.items():
            setattr(row, key, value)
        db.commit()
        return row.id
    except Exception as exc:
        db.rollback()
        print(f"[paper-history] failed to save session {instance_key}: {exc}")
        return None
    finally:
        db.close()


def start_session(user_id, instance_key, service):
    """Create the history row for a freshly started paper instance."""
    values = _payload(service, STATUS_RUNNING)
    session_id = _upsert(instance_key, values, user_id=user_id)
    if session_id is not None:
        service.session_id = session_id
        service.history_status = STATUS_RUNNING
    return session_id


def persist_snapshot(instance_key, service):
    """Refresh the saved row from the live worker's current state."""
    if not instance_key:
        return None
    values = _payload(service, getattr(service, 'history_status', STATUS_RUNNING))
    return _upsert(instance_key, values, user_id=getattr(service, 'user_id', None))


def finalize_session(instance_key, service, status=STATUS_STOPPED):
    """Save the final state of a session that is leaving memory."""
    if not instance_key:
        return None
    service.history_status = status
    values = _payload(service, status)
    return _upsert(instance_key, values, user_id=getattr(service, 'user_id', None))


def delete_record(instance_key):
    """Purge one saved session (used by History → Delete and workspace delete)."""
    db = SessionLocal()
    try:
        deleted = db.query(PaperSession).filter(PaperSession.instance_key == instance_key).delete()
        db.commit()
        return bool(deleted)
    except Exception as exc:
        db.rollback()
        print(f"[paper-history] failed to delete session {instance_key}: {exc}")
        return False
    finally:
        db.close()


def delete_records_for_user(user_id):
    db = SessionLocal()
    try:
        deleted = db.query(PaperSession).filter(PaperSession.user_id == user_id).delete()
        db.commit()
        return int(deleted or 0)
    except Exception as exc:
        db.rollback()
        print(f"[paper-history] failed to clear sessions for user {user_id}: {exc}")
        return 0
    finally:
        db.close()


def mark_interrupted_sessions():
    """Flag rows still marked running — the process that owned them is gone."""
    db = SessionLocal()
    try:
        rows = db.query(PaperSession).filter(PaperSession.status == STATUS_RUNNING).all()
        for row in rows:
            row.status = STATUS_INTERRUPTED
            row.stopped_at = row.stopped_at or _utc_now()
        db.commit()
        return len(rows)
    except Exception as exc:
        db.rollback()
        print(f"[paper-history] failed to mark interrupted sessions: {exc}")
        return 0
    finally:
        db.close()


def _summary_dict(row):
    return {
        'id': row.id,
        'instance_key': row.instance_key,
        'strategy_id': row.strategy_id,
        'strategy_name': row.strategy_name or row.strategy_id,
        'symbol': row.symbol,
        'data_source': row.data_source,
        'status': row.status,
        'initial_capital': row.initial_capital,
        'final_equity': row.final_equity,
        'net_pnl': row.net_pnl,
        'roi': row.roi,
        'win_rate': row.win_rate,
        'profit_factor': row.profit_factor,
        'total_fees': row.total_fees,
        'max_drawdown_pct': row.max_drawdown_pct,
        'peak_equity': row.peak_equity,
        'closed_trade_count': row.closed_trade_count or 0,
        'margin_pct': row.margin_pct,
        'leverage': row.leverage,
        'taker_fee_bps': row.taker_fee_bps,
        'maker_fee_bps': row.maker_fee_bps,
        'last_price': row.last_price,
        'created_at': row.created_at,
        'started_at': row.started_at,
        'stopped_at': row.stopped_at,
        'updated_at': row.updated_at,
        'last_checked': row.last_checked,
    }


def list_sessions(user_id, db=None):
    """History list (newest first) for one user."""
    own_db = db is None
    session = db or SessionLocal()
    try:
        rows = session.query(PaperSession).filter(PaperSession.user_id == user_id) \
            .order_by(PaperSession.created_at.desc()).all()
        return [_summary_dict(r) for r in rows]
    finally:
        if own_db:
            session.close()


def get_session(session_id, user_id, db=None):
    """Full saved detail for one session, or None when it is not the user's."""
    own_db = db is None
    session = db or SessionLocal()
    try:
        row = session.query(PaperSession).filter(
            PaperSession.id == session_id, PaperSession.user_id == user_id).first()
        if not row:
            return None
        detail = _summary_dict(row)
        detail.update({
            'equity_curve': row.equity_curve or [],
            'closed_trades': row.closed_trades or [],
            'open_positions': row.open_positions or [],
            'logs': row.logs or [],
            'params': _parse_config(row.config_json),
        })
        return detail
    finally:
        if own_db:
            session.close()


def _parse_config(config_json):
    if not config_json:
        return {}
    try:
        value = json.loads(config_json) if isinstance(config_json, str) else config_json
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def delete_session(session_id, user_id, db=None):
    """Delete a saved session. Returns ``(found, instance_key)``."""
    own_db = db is None
    session = db or SessionLocal()
    try:
        row = session.query(PaperSession).filter(
            PaperSession.id == session_id, PaperSession.user_id == user_id).first()
        if not row:
            return False, None
        instance_key = row.instance_key
        session.delete(row)
        session.commit()
        return True, instance_key
    except Exception as exc:
        session.rollback()
        print(f"[paper-history] failed to delete session {session_id}: {exc}")
        return False, None
    finally:
        if own_db:
            session.close()
