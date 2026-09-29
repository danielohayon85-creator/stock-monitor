# -*- coding: utf-8 -*-
"""
לוגיקת הסריקה: אוניברסת S&P 500, שליפת נתונים מ-yfinance, וחישוב איתותי
"הזדמנות לרווח מהיר" (מומנטום + פריצת נפח). ראו README.md להסבר המתודולוגיה.

הערה: זה כלי סיגנלים/מחקר — לא ייעוץ השקעות. ראו DISCLAIMER למטה.
"""
import csv
import io
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

try:
    import pandas_market_calendars as mcal
except ImportError:  # pragma: no cover
    mcal = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('DATA_DIR', BASE_DIR)
SP500_CACHE_PATH = os.path.join(DATA_DIR, 'sp500_universe.json')
SP500_FALLBACK_PATH = os.path.join(BASE_DIR, 'sp500_fallback.csv')
SP500_SOURCE_URL = 'https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv'
SP500_CACHE_MAX_AGE_SEC = 24 * 3600

DISCLAIMER = (
    "⚠️ כלי סיגנלים/מחקר בלבד — לא ייעוץ השקעות ואינו מהווה הצעה לרכישה/מכירה. "
    "כל החלטת מסחר היא באחריותכם המלאה."
)

# --- פרמטרי הסיגנל (שקיפות מלאה — אפשר לכוונן) --------------------------
VOL_SPIKE_MIN = 2.0          # נפח (3 עמודות אחרונות) לפחות פי כך מהבייסליין
MOMENTUM_MIN_PCT = 1.5       # עלייה מינימלית באחוזים בשעה האחרונה (4 עמודות של 15 דק')
RSI_MIN, RSI_MAX = 45, 82    # טווח RSI תקין (לא רדום, לא "over-extended")
SIGNAL_THRESHOLD = 70        # ציון מינימלי כדי להיחשב איתות
STOP_LOSS_PCT = 0.03         # סטופ-לוס גנרי: 3% מנקודת הכניסה
TARGET_PCT = 0.06            # יעד גנרי: 6% מנקודת הכניסה (יחס סיכוי:סיכון 2:1)

INTRADAY_INTERVAL = '15m'
INTRADAY_PERIOD = '5d'
DAILY_PERIOD = '4mo'
MIN_INTRADAY_BARS = 24       # ~2 ימי מסחר לפחות
MIN_DAILY_BARS = 25          # מספיק ל-EMA20/RSI14

CHUNK_SIZE = 60
CHUNK_SLEEP_SEC = 1.5
REQUEST_TIMEOUT_SEC = 15
SCAN_TIME_BUDGET_SEC = 240   # תקציב זמן כולל לשליפת הדאטה (מתחת ל-timeout של gunicorn)


# ------------------------------------------------------------- אוניברסה
def _parse_sp500_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    return sorted({row['Symbol'].strip().upper().replace('.', '-') for row in reader if row.get('Symbol')})


