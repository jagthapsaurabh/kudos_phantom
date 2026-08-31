"""
DB Integrity Check — Phantom V3
Checks constraints, indexes, orphaned data, seed progress durability,
klines duplicate/misaligned repair status.

Usage:
  python -m scripts.db_integrity_check
  python scripts/db_integrity_check.py --fix
  python scripts/db_integrity_check.py --json

Exit code 0 = clean, 1 = issues found, 2 = error.
"""
import sys
import os
import json
import argparse
from datetime import datetime

# Ensure backend is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.models import (
    SessionLocal, User, BrokerDefinition, BrokerConnection, FeeSetting,
    Klines, MarketTick, MarketDataSeedProgress, CustomStrategy,
    BacktestRun, Trade, PaperSession, BrokerOrder, BrokerFill,
    engine
)
from sqlalchemy import func, text, inspect
from sqlalchemy.exc import SQLAlchemyError

def check_unique_constraints(db):
    issues = []
    # Users: username unique
    dup_users = db.query(User.username, func.count(User.id).label('c')).group_by(User.username).having(func.count(User.id) > 1).all()
    for username, count in dup_users:
        issues.append({"type": "duplicate", "table": "users", "field": "username", "value": username, "count": count})

    # BrokerDefinitions: code unique (case-sensitive check + case-insensitive)
    dup_codes = db.query(func.lower(BrokerDefinition.code), func.count(BrokerDefinition.id)).group_by(func.lower(BrokerDefinition.code)).having(func.count(BrokerDefinition.id) > 1).all()
    for code_lower, count in dup_codes:
        rows = db.query(BrokerDefinition).filter(func.lower(BrokerDefinition.code) == code_lower).all()
        issues.append({"type": "duplicate_case_insensitive", "table": "broker_definitions", "field": "code", "value": code_lower, "count": count, "ids": [r.id for r in rows], "codes": [r.code for r in rows]})

    # BrokerConnections: uq_user_broker_label
    dup_conn = db.query(BrokerConnection.user_id, BrokerConnection.broker_code, BrokerConnection.label, func.count(BrokerConnection.id)).group_by(BrokerConnection.user_id, BrokerConnection.broker_code, BrokerConnection.label).having(func.count(BrokerConnection.id) > 1).all()
    for uid, bcode, label, count in dup_conn:
        issues.append({"type": "duplicate", "table": "broker_connections", "constraint": "uq_user_broker_label", "user_id": uid, "broker_code": bcode, "label": label, "count": count})

    # FeeSettings: uq_fee_broker_mode
    dup_fee = db.query(FeeSetting.broker_code, FeeSetting.mode, func.count(FeeSetting.id)).group_by(FeeSetting.broker_code, FeeSetting.mode).having(func.count(FeeSetting.id) > 1).all()
    for bcode, mode, count in dup_fee:
        issues.append({"type": "duplicate", "table": "fee_settings", "constraint": "uq_fee_broker_mode", "broker_code": bcode, "mode": mode, "count": count})

    # Klines: duplicate timestamps per source/symbol/interval
    # Use SQL for performance
    try:
        result = db.execute(text("""
            SELECT source, symbol, interval, event_time, COUNT(*) as cnt
            FROM klines
            GROUP BY source, symbol, interval, event_time
            HAVING COUNT(*) > 1
            LIMIT 100
        """)).fetchall()
        for row in result:
            issues.append({"type": "duplicate_kline", "table": "klines", "source": row[0], "symbol": row[1], "interval": row[2], "event_time": str(row[3]), "count": row[4]})
    except Exception as e:
        issues.append({"type": "check_failed", "table": "klines", "error": str(e)})

    # BrokerFills: uq_broker_fill_trade
    dup_fills = db.query(BrokerFill.user_id, BrokerFill.broker_code, BrokerFill.broker_trade_id, func.count(BrokerFill.id)).group_by(BrokerFill.user_id, BrokerFill.broker_code, BrokerFill.broker_trade_id).having(func.count(BrokerFill.id) > 1).all()
    for uid, bcode, tid, count in dup_fills:
        if tid:  # NULL allowed, skip
            issues.append({"type": "duplicate", "table": "broker_fills", "constraint": "uq_broker_fill_trade", "user_id": uid, "broker_code": bcode, "broker_trade_id": tid, "count": count})

    # PaperSessions: instance_key unique
    dup_sess = db.query(PaperSession.instance_key, func.count(PaperSession.id)).group_by(PaperSession.instance_key).having(func.count(PaperSession.id) > 1).all()
    for ik, count in dup_sess:
        issues.append({"type": "duplicate", "table": "paper_sessions", "field": "instance_key", "value": ik, "count": count})

    return issues

