# -*- coding: utf-8 -*-
"""שליחת התראות טלגרם על איתותים. דורש TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID.
ריק = שליחה מושבתת, המערכת ממשיכה לעבוד (רק בלי טלגרם)."""
import os
import requests

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
API_BASE = 'https://api.telegram.org/bot{token}/sendMessage'

DISCLAIMER = "⚠️ <i>כלי סיגנלים/מחקר בלבד — לא ייעוץ השקעות. ההחלטה והאחריות עליכם.</i>"


def telegram_enabled():
    return bool(BOT_TOKEN and CHAT_ID)


def format_signal_message(sig):
    arrow = '🚀' if sig['score'] >= 85 else '📈'
    breakout_txt = 'כן ✅' if sig['breakout'] else 'לא'
    trend_txt = 'כן ✅' if sig['trend_ok'] else 'לא'
    return (
        f"{arrow} <b>איתות: {sig['ticker']}</b>  (ציון {sig['score']:.0f}/100)\n"
        f"מחיר נוכחי: <b>${sig['price']:.2f}</b>\n"
        f"מומנטום שעה אחרונה: <b>+{sig['momentum_pct']:.1f}%</b>\n"
        f"נפח מסחר יחסי: <b>x{sig['rel_vol']:.1f}</b> מהרגיל\n"
        f"RSI(14): {sig['rsi14']:.0f}   פריצת שיא: {breakout_txt}   מגמה עולה: {trend_txt}\n"
        f"סטופ-לוס מוצע: ${sig['stop_loss']:.2f}   יעד מוצע: ${sig['target']:.2f}\n\n"
        f"{DISCLAIMER}"
    )


def send_telegram_message(text):
    if not telegram_enabled():
        return False, 'disabled'
    try:
        resp = requests.post(
            API_BASE.format(token=BOT_TOKEN),
            json={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True},
            timeout=15,
        )
        if resp.status_code == 200:
            return True, None
        return False, f'HTTP {resp.status_code}: {resp.text[:200]}'
    except requests.RequestException as e:
        return False, str(e)


def send_signal_alert(sig):
    return send_telegram_message(format_signal_message(sig))


def send_scan_error_alert(error_text):
    if not telegram_enabled():
        return False, 'disabled'
    text = f"❌ <b>סריקת מניות נכשלה</b>\n<code>{error_text[:500]}</code>"
    return send_telegram_message(text)