def get_sp500_tickers(force_refresh=False):
    """מחזיר רשימת טיקרים של S&P 500, עם קאש יומי ו-fallback מקומי אם הרשת נכשלת."""
    if not force_refresh and os.path.exists(SP500_CACHE_PATH):
        try:
            with open(SP500_CACHE_PATH, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            if time.time() - cached.get('fetched_at', 0) < SP500_CACHE_MAX_AGE_SEC:
                return cached['tickers']
        except (OSError, ValueError, KeyError):
            pass

    try:
        with urllib.request.urlopen(SP500_SOURCE_URL, timeout=20) as r:
            tickers = _parse_sp500_csv(r.read().decode('utf-8'))
        _save_universe_cache(tickers)
        return tickers
    except Exception:  # noqa: BLE001 — כשל רשת: נופלים ל-fallback
        pass

    if os.path.exists(SP500_CACHE_PATH):
        try:
            with open(SP500_CACHE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)['tickers']
        except (OSError, ValueError, KeyError):
            pass

    with open(SP500_FALLBACK_PATH, 'r', encoding='utf-8') as f:
        return _parse_sp500_csv(f.read())


def _save_universe_cache(tickers):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SP500_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump({'fetched_at': time.time(), 'tickers': tickers}, f)
    except OSError:
        pass


# ------------------------------------------------------------- שעות מסחר
def is_market_open_now():
    """True אם שוק המניות האמריקאי (NYSE) פתוח כרגע."""
    now_utc = datetime.now(timezone.utc)
    if mcal is None:
        # Fallback גס: ב'-ו' (UTC weekday 0-4), 13:30-20:00 UTC (מכסה EST/EDT)
        return now_utc.weekday() < 5 and 13 * 60 + 30 <= now_utc.hour * 60 + now_utc.minute <= 20 * 60
    nyse = mcal.get_calendar('NYSE')
    sched = nyse.schedule(start_date=now_utc.date(), end_date=now_utc.date())
    if sched.empty:
        return False
    open_t, close_t = sched.iloc[0]['market_open'], sched.iloc[0]['market_close']
    return open_t <= now_utc <= close_t


# ------------------------------------------------------------- אינדיקטורים
def _rsi(closes, period=14):
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(100)  # loss==0 -> RSI 100


def _ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


# ------------------------------------------------------------- ניתוח טיקר
def analyze_ticker(ticker, daily_df, intraday_df):
    """מחשב את ציון ה'הזדמנות לרווח מהיר' לטיקר בודד. מחזיר dict אם נחצה הסף, אחרת None."""
    if daily_df is None or intraday_df is None:
        return None
    if len(daily_df) < MIN_DAILY_BARS or len(intraday_df) < MIN_INTRADAY_BARS:
        return None

    daily_close = daily_df['Close'].dropna()
    rsi_series = _rsi(daily_close, 14)
    ema20 = _ema(daily_close, 20)
    rsi14 = float(rsi_series.iloc[-1])
    trend_ok = bool(daily_close.iloc[-1] > ema20.iloc[-1])

    price = float(intraday_df['Close'].iloc[-1])
    vol_recent = float(intraday_df['Volume'].iloc[-3:].sum())
    baseline_window = intraday_df['Volume'].iloc[-23:-3]
    vol_baseline = float(baseline_window.mean()) * 3 if len(baseline_window) else 0.0
    rel_vol = (vol_recent / vol_baseline) if vol_baseline > 0 else 0.0

    lookback = min(5, len(intraday_df) - 1)
    price_1h_ago = float(intraday_df['Close'].iloc[-1 - lookback])
    momentum_pct = ((price / price_1h_ago) - 1) * 100 if price_1h_ago else 0.0

    prior_bars = intraday_df.iloc[-21:-1]
    rolling_high = float(prior_bars['High'].max()) if len(prior_bars) else price
    breakout = price > rolling_high

    # --- דלת כניסה: חייבים גם נפח חריג וגם מומנטום חיובי ---
    if rel_vol < VOL_SPIKE_MIN or momentum_pct < MOMENTUM_MIN_PCT:
        return None

    score = 0.0
    score += 30 + min((rel_vol - VOL_SPIKE_MIN) * 5, 10)          # 30-40
    score += 25 + min((momentum_pct - MOMENTUM_MIN_PCT) * 5, 15)  # 25-40
    if breakout:
        score += 15
    if trend_ok:
        score += 10
    if not (RSI_MIN <= rsi14 <= RSI_MAX):
        score -= 20

    if score < SIGNAL_THRESHOLD:
        return None

    recent_low = float(intraday_df['Low'].iloc[-5:].min())
    stop_loss = min(price * (1 - STOP_LOSS_PCT), recent_low)
    target = price * (1 + TARGET_PCT)

    return {
        'ticker': ticker,
        'price': round(price, 2),
        'rel_vol': round(rel_vol, 2),
        'momentum_pct': round(momentum_pct, 2),
        'rsi14': round(rsi14, 1),
        'breakout': breakout,
        'trend_ok': trend_ok,
        'score': round(min(score, 100), 1),
        'stop_loss': round(stop_loss, 2),
        'target': round(target, 2),
    }


# ------------------------------------------------------------- שליפת דאטה
def _chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _split_multi(df, tickers):
    """מפרק DataFrame מרובה-טיקרים (group_by='ticker') למילון {ticker: df}."""
    out = {}
    if df is None or df.empty:
        return out
    single = len(tickers) == 1
    for t in tickers:
        try:
            sub = df if single else df[t]
        except (KeyError, TypeError):
            continue
        sub = sub.dropna(how='all')
        if not sub.empty:
            out[t] = sub
    return out


def fetch_universe_data(tickers, sleep_between_chunks=CHUNK_SLEEP_SEC):
    """שולף בבאצ'ים נתוני יומי + תוך-יומי לכל הטיקרים. מחזיר (daily_map, intraday_map, failed).
    מוגבל בתקציב זמן כולל (SCAN_TIME_BUDGET_SEC) כדי לא לחסום את ה-worker אם yfinance/Yahoo תקוע."""
    daily_map, intraday_map = {}, {}
    chunks = list(_chunked(tickers, CHUNK_SIZE))
    start_time = time.monotonic()
    scanned_chunks = 0
    for idx, chunk in enumerate(chunks):
        if time.monotonic() - start_time > SCAN_TIME_BUDGET_SEC:
            break
        try:
            daily = yf.download(chunk, period=DAILY_PERIOD, interval='1d',
                                 group_by='ticker', progress=False, auto_adjust=False,
                                 threads=True, timeout=REQUEST_TIMEOUT_SEC)
            intraday = yf.download(chunk, period=INTRADAY_PERIOD, interval=INTRADAY_INTERVAL,
                                    group_by='ticker', progress=False, auto_adjust=False,
                                    threads=True, timeout=REQUEST_TIMEOUT_SEC)
            daily_map.update(_split_multi(daily, chunk))
            intraday_map.update(_split_multi(intraday, chunk))
        except Exception:  # noqa: BLE001 — צ'אנק בעייתי לא מפיל את כל הסריקה
            pass
        scanned_chunks += 1
        if idx < len(chunks) - 1:
            time.sleep(sleep_between_chunks)

    attempted = set(t for c in chunks[:scanned_chunks] for t in c)
    covered = set(daily_map) | set(intraday_map)
    failed = sorted(attempted - covered) + sorted(set(tickers) - attempted)
    return daily_map, intraday_map, failed


# ------------------------------------------------------------- ריצה מלאה
def run_scan(tickers=None):
    """מריץ סריקה מלאה ומחזיר (signals: list[dict], meta: dict)."""
    tickers = tickers or get_sp500_tickers()
    daily_map, intraday_map, failed = fetch_universe_data(tickers)

    signals = []
    for ticker in tickers:
        daily_df = daily_map.get(ticker)
        intraday_df = intraday_map.get(ticker)
        try:
            result = analyze_ticker(ticker, daily_df, intraday_df)
        except Exception:  # noqa: BLE001 — טיקר בעייתי בודד לא מפיל את הסריקה
            result = None
        if result:
            signals.append(result)

    signals.sort(key=lambda s: s['score'], reverse=True)
    meta = {
        'tickers_scanned': len(tickers),
        'tickers_with_data': len(set(daily_map) & set(intraday_map)),
        'tickers_failed': len(failed),
        'signals_found': len(signals),
    }
    return signals, meta