def check_orphaned_data(db):
    issues = []
    # BrokerConnection.user_id -> users.id
    orphan_conn = db.query(BrokerConnection).filter(~BrokerConnection.user_id.in_(db.query(User.id))).all()
    for row in orphan_conn:
        issues.append({"type": "orphan", "table": "broker_connections", "id": row.id, "fk": "user_id", "value": row.user_id})

    # CustomStrategy.user_id
    orphan_strat = db.query(CustomStrategy).filter(~CustomStrategy.user_id.in_(db.query(User.id))).all()
    for row in orphan_strat:
        issues.append({"type": "orphan", "table": "custom_strategies", "id": row.id, "fk": "user_id", "value": row.user_id})

    # BacktestRun.user_id
    orphan_run = db.query(BacktestRun).filter(~BacktestRun.user_id.in_(db.query(User.id))).all()
    for row in orphan_run:
        issues.append({"type": "orphan", "table": "backtest_runs", "id": row.id, "fk": "user_id", "value": row.user_id})

    # Trade.run_id
    orphan_trade = db.query(Trade).filter(~Trade.run_id.in_(db.query(BacktestRun.id))).all()
    for row in orphan_trade:
        issues.append({"type": "orphan", "table": "trades", "id": row.id, "fk": "run_id", "value": row.run_id})

    # PaperSession.user_id
    orphan_ps = db.query(PaperSession).filter(~PaperSession.user_id.in_(db.query(User.id))).all()
    for row in orphan_ps:
        issues.append({"type": "orphan", "table": "paper_sessions", "id": row.id, "fk": "user_id", "value": row.user_id})

    # BrokerOrder.user_id
    orphan_bo = db.query(BrokerOrder).filter(~BrokerOrder.user_id.in_(db.query(User.id))).all()
    for row in orphan_bo:
        issues.append({"type": "orphan", "table": "broker_orders", "id": row.id, "fk": "user_id", "value": row.user_id})

    # BrokerFill.user_id
    orphan_bf = db.query(BrokerFill).filter(~BrokerFill.user_id.in_(db.query(User.id))).all()
    for row in orphan_bf:
        issues.append({"type": "orphan", "table": "broker_fills", "id": row.id, "fk": "user_id", "value": row.user_id})

    # BrokerConnection with NULL api_key but is_active=1 (misconfigured)
    bad_conn = db.query(BrokerConnection).filter(BrokerConnection.is_active == 1).filter((BrokerConnection.api_key == None) | (BrokerConnection.api_key == '')).all()
    for row in bad_conn:
        issues.append({"type": "misconfigured", "table": "broker_connections", "id": row.id, "reason": "active connection has no api_key", "user_id": row.user_id, "broker_code": row.broker_code})

    return issues

def check_indexes(db):
    issues = []
    insp = inspect(engine)
    # Expected indexes
    expected = {
        'klines': ['ix_source_symbol_interval_time', 'ix_klines_source', 'ix_klines_symbol', 'ix_klines_interval', 'ix_klines_event_time'],
        'market_ticks': ['ix_market_ticks_source_symbol_time', 'ix_market_ticks_source', 'ix_market_ticks_symbol', 'ix_market_ticks_event_time'],
        'broker_connections': ['ix_broker_connections_user_id', 'ix_broker_connections_broker_code'],
        'broker_orders': ['ix_broker_orders_user_id', 'ix_broker_orders_broker_code', 'ix_broker_orders_symbol', 'ix_broker_orders_instance_key'],
    }
    for table, exp_indexes in expected.items():
        try:
            existing = [idx['name'] for idx in insp.get_indexes(table)]
            for exp in exp_indexes:
                # Check prefix match (SQLAlchemy auto-names may differ)
                if exp not in existing and not any(exp in e or e in exp for e in existing):
                    # Only warn for critical composite index
                    if 'source_symbol' in exp or 'source_symbol_time' in exp:
                        issues.append({"type": "missing_index", "table": table, "index": exp, "existing": existing})
        except Exception as e:
            issues.append({"type": "index_check_failed", "table": table, "error": str(e)})
    return issues

