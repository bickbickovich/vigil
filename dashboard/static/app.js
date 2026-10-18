function fmtMinutes(seconds) {
  const m = Math.floor(seconds / 60);
  const h = Math.floor(m / 60);
  const mm = m % 60;
  if (h) return `${h}h ${mm}m`;
  return `${mm}m`;
}

function fmtClock(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function fmtCountdown(seconds) {
  if (seconds <= 0) return '0:00';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

async function api(path, body) {
  const opts = body !== undefined
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    : { method: 'POST' };
  const res = await fetch(path, opts);
  return res.json();
}

function renderDonut(today) {
  const total = today.productive + today.distracting + today.neutral;
  const svg = document.getElementById('donut');
  const r = 50, cx = 60, cy = 60, circumference = 2 * Math.PI * r;
  let offset = 0;
  const segs = [
    ['productive', today.productive],
    ['distracting', today.distracting],
    ['neutral', today.neutral],
  ];
  let paths = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--neutral-soft)" stroke-width="14"/>`;
  if (total > 0) {
    for (const [key, val] of segs) {
      if (val <= 0) continue;
      const frac = val / total;
      const len = frac * circumference;
      paths += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--${key})" stroke-width="14"
        stroke-dasharray="${len} ${circumference - len}" stroke-dashoffset="${-offset}"
        transform="rotate(-90 ${cx} ${cy})" stroke-linecap="butt"/>`;
      offset += len;
    }
  }
  const centerLabel = total > 0
    ? `<text x="60" y="56" text-anchor="middle" font-size="15" font-weight="700" fill="var(--text)">${fmtMinutes(total)}</text>
       <text x="60" y="72" text-anchor="middle" font-size="9" fill="var(--muted-2)">tracked today</text>`
    : `<text x="60" y="64" text-anchor="middle" font-size="10" fill="var(--muted-2)">no data yet</text>`;
  svg.innerHTML = paths + centerLabel;

  const legend = document.getElementById('legend');
  legend.innerHTML = segs.map(([key, val]) => `
    <li><span class="dot" style="background:var(--${key})"></span>
      <span class="label">${key[0].toUpperCase()}${key.slice(1)}</span>
      <span class="value">${fmtMinutes(val)}</span></li>`).join('');
}

function renderWeek(week) {
  const svg = document.getElementById('weekChart');
  const W = 560, H = 160, padBottom = 22, padTop = 10;
  const barW = 34, gap = (W - barW * 7) / 8;
  const max = Math.max(1, ...week.map(d => d.productive + d.distracting + d.neutral));
  const scale = (H - padBottom - padTop) / max;

  let bars = '';
  week.forEach((d, i) => {
    const x = gap + i * (barW + gap);
    const pH = d.productive * scale;
    const dH = d.distracting * scale;
    const nH = d.neutral * scale;
    let y = H - padBottom;
    const segs = [['neutral', nH], ['distracting', dH], ['productive', pH]];
    let rects = '';
    for (const [key, h] of segs) {
      if (h <= 0.5) continue;
      y -= h;
      rects += `<rect x="${x}" y="${y}" width="${barW}" height="${h}" fill="var(--${key})" rx="3"/>`;
    }
    if (!rects) {
      rects = `<rect x="${x}" y="${H - padBottom - 3}" width="${barW}" height="3" fill="var(--neutral-soft)" rx="1.5"/>`;
    }
    bars += rects;
    bars += `<text x="${x + barW / 2}" y="${H - 4}" text-anchor="middle" font-size="10.5" fill="var(--muted-2)">${d.label}</text>`;
  });
  svg.innerHTML = bars;
}

function renderStatus(state) {
  const badge = document.getElementById('statusBadge');
  const pauseBtn = document.getElementById('pauseBtn');
  if (state.paused) {
    badge.textContent = 'Paused';
    badge.className = 'badge paused';
    pauseBtn.textContent = 'Resume';
  } else {
    badge.textContent = 'Active';
    badge.className = 'badge';
    pauseBtn.textContent = 'Pause';
  }
}

function renderFocus(state) {
  const el = document.getElementById('focusBody');
  const s = state.focus_session;
  if (!s) {
    el.innerHTML = `
      <p class="hint" style="margin-bottom:2px;">Block distracting apps/sites for a set time.</p>
      <div class="focus-idle">
        <button class="chip" data-min="25">25 min</button>
        <button class="chip" data-min="50">50 min</button>
        <button class="chip" data-min="90">90 min</button>
      </div>`;
    el.querySelectorAll('.chip').forEach(btn => {
      btn.onclick = async () => {
        await api('/api/focus/start', { minutes: parseInt(btn.dataset.min, 10) });
        load();
      };
    });
    return;
  }
  const remaining = Math.max(0, Math.round((new Date(s.effective_ends_at) - new Date()) / 1000));
  el.innerHTML = `
    <div class="focus-active ${s.ending ? 'focus-ending' : ''}">
      <div class="fa-left">
        <span class="fa-time">${fmtCountdown(remaining)}</span>
        <span class="fa-sub">${s.ending ? 'ending shortly…' : `until ${fmtClock(s.effective_ends_at)}`}</span>
      </div>
      <button class="btn ${s.ending ? '' : 'btn-danger'} btn-sm" id="focusEndBtn">
        ${s.ending ? 'Cancel end' : 'End session'}
      </button>
    </div>`;
  document.getElementById('focusEndBtn').onclick = async () => {
    await api(s.ending ? '/api/focus/cancel_end' : '/api/focus/end_request');
    load();
  };
}

function renderLocks(state) {
  const list = document.getElementById('lockList');
  if (state.self_locks.length === 0) {
    list.innerHTML = `<li class="log-empty">No distracting apps/sites configured yet — add one below.</li>`;
    return;
  }
  list.innerHTML = state.self_locks.map(l => {
    const isOn = l.status === 'locked' || l.status === 'unlocking';
    const pending = l.status === 'unlocking';
    let statusText = '';
    if (l.status === 'unlocking') {
      const remain = Math.max(0, Math.round((new Date(l.unlocks_at) - new Date()) / 1000));
      statusText = `unlocking in ${fmtCountdown(remain)}`;
    } else if (l.status === 'locked') {
      statusText = 'locked';
    }
    return `
      <li>
        <span class="lock-target">${escapeHtml(l.target)} <span class="lock-type">${l.target_type === 'domain' ? 'site' : 'app'}</span></span>
        <span style="display:flex;align-items:center;">
          ${statusText ? `<span class="lock-status">${statusText}</span>` : ''}
          <button class="toggle ${isOn ? (pending ? 'on pending' : 'on') : ''}" data-target="${escapeAttr(l.target)}" data-type="${l.target_type}"></button>
        </span>
      </li>`;
  }).join('');
  list.querySelectorAll('.toggle').forEach(btn => {
    btn.onclick = async () => {
      await api('/api/lock', { target: btn.dataset.target, target_type: btn.dataset.type });
      load();
    };
  });
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function escapeAttr(s) { return escapeHtml(s); }

const KIND_LABELS = {
  nudge: 'Nudge', self_block: 'Self-lock enforced', self_lock_created: 'Locked',
  self_lock_unlock_requested: 'Unlock started', self_lock_relocked: 'Unlock cancelled',
  paused: 'Paused', resumed: 'Resumed', tracker_health: 'Needs attention',
  focus_session_start: 'Focus session started', focus_session_block: 'Focus session enforced',
  focus_session_end_requested: 'Ending focus session', focus_session_resumed: 'Focus session resumed',
  settings_updated: 'Settings updated',
};

function renderLog(state) {
  const list = document.getElementById('logList');
  if (state.interventions.length === 0) {
    list.innerHTML = `<li class="log-empty">Nothing logged yet.</li>`;
    return;
  }
  list.innerHTML = state.interventions.map(r => `
    <li>
      <span class="log-time">${fmtClock(r.ts)}</span>
      <span class="log-kind">${KIND_LABELS[r.kind] || r.kind}</span>
      <span class="log-msg">${escapeHtml(r.message || '')}</span>
    </li>`).join('');
}

let lastCategories = null;

function openSettings(categories) {
  lastCategories = categories;
  document.getElementById('prodApps').value = (categories.productive?.apps || []).join('\n');
  document.getElementById('prodDomains').value = (categories.productive?.domains || []).join('\n');
  document.getElementById('distApps').value = (categories.distracting?.apps || []).join('\n');
  document.getElementById('distDomains').value = (categories.distracting?.domains || []).join('\n');
  document.getElementById('settingsModal').classList.remove('hidden');
}

function linesToList(text) {
  return text.split('\n').map(s => s.trim()).filter(Boolean);
}

document.getElementById('settingsBtn').onclick = () => openSettings(lastCategories || { productive: {}, distracting: {} });
document.getElementById('closeSettings').onclick = () => document.getElementById('settingsModal').classList.add('hidden');
document.getElementById('settingsModal').addEventListener('click', e => {
  if (e.target.id === 'settingsModal') e.target.classList.add('hidden');
});
document.getElementById('saveSettings').onclick = async () => {
  const categories = {
    productive: { apps: linesToList(document.getElementById('prodApps').value), domains: linesToList(document.getElementById('prodDomains').value) },
    distracting: { apps: linesToList(document.getElementById('distApps').value), domains: linesToList(document.getElementById('distDomains').value) },
  };
  await api('/api/settings/categories', { categories });
  document.getElementById('settingsModal').classList.add('hidden');
  load();
};

document.getElementById('pauseBtn').onclick = async () => { await api('/api/pause'); load(); };

document.getElementById('addLockForm').onsubmit = async (e) => {
  e.preventDefault();
  const target = document.getElementById('addLockTarget').value.trim();
  const target_type = document.getElementById('addLockType').value;
  if (!target) return;
  await api('/api/lock', { target, target_type });
  document.getElementById('addLockTarget').value = '';
  load();
};

async function load() {
  const state = await fetch('/api/state').then(r => r.json());
  renderStatus(state);
  renderDonut(state.today);
  renderWeek(state.week);
  renderFocus(state);
  renderLocks(state);
  renderLog(state);
  lastCategories = state.categories;
}

load();
setInterval(load, 5000);
