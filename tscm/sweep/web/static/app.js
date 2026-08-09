/* sweep web UI
 *
 * No framework, no build step, no dependencies. The whole app is ~400 lines
 * because the server already does the hard work — it ships fully-resolved
 * device objects, so the client renders and never reasons.
 *
 * Live updates arrive over Server-Sent Events rather than WebSockets: one-way
 * push is all this needs, EventSource reconnects on its own, and it survives
 * an iPhone locking and waking without any reconnect logic of ours.
 */
'use strict';

(() => {

// ── state ────────────────────────────────────────────────────────────

const S = {
  state: null,
  filter: 'all',
  openId: null,       // device shown in the detail sheet
  view: 'list',       // 'list' | 'find'
  source: null,
  connected: false,
};

const $  = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};

// ── heat colours, matched to the CSS tokens ──────────────────────────

const HEAT = {
  hot:         { css: 'var(--ok)',       word: 'MUCH WARMER', arrow: '▲▲' },
  warmer:      { css: 'var(--ok)',       word: 'WARMER',      arrow: '▲'  },
  steady:      { css: 'var(--warn)',     word: 'SAME SPOT',   arrow: '•'  },
  cooler:      { css: 'var(--info)',     word: 'COOLER',      arrow: '▼'  },
  cold:        { css: 'var(--info)',     word: 'MUCH COOLER', arrow: '▼▼' },
  lost:        { css: 'var(--fg-faint)', word: 'SIGNAL LOST', arrow: '??' },
  calibrating: { css: 'var(--fg-faint)', word: 'CALIBRATING', arrow: '··' },
};

const CLASS_TAG = {
  tracker: 'crit', camera: 'crit', microphone: 'crit', covert: 'crit',
  jammer: 'crit', beacon: 'warn',
};

// ── networking ───────────────────────────────────────────────────────

/* The token, when the server is bound off-loopback. It arrives in the URL,
   and the server also sets a cookie, so same-origin fetches work either way. */
const TOKEN = new URLSearchParams(location.search).get('t');
const withToken = (url) => TOKEN ? url + (url.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(TOKEN) : url;

async function post(body) {
  try {
    const res = await fetch(withToken('/api/action'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return await res.json();
  } catch (err) {
    toast('Lost contact with the sweep engine.');
    return null;
  }
}

function connect() {
  if (S.source) S.source.close();
  const source = new EventSource(withToken('/api/events'));
  S.source = source;

  source.addEventListener('state', (ev) => {
    S.connected = true;
    try { S.state = JSON.parse(ev.data); } catch { return; }
    render();
  });

  source.addEventListener('finding', (ev) => {
    let f;
    try { f = JSON.parse(ev.data); } catch { return; }
    if (f.severity >= 2) {
      toast(`${f.severity_label.toUpperCase()}: ${f.device} — ${f.title}`, 7000);
      // A critical finding is the one moment a buzz is warranted.
      if (f.severity >= 4 && navigator.vibrate) navigator.vibrate([90, 60, 90]);
    }
  });

  source.onerror = () => {
    S.connected = false;
    $('live-dot').className = 'brand-dot lost';
    // EventSource reconnects on its own; we only reflect the state.
  };
}

// ── rendering ────────────────────────────────────────────────────────

function render() {
  if (!S.state) return;
  renderHeader();
  if (S.view === 'find') renderFind();
  else renderList();
  if (S.openId) renderSheet();
}

function renderHeader() {
  const st = S.state.stats || {};
  $('stat-present').textContent = st.present ?? 0;
  $('stat-total').textContent   = st.devices ?? 0;
  $('stat-loc').textContent     = (S.state.epoch ?? 0) + 1;
  $('live-dot').className = 'brand-dot ' + (S.connected ? 'live' : 'lost');

  const bands = $('bands');
  bands.replaceChildren();
  for (const s of S.state.sensors || []) {
    const chip = el('span', 'band ' + (s.available ? 'on' : 'off'), s.name);
    chip.title = s.available
      ? `${s.reason} — ${s.observations} packets`
      : `not covered: ${s.reason}${s.hint ? ' · ' + s.hint : ''}`;
    chip.setAttribute('role', 'listitem');
    bands.append(chip);
  }

  const crit = (S.state.devices || []).filter((d) => d.risk >= 3);
  const strip = $('alert-strip');
  if (crit.length) {
    strip.textContent = crit.length === 1
      ? `${crit[0].name} — ${crit[0].findings[0]?.title || 'flagged'}`
      : `${crit.length} devices flagged high or critical`;
    strip.hidden = false;
  } else {
    strip.hidden = true;
  }
}

function visibleDevices() {
  const all = S.state.devices || [];
  switch (S.filter) {
    case 'alerts':   return all.filter((d) => d.risk >= 2);
    case 'trackers': return all.filter((d) => d.class === 'tracker');
    case 'cameras':  return all.filter((d) => d.class === 'camera' || d.class === 'microphone');
    case 'unknown':  return all.filter((d) => d.class === 'unknown' && d.trust === 'unset');
    case 'mine':     return all.filter((d) => d.trust === 'mine' || d.trust === 'known');
    default:         return all;
  }
}

function renderList() {
  $('view-list').hidden = false;
  $('view-find').hidden = true;

  const devices = visibleDevices();
  const list = $('devices');
  list.replaceChildren();

  $('empty').hidden = devices.length > 0;
  if (!devices.length) {
    const active = (S.state.sensors || []).filter((s) => s.available);
    $('empty-body').textContent = active.length
      ? 'Sensors are running but nothing matches this filter yet.'
      : 'No sensors are available. Run `sweep doctor` to see what is missing.';
    return;
  }

  for (const d of devices) list.append(deviceRow(d));
}

function deviceRow(d) {
  const li = el('li');
  const row = el('button', `row r${d.risk}` + (d.trust === 'mine' ? ' mine' : ''));
  row.type = 'button';
  row.append(el('span', 'row-bar'));

  const main = el('div', 'row-main');
  main.append(el('div', 'row-name', d.name));
  main.append(el('div', 'row-sub', d.attributes?.summary || d.attributes?.class_reason || d.address));

  const tags = el('div', 'row-tags');
  if (d.risk >= 2 && d.findings?.length) {
    tags.append(el('span', `tag t-${d.risk >= 4 ? 'crit' : d.risk >= 3 ? 'hot' : 'warn'}`,
                   d.findings[0].severity_label));
  }
  const ct = CLASS_TAG[d.class];
  if (ct && d.risk < 2) tags.append(el('span', `tag t-${ct}`, d.class));
  for (const b of d.bands || []) tags.append(el('span', 'tag t-band', b));
  if (d.trust === 'mine') tags.append(el('span', 'tag t-mine', 'mine'));
  if (tags.childElementCount) main.append(tags);
  row.append(main);

  const sig = el('div', 'row-sig');
  sig.append(bars(d.rssi));
  sig.append(el('div', 'row-dbm', d.rssi == null ? '—' : `${Math.round(d.rssi)}`));
  const dist = fmtDist(d.distance_m_estimate);
  if (dist) sig.append(el('div', 'row-dist', dist));
  row.append(sig);

  row.addEventListener('click', () => openSheet(d.id));
  li.append(row);
  return li;
}

/* Distance comes from a path-loss model that is routinely wrong by a factor of
   two indoors. Printing "~0.16 m" claims centimetre precision the physics does
   not support, so the formatting is deliberately coarse. */
function fmtDist(m) {
  if (m == null || !isFinite(m) || m > 300) return null;
  if (m < 1)  return '<1 m';
  if (m < 10) return `~${m.toFixed(1)} m`;
  return `~${Math.round(m)} m`;
}

function bars(rssi) {
  const wrap = el('div', 'bars');
  // -100 dBm is the practical noise floor, -35 is where a phone at arm's
  // length saturates; five bars across that range.
  const lit = rssi == null ? 0 : Math.max(0, Math.min(5, Math.round((rssi + 100) / 13)));
  for (let i = 0; i < 5; i++) wrap.append(el('i', i < lit ? 'on' : ''));
  return wrap;
}

// ── detail sheet ─────────────────────────────────────────────────────

async function openSheet(id) {
  S.openId = id;
  $('sheet').hidden = false;
  $('scrim').hidden = window.matchMedia('(min-width: 860px)').matches;
  renderSheet();
}

function closeSheet() {
  S.openId = null;
  $('sheet').hidden = true;
  $('scrim').hidden = true;
}

function currentDevice() {
  return (S.state?.devices || []).find((d) => d.id === S.openId);
}

function renderSheet() {
  const d = currentDevice();
  if (!d) return;

  $('d-name').textContent = d.name;
  $('d-sub').textContent  = `${d.address} · ${d.class}`;

  const body = $('d-body');
  body.replaceChildren();

  if (d.findings?.length) {
    const sec = section('Findings');
    for (const f of d.findings) {
      const box = el('div', `finding s${f.severity}`);
      box.append(el('span', 'sev', f.severity_label));
      box.append(el('h4', null, f.title));
      box.append(el('p', null, f.detail));
      sec.append(box);
    }
    body.append(sec);
  }

  const idSec = section('Identity');
  idSec.append(kv({
    'Class':    `${d.class} — ${d.attributes?.class_reason || ''}`,
    'Vendor':   d.vendor || 'unknown',
    'Model':    d.model || '—',
    'OS':       d.os_hint || '—',
    'Trust':    d.trust,
    'Radios':   (d.bands || []).join(', '),
    'Signal':   d.rssi == null ? '—' : `${d.rssi} dBm  (${fmtDist(d.distance_m_estimate) || 'distance unknown'} est.)`,
    'Seen for': `${((d.last_seen - d.first_seen) / 60).toFixed(1)} min`,
    'Locations': String(d.epochs_seen ?? 1),
    'Earlier sweeps': String(d.seen_in_previous_sessions ?? 0),
  }));
  body.append(idSec);

  if (d.tracks?.length) {
    const sec = section('Radios');
    const map = {};
    for (const t of d.tracks) {
      map[`${t.band} ${t.address}`] =
        `${t.packets} pkts · ${t.rssi ?? '?'} dBm (min ${t.rssi_min} / max ${t.rssi_max})`;
    }
    sec.append(kv(map));
    body.append(sec);
  }

  if (d.identity_links?.length) {
    const sec = section('Why we think this is one device');
    for (const l of d.identity_links) {
      const p = el('div', 'link-note');
      p.append(el('b', null, l.confidence.toFixed(2)));
      p.append(document.createTextNode(` — ${l.reason} (from ${l.previous_address})`));
      sec.append(p);
    }
    body.append(sec);
  }

  const attrs = d.attributes || {};
  const skip = new Set(['summary', 'class_reason', 'signatures', 'signature_labels',
                        'mac', 'rotating', 'class_hint', 'device_class']);
  const shown = {};
  for (const k of Object.keys(attrs).sort()) {
    if (skip.has(k) || k.startsWith('mfr_data_') || k.startsWith('svc_data_')) continue;
    const v = attrs[k];
    if (v == null || v === '' || (Array.isArray(v) && !v.length)) continue;
    shown[k] = Array.isArray(v) ? v.join(', ') : String(v);
  }
  if (Object.keys(shown).length) {
    const sec = section('Everything decoded');
    sec.append(kv(shown));
    body.append(sec);
  }
}

function section(title) {
  const s = el('div', 'd-section');
  s.append(el('h3', null, title));
  return s;
}

function kv(map) {
  const dl = el('dl', 'kv');
  for (const [k, v] of Object.entries(map)) {
    dl.append(el('dt', null, k));
    dl.append(el('dd', null, v));
  }
  return dl;
}

// ── find view ────────────────────────────────────────────────────────

async function startFind(id) {
  const res = await post({ action: 'target', device: id });
  if (!res?.ok) { toast('Could not start ranging on that device.'); return; }
  S.view = 'find';
  closeSheet();
  render();
}

async function stopFind() {
  await post({ action: 'target', device: null });
  S.view = 'list';
  render();
}

function renderFind() {
  $('view-list').hidden = true;
  $('view-find').hidden = false;

  const r = S.state.range || {};
  if (!r.active) { $('find-name').textContent = 'No target'; return; }

  const heat = HEAT[r.heat] || HEAT.calibrating;
  document.documentElement.style.setProperty('--heat', heat.css);

  $('find-name').textContent = r.name || '—';
  $('find-meta').textContent = [r.address, r.class, r.vendor].filter(Boolean).join(' · ');
  $('find-dbm').textContent  = r.current_dbm == null ? '—' : String(Math.round(r.current_dbm));
  $('find-arrow').textContent = heat.arrow;
  $('find-word').textContent  = heat.word;

  const pct = r.current_dbm == null ? 0
            : Math.max(0, Math.min(100, ((r.current_dbm + 100) / 65) * 100));
  $('meter-fill').style.width = pct.toFixed(1) + '%';

  $('find-note').textContent = r.note || (
    r.delta_db
      ? `${r.delta_db > 0 ? '+' : ''}${r.delta_db} dB against the rolling baseline — ` +
        `roughly ${r.distance_ratio}× the distance, ${r.delta_db > 0 ? 'closer' : 'farther'}.`
      : ''
  );

  $('fact-dist').textContent    = fmtDist(r.distance_m) || '—';
  $('fact-delta').textContent   = r.delta_db == null ? '—' : `${r.delta_db > 0 ? '+' : ''}${r.delta_db} dB`;
  $('fact-packets').textContent = `${r.samples_recent}/${r.samples_total}`;
  $('fact-age').textContent     = r.age_s == null || !isFinite(r.age_s) ? '—' : `${r.age_s}s`;

  drawSpark(r.history || []);
}

function drawSpark(history) {
  const svg = $('spark');
  svg.replaceChildren();
  if (history.length < 2) return;

  const W = 300, H = 60, pad = 4;
  const lo = Math.min(...history), hi = Math.max(...history);
  // A flat trace would divide by zero; give it a nominal 1 dB span so a
  // steady signal renders as a centred line rather than vanishing.
  const span = Math.max(1, hi - lo);
  const pt = (v, i) => [
    pad + (i / (history.length - 1)) * (W - pad * 2),
    H - pad - ((v - lo) / span) * (H - pad * 2),
  ];

  const path = history.map((v, i) => {
    const [x, y] = pt(v, i);
    return `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(' ');

  const area = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  area.setAttribute('class', 'area');
  area.setAttribute('d', `${path} L${W - pad} ${H} L${pad} ${H} Z`);
  svg.append(area);

  const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  line.setAttribute('d', path);
  svg.append(line);
}

// ── toast ────────────────────────────────────────────────────────────

let toastTimer = null;
function toast(text, ms = 3500) {
  const t = $('toast');
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

// ── theme ────────────────────────────────────────────────────────────

function applyTheme(mode) {
  document.documentElement.setAttribute('data-theme', mode);
  try { localStorage.setItem('sweep-theme', mode); } catch { /* private mode */ }
}

// ── wiring ───────────────────────────────────────────────────────────

function init() {
  try {
    const saved = localStorage.getItem('sweep-theme');
    if (saved) document.documentElement.setAttribute('data-theme', saved);
  } catch { /* private mode */ }

  $('btn-theme').addEventListener('click', () => {
    const now = document.documentElement.getAttribute('data-theme');
    applyTheme(now === 'dark' ? 'light' : now === 'light' ? 'auto' : 'dark');
  });

  for (const chip of document.querySelectorAll('.chip[data-filter]')) {
    chip.addEventListener('click', () => {
      S.filter = chip.dataset.filter;
      for (const c of document.querySelectorAll('.chip[data-filter]')) {
        c.classList.toggle('is-on', c === chip);
      }
      renderList();
    });
  }

  $('btn-epoch').addEventListener('click', async () => {
    const res = await post({ action: 'epoch' });
    if (res?.ok) {
      toast(`Location ${res.epoch + 1} marked. Anything that follows you across ` +
            `locations will be flagged.`, 5000);
      if (navigator.vibrate) navigator.vibrate(25);
    }
  });

  $('btn-baseline').addEventListener('click', async () => {
    const res = await post({ action: 'baseline' });
    if (res?.ok) toast(`Baseline set: ${res.baseline} devices marked as already present.`);
  });

  $('btn-report').addEventListener('click', () => {
    $('btn-report').href = withToken('/api/report');
  });

  $('d-close').addEventListener('click', closeSheet);
  $('scrim').addEventListener('click', closeSheet);
  $('d-find').addEventListener('click', () => S.openId && startFind(S.openId));
  $('find-back').addEventListener('click', stopFind);

  for (const btn of document.querySelectorAll('[data-trust]')) {
    btn.addEventListener('click', async () => {
      if (!S.openId) return;
      const res = await post({ action: 'trust', device: S.openId, trust: btn.dataset.trust });
      if (res?.ok) toast(`Marked as ${btn.dataset.trust}.`);
    });
  }

  document.addEventListener('keydown', (ev) => {
    if (ev.target.tagName === 'INPUT') return;
    if (ev.key === 'Escape') { S.view === 'find' ? stopFind() : closeSheet(); }
    if (ev.key === 'm') $('btn-epoch').click();
    if (ev.key === 'b') $('btn-baseline').click();
    if (ev.key === 'f' && S.openId) startFind(S.openId);
  });

  // iOS suspends timers and connections when Safari is backgrounded; the
  // stream is stale on return, so force a fresh one.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && (!S.source || S.source.readyState === 2)) connect();
  });

  connect();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

})();