def check_seed_progress(db):
    issues = []
    # Stuck running seeds (updated > 2 hours ago but still running)
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(hours=2)
    stuck = db.query(MarketDataSeedProgress).filter(
        MarketDataSeedProgress.status == 'running',
        MarketDataSeedProgress.updated_at < cutoff
    ).all()
    for row in stuck:
        issues.append({"type": "stuck_seed", "table": "market_data_seed_progress", "id": row.id, "source": row.source, "symbol": row.symbol, "interval": row.interval, "updated_at": str(row.updated_at), "pages": row.pages})

    # Failed seeds
    failed = db.query(MarketDataSeedProgress).filter(MarketDataSeedProgress.status == 'failed').order_by(MarketDataSeedProgress.updated_at.desc()).limit(20).all()
    for row in failed:
        issues.append({"type": "failed_seed", "table": "market_data_seed_progress", "id": row.id, "source": row.source, "symbol": row.symbol, "interval": row.interval, "last_error": (row.last_error or '')[:200], "updated_at": str(row.updated_at)})

    # Seeds with no progress but marked running
    zero_progress = db.query(MarketDataSeedProgress).filter(MarketDataSeedProgress.status == 'running', MarketDataSeedProgress.pages == 0).all()
    for row in zero_progress:
        age_minutes = (datetime.utcnow() - (row.updated_at or row.created_at)).total_seconds() / 60 if row.updated_at else 999
        if age_minutes > 30:
            issues.append({"type": "zero_progress_seed", "table": "market_data_seed_progress", "id": row.id, "source": row.source, "symbol": row.symbol, "interval": row.interval, "age_minutes": int(age_minutes)})

    return issues

def check_klines_health(db):
    issues = []
    # Count per source/symbol/interval
    counts = db.query(Klines.source, Klines.symbol, Klines.interval, func.count(Klines.id).label('c')).group_by(Klines.source, Klines.symbol, Klines.interval).all()
    for source, symbol, interval, count in counts:
        if count == 0:
            issues.append({"type": "empty_series", "table": "klines", "source": source, "symbol": symbol, "interval": interval})
        # Check for gaps: if we have data, first and last should be reasonable
        try:
            first_last = db.query(func.min(Klines.event_time), func.max(Klines.event_time)).filter(Klines.source == source, Klines.symbol == symbol, Klines.interval == interval).first()
            if first_last and first_last[0] and first_last[1]:
                span_days = (first_last[1] - first_last[0]).days
                # Rough expected count check (not exact, just sanity)
                interval_minutes = {'1m': 1, '5m': 5, '15m': 15, '1h': 60, '4h': 240, '1d': 1440}.get(interval, 60)
                if interval_minutes and span_days > 7:
                    expected = (span_days * 24 * 60) // interval_minutes
                    if expected > 0 and count < expected * 0.5:
                        issues.append({"type": "sparse_series", "table": "klines", "source": source, "symbol": symbol, "interval": interval, "count": count, "expected_approx": expected, "span_days": span_days, "first": str(first_last[0]), "last": str(first_last[1])})
        except Exception:
            pass

    # Misaligned timestamps (off-grid) - sample check
    try:
        from app.services.data_sync import DataSyncService
        health = DataSyncService.data_health()
        for item in health:
            if item.get('misaligned_rows', 0) > 0:
                issues.append({"type": "misaligned_klines", "table": "klines", "source": item['source'], "symbol": item['symbol'], "interval": item['interval'], "misaligned_rows": item['misaligned_rows'], "scanned": item.get('scanned', 0)})
            if item.get('duplicate_rows', 0) > 0:
                issues.append({"type": "duplicate_kline_rows", "table": "klines", "source": item['source'], "symbol": item['symbol'], "interval": item['interval'], "duplicate_rows": item['duplicate_rows']})
    except Exception as e:
        issues.append({"type": "health_check_failed", "error": str(e)})

    return issues

def check_not_null(db):
    issues = []
    # Users must have username and password_hash
    null_users = db.query(User).filter((User.username == None) | (User.username == '') | (User.password_hash == None)).all()
    for u in null_users:
        issues.append({"type": "null_required", "table": "users", "id": u.id, "field": "username or password_hash"})

    # BrokerDefinitions must have code and name
    null_brokers = db.query(BrokerDefinition).filter((BrokerDefinition.code == None) | (BrokerDefinition.code == '')).all()
    for b in null_brokers:
        issues.append({"type": "null_required", "table": "broker_definitions", "id": b.id, "field": "code"})

    # Klines must have open/high/low/close
    null_klines = db.query(Klines).filter((Klines.open == None) | (Klines.high == None) | (Klines.low == None) | (Klines.close == None)).limit(10).all()
    for k in null_klines:
        issues.append({"type": "null_required", "table": "klines", "id": k.id, "field": "ohlc", "source": k.source, "symbol": k.symbol, "interval": k.interval, "event_time": str(k.event_time)})

    return issues

