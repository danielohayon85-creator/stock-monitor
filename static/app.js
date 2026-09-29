const loginView = document.getElementById('loginView');
const appView = document.getElementById('appView');
const loginForm = document.getElementById('loginForm');
const loginError = document.getElementById('loginError');

let refreshTimer = null;

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || 'שגיאה');
  return data;
}

function fmtTime(iso) {
  if (!iso) return '–';
  const d = new Date(iso);
  return d.toLocaleString('he-IL', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' });
}

function scoreBadge(score) {
  const cls = score >= 85 ? 'score-high' : 'score-mid';
  return `<span class="score-badge ${cls}">${score.toFixed(0)}</span>`;
}

function statusPill(status) {
  const map = {
    ok: ['status-ok', 'הצלחה'],
    error: ['status-error', 'שגיאה'],
    running: ['status-skipped', 'רץ'],
    skipped_market_closed: ['status-skipped', 'שוק סגור'],
  };
  const [cls, label] = map[status] || ['status-skipped', status];
  return `<span class="status-pill ${cls}">${label}</span>`;
}

async function loadStatus() {
  try {
    const s = await api('/api/status');
    const pill = document.getElementById('marketPill');
    pill.textContent = s.market_open ? '🟢 השוק פתוח' : '⚪ השוק סגור';
    pill.className = 'market-pill ' + (s.market_open ? 'open' : 'closed');

    document.getElementById('statSignalsToday').textContent = s.signals_today;
    document.getElementById('statLastScan').textContent = s.last_scan ? fmtTime(s.last_scan.started_at) : 'אין עדיין';
    document.getElementById('statTickersScanned').textContent = s.last_scan ? (s.last_scan.tickers_scanned || 0) : '–';
    document.getElementById('statTelegram').textContent = s.telegram_enabled ? '✅ מחובר' : '❌ לא מוגדר';

    const body = document.getElementById('signalsBody');
    body.innerHTML = '';
    document.getElementById('signalsEmpty').classList.toggle('hidden', s.recent_signals.length > 0);
    for (const sig of s.recent_signals) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${fmtTime(sig.ts)}</td>
        <td class="ticker">${sig.ticker}</td>
        <td>${scoreBadge(sig.score)}</td>
        <td>$${sig.price.toFixed(2)}</td>
        <td>+${sig.momentum_pct.toFixed(1)}%</td>
        <td>x${sig.rel_vol.toFixed(1)}</td>
        <td>${sig.rsi14.toFixed(0)}</td>
        <td>$${sig.stop_loss.toFixed(2)}</td>
        <td>$${sig.target.toFixed(2)}</td>
        <td>${sig.telegram_sent ? '✅' : '—'}</td>`;
      body.appendChild(tr);
    }

    const scansBody = document.getElementById('scansBody');
    scansBody.innerHTML = '';
    for (const scan of s.recent_scans) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${fmtTime(scan.started_at)}</td>
        <td>${statusPill(scan.status)}</td>
        <td>${scan.tickers_scanned || '–'}</td>
        <td>${scan.tickers_with_data || '–'}</td>
        <td>${scan.signals_found || 0}</td>`;
      scansBody.appendChild(tr);
    }
  } catch (e) {
    if (e.message === 'unauthorized') showLogin();
  }
}

function showApp() {
  loginView.classList.add('hidden');
  appView.classList.remove('hidden');
  loadStatus();
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(loadStatus, 60000);
}

function showLogin() {
  appView.classList.add('hidden');
  loginView.classList.remove('hidden');
  if (refreshTimer) clearInterval(refreshTimer);
}

loginForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  loginError.textContent = '';
  const password = document.getElementById('password').value;
  try {
    await api('/api/login', { method: 'POST', body: JSON.stringify({ password }) });
    showApp();
  } catch (err) {
    loginError.textContent = err.message;
  }
});

document.getElementById('logoutBtn').addEventListener('click', async () => {
  await api('/api/logout', { method: 'POST' });
  showLogin();
});

document.getElementById('refreshBtn').addEventListener('click', loadStatus);

(async function init() {
  try {
    const s = await api('/api/session');
    if (s.authed) showApp(); else showLogin();
  } catch {
    showLogin();
  }
})();
