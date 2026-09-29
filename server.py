# -*- coding: utf-8 -*-
"""
מערכת ניטור מניות והתראות הזדמנויות רווח מהיר — S&P 500.
Backend: Flask + SQLite. סריקה מופעלת ע"י Render Cron ל-/api/scan (מוגן טוקן).
דשבורד עם התחברות בסיסמה יחידה מציג סטטוס סריקה + היסטוריית איתותים.

⚠️ כלי סיגנלים/מחקר בלבד — לא ייעוץ השקעות.
"""
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, jsonify, request, session, render_template

import scanner
import storage
import telegram_notify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder='static', template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', 'stock-monitor-dev-key')

IS_PROD = bool(os.environ.get('DATA_DIR'))
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=IS_PROD,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=256 * 1024,
)

DASHBOARD_PASSWORD = os.environ.get('DASHBOARD_PASSWORD', '')
CRON_TOKEN = os.environ.get('CRON_TOKEN', '')

# הגבלת קצב פשוטה לניסיונות התחברות (זיכרון פנימי — מספיק לשימוש יחיד)
_login_attempts = {}
LOGIN_MAX_ATTEMPTS = 8
LOGIN_WINDOW_SEC = 300


def _client_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown').split(',')[0].strip()


def _rate_limited(ip):
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < LOGIN_WINDOW_SEC]
    _login_attempts[ip] = attempts
    return len(attempts) >= LOGIN_MAX_ATTEMPTS


def _record_attempt(ip):
    _login_attempts.setdefault(ip, []).append(time.time())


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('authed'):
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return wrapper


@app.route('/')
def index():
    return render_template('dashboard.html')


@app.route('/api/login', methods=['POST'])
def login():
    ip = _client_ip()
    if _rate_limited(ip):
        return jsonify({'error': 'יותר מדי ניסיונות — נסה שוב בעוד כמה דקות'}), 429
    data = request.get_json(silent=True) or {}
    password = data.get('password', '')
    if not DASHBOARD_PASSWORD:
        return jsonify({'error': 'המערכת לא מוגדרת (חסרה סיסמת דשבורד בצד השרת)'}), 500
    if secrets.compare_digest(password, DASHBOARD_PASSWORD):
        session.clear()
        session.permanent = True
        session['authed'] = True
        return jsonify({'ok': True})
    _record_attempt(ip)
    return jsonify({'error': 'סיסמה שגויה'}), 401


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})


@app.route('/api/session')
def session_status():
    return jsonify({'authed': bool(session.get('authed'))})


@app.route('/api/status')
@login_required
def status():
    last_scan = storage.get_last_scan()
    today = datetime.now(timezone.utc).date().isoformat()
    return jsonify({
        'market_open': scanner.is_market_open_now(),
        'last_scan': last_scan,
        'signals_today': storage.count_signals_today(today),
        'recent_signals': storage.get_recent_signals(limit=50),
        'recent_scans': storage.get_recent_scans(limit=10),
        'telegram_enabled': telegram_notify.telegram_enabled(),
    })


@app.route('/api/scan', methods=['POST'])
def run_scan_endpoint():
    """נקודת קצה מוגנת-טוקן שמופעלת ע"י Render Cron. force=1 עוקף בדיקת שעות מסחר (לבדיקה ידנית)."""
    token = request.args.get('token', '')
    if not CRON_TOKEN or not secrets.compare_digest(token, CRON_TOKEN):
        return jsonify({'error': 'unauthorized'}), 401

    force = request.args.get('force') == '1'
    if not force and not scanner.is_market_open_now():
        scan_id = storage.start_scan()
        storage.finish_scan(scan_id, status='skipped_market_closed')
        return jsonify({'status': 'skipped_market_closed'})

    scan_id = storage.start_scan()
    try:
        tickers = scanner.get_sp500_tickers()
        signals, meta = scanner.run_scan(tickers)
        today = datetime.now(timezone.utc).date().isoformat()
        alerted = 0
        for sig in signals:
            if storage.already_alerted_today(sig['ticker'], today):
                continue
            sent, _err = telegram_notify.send_signal_alert(sig)
            storage.insert_signal(scan_id, sig, today, telegram_sent=sent)
            if sent:
                alerted += 1
        storage.finish_scan(
            scan_id, status='ok',
            tickers_scanned=meta['tickers_scanned'],
            tickers_with_data=meta['tickers_with_data'],
            signals_found=meta['signals_found'],
        )
        return jsonify({'status': 'ok', 'meta': meta, 'alerts_sent': alerted})
    except Exception as e:  # noqa: BLE001
        storage.finish_scan(scan_id, status='error', error=str(e))
        telegram_notify.send_scan_error_alert(str(e))
        return jsonify({'status': 'error', 'error': str(e)}), 500


storage.init_db()

if __name__ == '__main__':
    app.run(debug=not IS_PROD, port=int(os.environ.get('PORT', 5001)))