def run_all_checks(fix=False):
    db = SessionLocal()
    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "database_url": engine.url.render_as_string(hide_password=True),
        "checks": {},
        "summary": {},
        "fixed": []
    }
    try:
        # Run checks (each wrapped to survive missing tables)
        def safe_check(name, fn):
            try:
                print(f"Checking {name}...")
                return fn(db)
            except Exception as e:
                print(f"  Check {name} failed: {e}")
                return [{"type": "check_failed", "check": name, "error": str(e)[:500]}]

        report["checks"]["unique_constraints"] = safe_check("unique_constraints", check_unique_constraints)
        report["checks"]["orphaned_data"] = safe_check("orphaned_data", check_orphaned_data)
        report["checks"]["indexes"] = safe_check("indexes", check_indexes)
        report["checks"]["seed_progress"] = safe_check("seed_progress", check_seed_progress)
        report["checks"]["klines_health"] = safe_check("klines_health", check_klines_health)
        report["checks"]["not_null"] = safe_check("not_null", check_not_null)

        # Summary
        total_issues = sum(len(v) for v in report["checks"].values())
        report["summary"] = {
            "total_issues": total_issues,
            "by_check": {k: len(v) for k, v in report["checks"].items()},
            "critical": len([i for check in report["checks"].values() for i in check if i.get("type") in ("orphan", "null_required", "duplicate", "duplicate_case_insensitive")]),
            "warnings": len([i for check in report["checks"].values() for i in check if i.get("type") not in ("orphan", "null_required", "duplicate", "duplicate_case_insensitive")]),
        }

        # Fix mode: handle safe auto-fixes
        if fix:
            print("\n--- FIX MODE ---")
            # Fix stuck seeds: mark as failed
            stuck = [i for i in report["checks"]["seed_progress"] if i["type"] == "stuck_seed"]
            for item in stuck:
                try:
                    row = db.query(MarketDataSeedProgress).filter(MarketDataSeedProgress.id == item["id"]).first()
                    if row:
                        row.status = "failed"
                        row.last_error = f"Auto-marked as failed by integrity check (stuck since {item['updated_at']})"
                        report["fixed"].append({"type": "stuck_seed_fixed", "id": item["id"]})
                except Exception as e:
                    report["fixed"].append({"type": "fix_failed", "id": item["id"], "error": str(e)})
            
            # Fix active connections with no api_key: deactivate them
            bad_conns = [i for i in report["checks"]["orphaned_data"] if i["type"] == "misconfigured"]
            for item in bad_conns:
                try:
                    row = db.query(BrokerConnection).filter(BrokerConnection.id == item["id"]).first()
                    if row:
                        row.is_active = 0
                        report["fixed"].append({"type": "misconfigured_fixed", "id": item["id"], "action": "deactivated"})
                except Exception as e:
                    report["fixed"].append({"type": "fix_failed", "id": item["id"], "error": str(e)})
            
            db.commit()
            print(f"Fixed {len(report['fixed'])} issues")

    except Exception as e:
        report["error"] = str(e)
        import traceback
        report["traceback"] = traceback.format_exc()
    finally:
        db.close()
    
    return report

def main():
    parser = argparse.ArgumentParser(description="DB Integrity Check for Phantom V3")
    parser.add_argument('--fix', action='store_true', help='Auto-fix safe issues (stuck seeds, misconfigured connections)')
    parser.add_argument('--json', action='store_true', help='Output JSON report')
    parser.add_argument('--output', type=str, help='Output file for JSON report')
    args = parser.parse_args()

    report = run_all_checks(fix=args.fix)

    if args.json or args.output:
        output = json.dumps(report, indent=2, default=str)
        if args.output:
            with open(args.output, 'w') as f:
                f.write(output)
            print(f"Report written to {args.output}")
        else:
            print(output)
    else:
        # Human-readable summary
        print("\n" + "="*60)
        print("DB INTEGRITY CHECK REPORT")
        print("="*60)
        print(f"Timestamp: {report['timestamp']}")
        print(f"Database: {report['database_url']}")
        print(f"Total issues: {report['summary'].get('total_issues', 0)}")
        print(f"Critical: {report['summary'].get('critical', 0)}, Warnings: {report['summary'].get('warnings', 0)}")
        print()
        for check_name, issues in report["checks"].items():
            print(f"--- {check_name}: {len(issues)} issues ---")
            for issue in issues[:20]:  # Show first 20 per check
                print(f"  [{issue.get('type')}] {json.dumps(issue, default=str)[:200]}")
            if len(issues) > 20:
                print(f"  ... and {len(issues)-20} more")
            print()
        
        if report.get("fixed"):
            print(f"--- Fixed {len(report['fixed'])} issues ---")
            for f in report["fixed"]:
                print(f"  {f}")
        
        if report.get("error"):
            print(f"\nERROR: {report['error']}")

    # Exit code
    if report.get("error"):
        sys.exit(2)
    elif report["summary"].get("total_issues", 0) > 0:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == '__main__':
    main()
