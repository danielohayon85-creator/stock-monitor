# -*- coding: utf-8 -*-
"""שכבת אחסון: SQLite לתולדות סריקות ואיתותים, על הדיסק המתמיד (DATA_DIR)."""
import os
import sqlite3
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(os.environ.get('DATA_DIR', BASE_DIR), 'stock_monitor.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            tickers_scanned INTEGER DEFAULT 0,
            tickers_with_data INTEGER DEFAULT 0,
            signals_found INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'running',   -- running / ok / error / skipped_market_closed
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            ticker TEXT NOT NULL,
            price REAL, rel_vol REAL, momentum_pct REAL, rsi14 REAL,
            breakout INTEGER, trend_ok INTEGER, score REAL,
            stop_loss REAL, target REAL,
            telegram_sent INTEGER DEFAULT 0,
            alert_date TEXT NOT NULL,                 -- YYYY-MM-DD, לצורך דדופ יומי
            FOREIGN KEY (scan_id) REFERENCES scans(id)
        );
        CREATE INDEX IF NOT EXISTS idx_signals_alert_date_ticker ON signals(alert_date, ticker);
        CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);
    ''')
    conn.commit()
    conn.close()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def start_scan():
    conn = get_db()
    cur = conn.execute('INSERT INTO scans (started_at, status) VALUES (?, ?)', (_now_iso(), 'running'))
    conn.commit()
    scan_id = cur.lastrowid
    conn.close()
    return scan_id


def finish_scan(scan_id, status='ok', tickers_scanned=0, tickers_with_data=0, signals_found=0, error=None):
    conn = get_db()
    conn.execute('''UPDATE scans SET finished_at=?, status=?, tickers_scanned=?,
                     tickers_with_data=?, signals_found=?, error=? WHERE id=?''',
                 (_now_iso(), status, tickers_scanned, tickers_with_data, signals_found, error, scan_id))
    conn.commit()
    conn.close()


def already_alerted_today(ticker, alert_date):
    conn = get_db()
    row = conn.execute('SELECT 1 FROM signals WHERE ticker=? AND alert_date=? LIMIT 1',
                        (ticker, alert_date)).fetchone()
    conn.close()
    return row is not None


def insert_signal(scan_id, sig, alert_date, telegram_sent=False):
    conn = get_db()
    conn.execute('''INSERT INTO signals (scan_id, ts, ticker, price, rel_vol, momentum_pct, rsi14,
                     breakout, trend_ok, score, stop_loss, target, telegram_sent, alert_date)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                 (scan_id, _now_iso(), sig['ticker'], sig['price'], sig['rel_vol'], sig['momentum_pct'],
                  sig['rsi14'], int(sig['breakout']), int(sig['trend_ok']), sig['score'],
                  sig['stop_loss'], sig['target'], int(telegram_sent), alert_date))
    conn.commit()
    conn.close()


def get_last_scan():
    conn = get_db()
    row = conn.execute('SELECT * FROM scans ORDER BY id DESC LIMIT 1').fetchone()
    conn.close()
    return dict(row) if row else None


def get_recent_signals(limit=50):
    conn = get_db()
    rows = conn.execute('SELECT * FROM signals ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_signals_today(alert_date):
    conn = get_db()
    row = conn.execute('SELECT COUNT(*) c FROM signals WHERE alert_date=?', (alert_date,)).fetchone()
    conn.close()
    return row['c'] if row else 0


def get_recent_scans(limit=10):
    conn = get_db()
    rows = conn.execute('SELECT * FROM scans ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
