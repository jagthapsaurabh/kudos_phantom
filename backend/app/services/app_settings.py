"""Platform-wide settings with a clear precedence order.

The USD→INR conversion rate is the first of these: every paper/live worker
and every backtest converts margin and PnL with it, so it must come from ONE
place instead of a constant copied around the codebase.

Precedence: admin-saved value (app_settings table) → USD_INR_RATE env var →
built-in default. Reads never raise — a broken DB falls back down the chain
so a worker can always start.
"""
import os

USD_INR_KEY = "usd_inr_rate"
DEFAULT_USD_INR = 85.0

# Sanity bounds for the admin input: wide enough for any realistic INR move,
# tight enough to reject a fat-fingered 8.5 or 8500 that would silently scale
# every position size 10x.
USD_INR_MIN = 10.0
USD_INR_MAX = 500.0


def _env_rate():
    raw = os.getenv("USD_INR_RATE")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        val = float(raw)
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


def usd_inr_setting():
    """The effective rate plus where it came from: (rate, source, updated_at).

    ``source`` is 'admin' | 'env' | 'default' so the panel can show whether
    the number on screen is something an admin chose or just a fallback.
    """
    try:
        from app.database.models import SessionLocal, AppSetting
        db = SessionLocal()
        try:
            row = db.query(AppSetting).filter(AppSetting.key == USD_INR_KEY).first()
            if row is not None:
                val = float(row.value)
                if USD_INR_MIN <= val <= USD_INR_MAX:
                    return val, "admin", row.updated_at
        finally:
            db.close()
    except Exception:
        # DB missing/locked/mid-migration: the workers still need a rate.
        pass
    env = _env_rate()
    if env is not None:
        return env, "env", None
    return DEFAULT_USD_INR, "default", None


def get_usd_inr_rate() -> float:
    """The effective USD→INR rate (admin value → env → default)."""
    return usd_inr_setting()[0]


def set_usd_inr_rate(value) -> float:
    """Persist an admin-chosen rate. Raises ValueError on a non-sane number."""
    try:
        val = float(value)
    except (TypeError, ValueError):
        raise ValueError("The rate must be a number")
    if not (USD_INR_MIN <= val <= USD_INR_MAX):
        raise ValueError(
            f"USD/INR rate {val:g} is outside the sane range "
            f"{USD_INR_MIN:g}–{USD_INR_MAX:g} — refusing to scale every "
            f"position by a typo")
    from app.database.models import SessionLocal, AppSetting
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == USD_INR_KEY).first()
        if row is None:
            row = AppSetting(key=USD_INR_KEY, value=str(val))
            db.add(row)
        else:
            row.value = str(val)
        db.commit()
        return val
    finally:
        db.close()
