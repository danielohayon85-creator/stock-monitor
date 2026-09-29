# -*- coding: utf-8 -*-
"""סקריפט קרון: מפעיל סריקת מניות דרך נקודת הקצה /api/scan.
מיועד לרוץ כ-Render Cron Job כל 15 דקות בחלון שעות המסחר (הסקריפט עצמו
בודק אם השוק פתוח ומדלג אם לא). אינו תלוי בחבילות חיצוניות (stdlib בלבד)."""
import os
import sys
import urllib.request

url = os.environ['APP_URL'].rstrip('/') + '/api/scan?token=' + os.environ.get('CRON_TOKEN', '')
req = urllib.request.Request(url, method='POST')
try:
    with urllib.request.urlopen(req, timeout=280) as r:
        print('scan triggered:', r.status, r.read().decode('utf-8')[:500])
except Exception as e:  # noqa: BLE001
    print('scan failed:', e, file=sys.stderr)
    sys.exit(1)
