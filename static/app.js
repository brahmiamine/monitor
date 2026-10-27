'use strict';
// Interface de Monitor Clubika : application monopage sans dépendance. Les mesures arrivent en direct (SSE /api/stream),
// les historiques et les contrôles par requêtes /api/*. Tout le texte inséré passe par esc() (pas d'HTML non échappé).
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const ICONS = {
  overview: '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>',
  pulse: '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"/>',
  server: '<rect width="20" height="8" x="2" y="2" rx="2"/><rect width="20" height="8" x="2" y="14" rx="2"/><path d="M6 6h.01M6 18h.01"/>',
  box: '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
  layers: '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
  bell: '<path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  close: '<path d="M18 6 6 18M6 6l12 12"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  package: '<path d="m7.5 4.27 9 5.15"/><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5M12 22V12"/>',
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  cpu: '<rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9"/><path d="M15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2"/>',
  external: '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  github: '<path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4"/><path d="M9 18c-4.51 2-5-2-7-2"/>',
  alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4M12 17h.01"/>',
};
const icon = (name) => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ''}</svg>`;
const decorate = (root = document) => $$('[data-icon]', root).forEach((el) => { if (!el.querySelector(':scope > svg.icon')) el.insertAdjacentHTML('afterbegin', icon(el.dataset.icon)); });

// ---------- Formats ----------
const UNITS = ['o', 'Ko', 'Mo', 'Go', 'To'];
const bytes = (n, digits = 1) => { if (n == null || Number.isNaN(+n)) return '—'; let i = 0, v = +n; while (v >= 1024 && i < 4) { v /= 1024; i++; } return `${v.toFixed(i ? digits : 0)} ${UNITS[i]}`; };
const rate = (n) => bytes(n) + '/s';
const pct = (n, d = 1) => (n == null ? '—' : `${(+n).toFixed(d)} %`);
const dur = (s) => { s = Math.max(0, Math.floor(s)); const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return d ? `${d} j ${h} h` : h ? `${h} h ${m} min` : m ? `${m} min` : `${s} s`; };
const clock = (ts) => new Date(ts * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
const stamp = (ts) => (ts ? new Date(ts * 1000).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—');
const ago = (ts) => (ts ? 'il y a ' + dur(Date.now() / 1000 - ts) : 'jamais');

// ---------- API ----------
async function api(path, opt = {}) {
  const response = await fetch(path, { method: opt.method || 'GET', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'Monitor' }, body: opt.body !== undefined ? JSON.stringify(opt.body) : undefined });
  if (response.status === 401 && !path.endsWith('/login')) { showLogin(); throw new Error('Connexion requise'); }
  const isJson = (response.headers.get('content-type') || '').includes('json');
  const data = isJson ? await response.json() : null;
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(Array.isArray(detail) ? detail.map((d) => d.msg.replace(/^Value error, /, '')).join(' · ') : detail || 'Erreur ' + response.status);
  }
  return data;
}
function toast(message, bad = false) {
  const el = $('#toast'); el.textContent = message; el.className = bad ? 'error' : ''; el.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => { el.hidden = true; }, 4200);
}

// ---------- Graphiques SVG ----------
const niceMax = (v) => { if (v <= 0) return 1; const p = 10 ** Math.floor(Math.log10(v)); return [1, 2, 5, 10].map((m) => m * p).find((m) => m >= v); };
function chart(series, { max, fmt = (v) => v, height = 220 } = {}) {
  const W = 640, H = height, L = 52, R = 10, T = 10, B = 24;
  const pts = series.flatMap((s) => s.values);
  if (pts.length < 2) return '<div class="empty">Pas encore assez de mesures pour tracer la courbe.</div>';
  const t0 = Math.min(...pts.map((p) => p[0])), t1 = Math.max(...pts.map((p) => p[0]));
  const top = max ?? niceMax(Math.max(...pts.map((p) => p[1] ?? 0)) * 1.1);
  const x = (t) => L + ((t - t0) / Math.max(1, t1 - t0)) * (W - L - R), y = (v) => T + (1 - Math.min(v, top) / top) * (H - T - B);
  const grid = [0, .25, .5, .75, 1].map((f) => `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(top * f)}" y2="${y(top * f)}"/><text x="${L - 8}" y="${y(top * f) + 3}" text-anchor="end">${esc(fmt(top * f))}</text>`).join('');
  const labels = [0, .5, 1].map((f) => `<text x="${x(t0 + (t1 - t0) * f)}" y="${H - 6}" text-anchor="${f === 0 ? 'start' : f === 1 ? 'end' : 'middle'}">${clock(t0 + (t1 - t0) * f)}</text>`).join('');
  const lines = series.map((s, i) => {
    const d = s.values.filter((p) => p[1] != null).map((p, j) => `${j ? 'L' : 'M'}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`).join('');
    const area = i === 0 && d ? `<path class="area" d="${d}L${x(t1)} ${y(0)}L${x(t0)} ${y(0)}Z" opacity=".55"/>` : '';
    return area + `<path class="plot ${i ? 'secondary' : ''}" d="${d}"/>`;
  }).join('');
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(series.map((s) => s.name).join(', '))}" style="height:${height}px">${grid}${lines}${labels}</svg>`;
}
const legend = (...names) => `<div class="legend">${names.map((n) => `<span><i></i>${esc(n)}</span>`).join('')}</div>`;
const panel = (title, body, { note = '', tag = '' } = {}) => `<section class="panel"><div class="panel-heading"><div><h2>${esc(title)}</h2>${note ? `<p>${esc(note)}</p>` : ''}</div>${tag}</div>${body}</section>`;
const meter = (value, limit = 90) => `<div class="meter"><span class="${value >= limit ? 'high' : ''}" style="width:${Math.min(100, Math.max(0, value || 0))}%"></span></div>`;
const table = (heads, rows, empty = 'Rien à afficher.') => `<div class="table-wrap"><table class="data-table"><thead><tr>${heads.map((h) => `<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('') || `<tr><td colspan="${heads.length}"><div class="empty">${esc(empty)}</div></td></tr>`}</tbody></table></div>`;
const badge = (kind, label) => `<span class="badge ${kind}"><span class="status-dot"></span>${esc(label)}</span>`;
function monitorState(m) {
  if (m.paused) return ['paused', 'En pause'];
  if (m.maintenance_until > Date.now() / 1000) return ['maintenance', 'Maintenance'];
  if (m.ok == null) return ['pending', 'En attente'];
  return m.ok ? ['', 'En ligne'] : ['down', 'Hors ligne'];
}
const monitorBadge = (m) => badge(...monitorState(m));

// ---------- État ----------
const S = { cfg: {}, projects: [], live: null, monitors: [], incidents: [], history: [], hours: 1, page: 'overview', last: 0, detail: null, settings: null };
const KIND = { http: 'Site HTTP(S)', keyword: 'HTTP + mot-clé', json: 'HTTP + JSON', tcp: 'Port TCP', dns: 'DNS', ping: 'Ping', docker: 'Conteneur Docker', systemd: 'Service système', push: 'Signal de vie', tls: 'Certificat TLS' };

// ---------- Pages ----------
const PAGES = {
  inventory: { title: 'Inventaire', desc: 'Ressources détectées automatiquement, fraîcheur des données et limites d’accès.', async render() {
    if (!S.inventory || Date.now() - (S.inventoryLoadedAt || 0) > 30000) {
      if (!S.inventoryRequest) S.inventoryRequest = api('/api/inventory').then((data) => { S.inventory = data; S.inventoryLoadedAt = Date.now(); }).finally(() => { S.inventoryRequest = null; });
      await S.inventoryRequest;
    }
    return MonitorInventory.renderInventory(S.inventory, S.inventoryFilters || {});
  } },
  overview: { title: 'Vue d’ensemble', desc: 'Tout ce qui fait tourner votre infrastructure, en temps réel.', live: true, render() {
    const l = S.live; if (!l?.ts) return '<div class="empty">Chargement des mesures réelles du serveur…</div>';
    const down = S.monitors.filter((m) => !m.paused && m.ok === 0), open = S.incidents.filter((i) => !i.closed);
    const root = l.disks?.find((d) => d.mount === '/') || l.disks?.[0] || {};
    const apps = S.monitors.filter((m) => m.group_name === 'Applications');
    const running = (l.containers || []).filter((c) => c.state === 'running').length;
    return `<div class="health-strip ${down.length || open.length ? 'warning' : ''}"><span class="health-symbol">${icon(down.length || open.length ? 'alert' : 'check')}</span><div><h2>${down.length ? `${down.length} contrôle(s) hors ligne` : open.length ? `${open.length} incident(s) ouvert(s)` : 'Tout fonctionne normalement'}</h2><p>${S.monitors.length} contrôles · ${running}/${(l.containers || []).length} conteneurs actifs · ${esc(l.hostname || '')} en ligne depuis ${dur(l.uptime)}</p></div><span class="right">Dernière mesure ${clock(l.ts)}</span></div>
    <div class="metric-grid">
      <div class="metric"><div class="metric-label">Processeur</div><div class="metric-value">${l.cpu.toFixed(1)}<small> %</small></div><div class="metric-note">${l.cores.length} cœurs · charge ${l.load_avg.map((v) => v.toFixed(2)).join(' / ')}</div>${meter(l.cpu)}</div>
      <div class="metric"><div class="metric-label">Mémoire</div><div class="metric-value">${l.ram.toFixed(1)}<small> %</small></div><div class="metric-note">${bytes(l.memory.used)} sur ${bytes(l.memory.total)}</div>${meter(l.ram)}</div>
      <div class="metric"><div class="metric-label">Disque</div><div class="metric-value">${(root.percent ?? 0).toFixed(1)}<small> %</small></div><div class="metric-note">${bytes(root.used)} sur ${bytes(root.total)}</div>${meter(root.percent)}</div>
      <div class="metric"><div class="metric-label">Réseau</div><div class="metric-value" style="font-size:19px">↓ ${rate(l.rx)}</div><div class="metric-note">↑ ${rate(l.tx)}</div></div></div>
    <div class="dashboard-grid">${panel('Processeur et mémoire', `<div class="chart-wrap">${chart([{ name: 'CPU', values: S.history.map((p) => [p.ts, p.cpu]) }, { name: 'Mémoire', values: S.history.map((p) => [p.ts, p.ram]) }], { max: 100, fmt: (v) => Math.round(v) + '%' })}</div>${legend('Processeur', 'Mémoire')}`, { note: 'Dernière heure' })}
    ${panel('Applications', `<div class="mini-list">${apps.map((m) => `<div class="mini-row"><div><strong>${esc(m.name)}</strong><small>${m.latency != null ? Math.round(m.latency) + ' ms · ' : ''}${m.uptime != null ? m.uptime + ' % sur 24 h' : 'en attente'}</small></div><span class="right">${monitorBadge(m)}</span></div>`).join('') || '<div class="empty">Aucune application surveillée.</div>'}</div>`, { tag: '<a href="#monitors">Tout voir →</a>' })}</div>
    <div class="split-grid">${panel('Conteneurs', `<div class="mini-list">${(l.containers || []).slice(0, 8).map((c) => `<div class="mini-row"><div><strong>${esc(c.name)}</strong><small>${esc(c.image)}</small></div><span class="right">${esc(c.cpu)} · ${esc(c.memory.split('/')[0])}</span></div>`).join('') || '<div class="empty">Aucun conteneur.</div>'}</div>`, { tag: '<a href="#containers">Tout voir →</a>' })}
    ${panel('Incidents ouverts', `<div class="mini-list">${open.map((i) => `<div class="mini-row"><div><strong>${esc(i.title)}</strong><small>${esc(i.message)}</small></div><span class="right">${ago(i.opened)}</span></div>`).join('') || '<div class="empty">Aucun incident. Rien à signaler.</div>'}</div>`, { tag: '<a href="#incidents">Historique →</a>' })}</div>`;
  } },

  server: { title: 'Serveur', desc: 'Processeur, mémoire, disques, réseau et processus de l’hôte.', live: true, render() {
    const l = S.live; if (!l?.ts) return '<div class="empty">Chargement…</div>';
    const H = S.history, hours = [[1, '1 h'], [6, '6 h'], [24, '24 h'], [168, '7 j'], [720, '30 j']];
    const swap = l.swap || {};
    const sensors = Object.entries(l.sensors || {}).flatMap(([n, a]) => a.map((s) => `<div class="mini-row"><strong>${esc(n)} ${esc(s.label || '')}</strong><span class="right">${s.current} °C</span></div>`));
    return `<div class="toolbar"><span class="muted" style="margin-left:0">Période des courbes</span>${hours.map(([h, t]) => `<button class="${S.hours === h ? 'primary' : ''}" data-hours="${h}">${t}</button>`).join('')}<a class="button-link" style="margin-left:auto" href="/api/export/metrics.csv?hours=${S.hours}">Exporter en CSV</a></div>
    <div class="split-grid">${panel('Processeur', `<div class="chart-wrap">${chart([{ name: 'CPU', values: H.map((p) => [p.ts, p.cpu]) }], { max: 100, fmt: (v) => Math.round(v) + '%' })}</div>`, { note: `${l.cpu.toFixed(1)} % · utilisateur ${l.cpu_times.user} % · système ${l.cpu_times.system} %` })}
    ${panel('Charge moyenne', `<div class="chart-wrap">${chart([{ name: 'Charge', values: H.map((p) => [p.ts, p.load]) }], { fmt: (v) => v.toFixed(1) })}</div>`, { note: `1 / 5 / 15 min : ${l.load_avg.map((v) => v.toFixed(2)).join(' / ')}` })}
    ${panel('Mémoire', `<div class="chart-wrap">${chart([{ name: 'Mémoire', values: H.map((p) => [p.ts, p.ram]) }], { max: 100, fmt: (v) => Math.round(v) + '%' })}</div>`, { note: `${bytes(l.memory.used)} / ${bytes(l.memory.total)} · cache ${bytes(l.memory.cached || 0)} · swap ${bytes(swap.used)} / ${bytes(swap.total)}` })}
    ${panel('Réseau', `<div class="chart-wrap">${chart([{ name: 'Réception', values: H.map((p) => [p.ts, p.rx]) }, { name: 'Émission', values: H.map((p) => [p.ts, p.tx]) }], { fmt: (v) => bytes(v, 0) + '/s' })}</div>${legend('Réception ↓', 'Émission ↑')}`, { note: `↓ ${rate(l.rx)} · ↑ ${rate(l.tx)}` })}
    ${panel('Entrées / sorties disque', `<div class="chart-wrap">${chart([{ name: 'Lecture', values: H.map((p) => [p.ts, p.read]) }, { name: 'Écriture', values: H.map((p) => [p.ts, p.write]) }], { fmt: (v) => bytes(v, 0) + '/s' })}</div>${legend('Lecture', 'Écriture')}`, { note: `lecture ${rate(l.read)} · écriture ${rate(l.write)}` })}
    ${panel('Occupation des disques', (l.disks || []).map((d) => `<div class="disk-item"><p><strong>${esc(d.mount)}</strong><small>${esc(d.device)} · ${esc(d.fstype)} · ${bytes(d.used)} / ${bytes(d.total)} (${d.percent} %)</small></p>${meter(d.percent)}</div>`).join(''), { note: 'Partitions réelles' })}</div>
    ${panel('Cœurs du processeur', `<div class="core-grid">${l.cores.map((c, i) => `<div><span>Cœur ${i + 1}<strong>${c.toFixed(0)} %</strong></span>${meter(c)}</div>`).join('')}</div>`)}
    <div class="split-grid" style="margin-top:24px">${panel('Système', `<div class="spec-list"><div><span>Nom d’hôte</span><strong>${esc(l.hostname)}</strong></div><div><span>Système</span><strong>${esc(l.platform)}</strong></div><div><span>Démarré depuis</span><strong>${dur(l.uptime)}</strong></div><div><span>Connexions réseau</span><strong>${l.connections ?? '—'}</strong></div><div><span>Mémoire virtuelle (swap)</span><strong>${bytes(swap.used)} / ${bytes(swap.total)}</strong></div></div>${sensors.length ? `<div class="mini-list">${sensors.join('')}</div>` : ''}`)}
    ${panel('Interfaces réseau', `<div class="network-list">${(l.interfaces || []).map((n) => `<div class="mini-row"><strong>${esc(n.name)}</strong><span class="right">↓ ${bytes(n.bytes_recv)} · ↑ ${bytes(n.bytes_sent)} · erreurs ${n.errin + n.errout}</span></div>`).join('')}</div>`, { note: 'Compteurs cumulés depuis le démarrage' })}</div>
    ${panel('Processus les plus actifs', table(['PID', 'Nom', 'Utilisateur', 'CPU', 'Mémoire', 'État'], (l.processes || []).slice(0, 15).map((p) => `<tr><td>${p.pid}</td><td>${esc(p.name)}</td><td>${esc(p.username || '')}</td><td>${(p.cpu_percent || 0).toFixed(1)} %</td><td>${(p.memory_percent || 0).toFixed(1)} %</td><td>${esc(p.status)}</td></tr>`)), { note: 'Triés par CPU puis mémoire' })}`;
  } },

  software: { title: 'Logiciels', desc: 'Paquets installés, environnements d’exécution, images et volumes Docker.', async render() {
    const d = await api('/api/software');
    if (!d.checked) return '<div class="empty">Inventaire en cours… Revenez dans quelques instants.</div>';
    return MonitorInventory.renderSoftware(d, { query: S.q || '', manualOnly: !!S.manualOnly });
  } },

  projects: { title: 'Projets', desc: 'Vos applications : adresse, dépôt GitHub, état et dernier déploiement.', async render() {
    const [items, found] = await Promise.all([api('/api/projects'), api('/api/discover')]);
    S.projects = items;
    const safe = (u) => (/^https?:\/\//.test(u || '') ? u : '');
    const link = (href, label, ic) => (safe(href) ? `<a class="button-link" href="${esc(safe(href))}" target="_blank" rel="noopener noreferrer">${icon(ic)} ${label}</a>` : '');
    const card = (p) => {
      const m = p.monitors[0], c = p.container_info, d = p.deploy;
      const rows = [
        m ? `<div><span>Statut</span><strong>${monitorBadge(m)}</strong></div><div><span>Disponibilité 24 h</span><strong>${m.uptime != null ? m.uptime + ' %' : '—'}${m.latency != null ? ' · ' + Math.round(m.latency) + ' ms' : ''}</strong></div>` : '',
        m?.tls_days != null ? `<div><span>Certificat HTTPS</span><strong class="${m.tls_days < 14 ? 'bad' : ''}">${m.tls_days} jours</strong></div>` : '',
        c ? `<div><span>Conteneur ${esc(c.name)}</span><strong>${esc(c.state)} · CPU ${esc(c.cpu)} · ${esc(c.memory.split('/')[0])}</strong></div>` : '',
        d ? `<div><span>Dernier déploiement</span><strong>${esc(String(d.sha || '').slice(0, 7))} · ${ago(d.at)}</strong></div>` : '',
      ].join('');
      return panel(p.name, `${p.description ? `<p class="form-help" style="padding:0 22px">${esc(p.description)}</p>` : ''}<div class="spec-list">${rows || '<div><span>Aucun contrôle ni conteneur rattaché</span><strong>—</strong></div>'}</div><div class="detail-actions" style="padding:0 22px 20px">${link(p.url, 'Ouvrir l’application', 'external')}${link(p.repo_url, 'Dépôt GitHub', 'github')}<button data-edit-project="${p.id}">Modifier</button><button class="danger" data-delete-project="${p.id}">Supprimer</button></div>`, { note: p.url ? p.url.replace(/^https?:\/\//, '') : '' });
    };
    const discovered = found.length ? panel('Détectés sur le serveur', table(['Conteneur', 'Image', 'Ports', ''], found.map((f) => `<tr><td><strong>${esc(f.name)}</strong></td><td>${esc(f.image)}</td><td class="log-message">${esc((f.ports || []).join(', ') || '—')}</td><td><button data-adopt="${esc(f.name)}">Ajouter comme projet</button></td></tr>`)), { note: 'Conteneurs qui ne sont rattachés à aucun projet' }) : '';
    return `<div class="toolbar"><button class="primary" id="add-project">+ Ajouter un projet</button><span class="muted">${items.length} projet(s)</span></div><div class="split-grid">${items.map(card).join('') || '<div class="empty">Aucun projet. Ajoutez le premier, ou choisissez un conteneur détecté ci-dessous.</div>'}</div>${discovered}`;
  } },

  system: { title: 'Système', desc: 'Toutes les informations disponibles sur le serveur et sa maintenance.', live: true, render() {
    const y = S.live?.system; if (!y || !y.os) return '<div class="empty">Collecte des informations du serveur en cours…</div>';
    const spec = (pairs) => `<div class="spec-list">${pairs.filter((p) => p[1] != null && p[1] !== '').map(([k, v]) => `<div><span>${esc(k)}</span><strong>${v}</strong></div>`).join('')}</div>`;
    const m = y.memory || {}, c = y.cpu || {}, up = y.updates || {};
    const failed = y.failed_units;
    const connections = Object.entries(y.connections || {}).map(([k, v]) => `${esc(k.toLowerCase())} ${v}`).join(' · ');
    const states = Object.entries(y.process_states || {}).map(([k, v]) => `${esc(k)} ${v}`).join(' · ');
    return `<div class="split-grid">
    ${panel('Machine', spec([['Nom d’hôte', esc(y.hostname)], ['Système', esc(y.os)], ['Noyau', esc(y.kernel)], ['Architecture', esc(y.arch)], ['Virtualisation', esc(y.virtualization || 'aucune détectée')], ['Fournisseur', esc([y.vendor, y.product].filter(Boolean).join(' · '))], ['Démarré le', stamp(y.boot_time)], ['Fuseau horaire', esc(y.timezone)], ['Horloge synchronisée (NTP)', y.ntp_synchronized == null ? '—' : y.ntp_synchronized ? 'oui' : '<span class="bad">non</span>'], ['Docker', esc(y.docker?.version || 'non détecté')], ['Python (Monitor)', esc(y.python)]]))}
    ${panel('Processeur', spec([['Modèle', esc(c.model)], ['Cœurs', `${c.logical ?? '—'} logiques${c.physical ? ` · ${c.physical} physiques` : ''}`], ['Fréquence', c.mhz ? `${c.mhz} MHz${c.max_mhz ? ` (max ${c.max_mhz})` : ''}` : null], ['Cache', esc(c.cache)], ['Changements de contexte', c.ctx_switches?.toLocaleString('fr-FR')], ['Interruptions', c.interrupts?.toLocaleString('fr-FR')], ['Charge / cœur', S.live.load_avg ? (S.live.load_avg[0] / (c.logical || 1)).toFixed(2) : null]]))}
    ${panel('Mémoire', spec([['Total', bytes(m.MemTotal)], ['Disponible', bytes(m.MemAvailable)], ['Cache', bytes(m.Cached)], ['Tampons', bytes(m.Buffers)], ['Slab (noyau)', bytes(m.Slab)], ['Fichiers modifiés (dirty)', bytes(m.Dirty)], ['Swap utilisé', `${bytes((m.SwapTotal || 0) - (m.SwapFree || 0))} / ${bytes(m.SwapTotal)}`], ['Swappiness', esc(y.swappiness)], ['Mémoire engagée', m.Committed_AS ? `${bytes(m.Committed_AS)} / ${bytes(m.CommitLimit)}` : null]]))}
    ${panel('Processus et ressources du noyau', spec([['Processus', y.processes], ['Threads', y.threads], ['États', states], ['Descripteurs de fichiers', y.file_handles ? `${y.file_handles.allocated.toLocaleString('fr-FR')} / ${y.file_handles.max.toLocaleString('fr-FR')}` : null], ['Entropie disponible', esc(y.entropy)]]))}
    ${panel('Stockage', `${(y.block_devices || []).map((d) => `<div class="disk-item"><p><strong>/dev/${esc(d.name)}</strong><small>${bytes(d.size)} · ${d.rotational ? 'disque dur' : 'SSD / virtuel'}${d.model ? ' · ' + esc(d.model) : ''}${d.scheduler ? ' · ordonnanceur ' + esc(d.scheduler) : ''}</small></p></div>`).join('')}${(y.inodes || []).map((i) => `<div class="disk-item"><p><strong>Inodes ${esc(i.mount)}</strong><small>${i.used.toLocaleString('fr-FR')} / ${i.total.toLocaleString('fr-FR')} (${i.percent} %)</small></p>${meter(i.percent)}</div>`).join('')}`, { note: 'Disques physiques et inodes (nombre de fichiers possibles)' })}
    ${panel('Systèmes de fichiers montés', table(['Point de montage', 'Type', 'Périphérique', 'Utilisé', 'Total', '%'], (y.filesystems || []).map((f) => `<tr><td><strong>${esc(f.mount)}</strong></td><td>${esc(f.fstype)}</td><td>${esc(f.device)}</td><td>${bytes(f.used)}</td><td>${bytes(f.total)}</td><td>${f.percent} %</td></tr>`), 'Aucun système de fichiers.'), { note: 'Disques, partitions et espaces temporaires' })}
    ${panel('Activité des disques', table(['Disque', 'Lectures', 'Écritures', 'Lu', 'Écrit', 'E/S par s'], (y.disk_io || []).map((d) => `<tr><td><strong>${esc(d.name)}</strong></td><td>${d.reads.toLocaleString('fr-FR')}</td><td>${d.writes.toLocaleString('fr-FR')}</td><td>${bytes(d.read_bytes)}</td><td>${bytes(d.write_bytes)}</td><td>${d.iops ?? '—'}</td></tr>`), 'Aucune activité disque mesurée.'), { note: 'Compteurs cumulés depuis le démarrage' })}
    ${panel('Réseau', spec([['Passerelle', esc(y.gateway)], ['Serveurs DNS', esc((y.dns || []).join(', '))], ['Connexions', connections]]) + `<div class="network-list">${(y.interfaces || []).map((n) => `<div class="mini-row"><div><strong>${esc(n.name)}</strong><small>${esc([...n.ipv4, ...n.ipv6].join(' · ') || '—')}</small></div><span class="right">${n.up ? 'actif' : 'inactif'}${n.speed ? ' · ' + n.speed + ' Mb/s' : ''} · MTU ${n.mtu ?? '—'}<br>${esc(n.mac || '')}</span></div>`).join('')}</div>`)}
    ${panel('Ports ouverts', table(['Protocole', 'Adresse', 'Port', 'Service probable', 'Exposition', 'Conteneur'], (y.listening || []).map((p) => `<tr><td>${esc(p.proto)}</td><td>${esc(p.address)}</td><td><strong>${p.port}</strong></td><td>${esc(p.service || '—')}</td><td>${p.exposure === 'public' ? 'adresse non locale' : 'boucle locale'}</td><td>${esc(p.container || '—')}</td></tr>`), 'Aucun port en écoute visible.'), { note: 'Adresse d’écoute observée ; accessibilité Internet non vérifiée, dépend du routage et du pare-feu' })}
    ${panel('Maintenance et sécurité', spec([['Redémarrage requis', y.reboot_required ? '<span class="bad">oui</span>' : 'non'], ['Mises à jour disponibles', up.count == null ? 'inconnu' : `${up.count}${up.security ? ` · <span class="bad">${up.security} de sécurité</span>` : ''}`], ['Dernière vérification', up.checked ? ago(up.checked) : null], ['Services en échec', failed == null ? 'inconnu' : failed.length ? `<span class="bad">${esc(failed.map((f) => f.name).join(', '))}</span>` : 'aucun'], ['Sessions ouvertes', y.session_count == null ? 'inconnu' : esc(y.session_count)]]))}</div>`;
  } },

  containers: { title: 'Conteneurs', desc: 'Tous les conteneurs Docker et ce qu’ils consomment.', live: true, render() {
    const l = S.live; if (!l?.ts) return '<div class="empty">Chargement…</div>';
    if (l.errors?.containers) return `<div class="notice error">${esc(l.errors.containers)}</div>`;
    const since = (c) => (c.state === 'running' && c.started ? dur(Date.now() / 1000 - Date.parse(c.started) / 1000) : '—');
    return panel('Conteneurs Docker', table(['Nom', 'Image', 'État', 'Santé', 'En ligne depuis', 'Redém.', 'Ports', 'CPU', 'Mémoire', 'Réseau', 'Disque', 'Logs'], (l.containers || []).map((c) => `<tr><td><strong>${esc(c.name)}</strong>${c.compose ? `<small>${esc(c.compose)}</small>` : ''}</td><td>${esc(c.image)}</td><td>${badge(c.state === 'running' ? '' : 'down', c.state)}</td><td>${c.health ? badge(c.health === 'healthy' ? '' : 'down', c.health) : '—'}</td><td>${since(c)}</td><td>${c.restarts ?? '—'}${c.oom ? ' · OOM' : ''}</td><td class="log-message">${esc((c.ports || []).join(', ') || '—')}</td><td>${esc(c.cpu)}</td><td>${esc(c.memory)}</td><td>${esc(c.network)}</td><td>${esc(c.io)}</td><td><button data-logs="${esc(c.name)}">Voir</button></td></tr>`), 'Aucun conteneur détecté.'), { note: 'Mesures selon la fréquence du collecteur ; fraîcheur détaillée dans Inventaire' });
  } },

  services: { title: 'Services système', desc: 'Services systemd en cours d’exécution sur le serveur.', live: true, render() {
    const l = S.live; if (!l?.ts) return '<div class="empty">Chargement…</div>';
    if (l.errors?.services) return `<div class="notice error">${esc(l.errors.services)}</div>`;
    const q = (S.q || '').toLowerCase(), list = (l.services || []).filter((s) => s.name.toLowerCase().includes(q));
    return `<div class="toolbar"><input id="filter" type="search" placeholder="Filtrer les services…" value="${esc(S.q || '')}" aria-label="Filtrer les services"><span class="muted">${list.length} service(s)</span></div>` +
      panel('Services', table(['Service', 'État', 'Détail', 'Mémoire', 'CPU cumulé'], list.map((s) => `<tr><td><strong>${esc(s.name)}</strong></td><td>${badge(s.active === 'active' ? '' : 'paused', s.active === 'active' ? 'actif' : 'inactif')}</td><td>${esc(s.description)}</td><td>${s.memory != null ? bytes(s.memory) : '—'}</td><td>${s.cpu_seconds != null ? dur(s.cpu_seconds) : '—'}</td></tr>`), 'Aucun service.'));
  } },

  monitors: { title: 'Disponibilité', desc: 'Sites, applications, ports, conteneurs et certificats surveillés en continu.', render() {
    const q = (S.q || '').toLowerCase(), list = S.monitors.filter((m) => (m.name + m.target + m.group_name).toLowerCase().includes(q));
    const groups = [...new Set(list.map((m) => m.group_name))];
    const rows = groups.flatMap((g) => [`<tr><td colspan="8" class="group-label">${esc(g)}</td></tr>`, ...list.filter((m) => m.group_name === g).map((m) => `<tr><td><button class="name-button" data-detail="${m.id}">${esc(m.name)}</button><small>${esc(m.target)}</small></td><td>${esc(KIND[m.kind] || m.kind)}</td><td>${monitorBadge(m)}</td><td>${m.uptime != null ? m.uptime + ' %' : '—'}</td><td>${m.latency != null ? Math.round(m.latency) + ' ms' : '—'}</td><td>${m.tls_days != null ? `<span class="${m.tls_days < 14 ? 'bad' : ''}">${m.tls_days} j</span>` : '—'}</td><td>${ago(m.checked_at)}</td><td class="actions-cell"><button data-check="${m.id}">Vérifier</button><button data-pause="${m.id}">${m.paused ? 'Reprendre' : 'Pause'}</button></td></tr>`)]);
    return `<div class="toolbar"><input id="filter" type="search" placeholder="Rechercher un contrôle…" value="${esc(S.q || '')}" aria-label="Rechercher"><button class="primary" id="add-monitor">+ Ajouter un contrôle</button><span class="muted">${list.length} contrôle(s)</span></div>` +
      panel('Contrôles', table(['Nom', 'Type', 'Statut', 'Dispo. 24 h', 'Latence', 'Certificat', 'Dernier contrôle', ''], rows, 'Aucun contrôle. Ajoutez votre premier site ou service.'));
  } },

  incidents: { title: 'Incidents', desc: 'Pannes de services et dépassements de seuils.', render() {
    $('#incident-count').textContent = S.incidents.filter((i) => !i.closed).length || '';
    return panel('Historique', table(['Source', 'Incident', 'Détail', 'Ouvert', 'Durée', ''], S.incidents.map((i) => `<tr><td>${esc(i.source)}</td><td><strong>${esc(i.title)}</strong></td><td class="log-message">${esc(i.message)}</td><td>${stamp(i.opened)}</td><td>${i.closed ? dur(i.closed - i.opened) : badge('down', 'En cours')}</td><td>${!i.closed && !i.acknowledged ? `<button data-ack="${i.id}">Prendre en compte</button>` : i.acknowledged ? '<span class="muted">Pris en compte</span>' : ''}</td></tr>`), 'Aucun incident enregistré.'));
  } },

  events: { title: 'Journal', desc: 'Connexions, modifications et notifications.', async render() {
    const events = await api('/api/events');
    return panel('200 derniers événements', table(['Date', 'Type', 'Message'], events.map((x) => `<tr><td>${stamp(x.ts)}</td><td>${esc(x.kind)}</td><td class="log-message">${esc(x.message)}</td></tr>`), 'Aucun événement.'));
  } },

  database: { title: 'Base de données', desc: 'Connectivité et volume des données du moteur.', async render() {
    const d=await api('/api/database');
    const meta=panel('Connexion',`<div class="spec-list"><div><span>Moteur</span><strong>${esc(d.engine)}</strong></div><div><span>État</span><strong>${d.ok?'Disponible':'Indisponible'}</strong></div><div><span>Latence</span><strong>${d.latency_ms} ms</strong></div><div><span>Fichier</span><strong>${d.size_bytes==null?'—':bytes(d.size_bytes)}</strong></div></div>`);
    const values=panel('Enregistrements',table(['Table','Nombre'],Object.entries(d.rows).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${v.toLocaleString('fr-FR')}</td></tr>`)));
    const stats=panel('Indicateurs',table(['Indicateur','Valeur'],Object.entries(d.status||{}).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`)));
    return meta+values+stats;
  } },
  sla: { title: 'Rapports de disponibilité', desc: 'Temps réellement observé sur chaque période.', async render() {
    const d=await api('/api/sla?days='+(S.slaDays||30));
    return `<div class="toolbar">${[1,7,30,90].map(n=>`<button data-sla-days="${n}" class="${d.days===n?'primary':''}">${n} j</button>`).join('')}</div>`+
      panel('Temps de disponibilité',table(['Contrôle','Disponible','Couverture','Panne cumulée'],d.monitors.map(m=>`<tr><td>${esc(m.name)}</td><td><strong>${m.availability==null?'—':m.availability+' %'}</strong></td><td>${m.coverage} %</td><td>${dur(m.down_seconds)}</td></tr>`)));
  } },
  settings: { title: 'Paramètres', desc: 'Seuils d’alerte, notifications, sauvegarde et sécurité.', async render() {
    const s = S.settings = await api('/api/settings');
    const f = (name, label, type = 'text', extra = '') => `<label>${label}<input name="${name}" type="${type}" value="${esc(s[name])}" ${extra}></label>`;
    return `<div class="settings-columns"><div><form id="settings-form"><section class="panel settings-section"><h2>Alertes de ressources</h2><p>Une alerte s’ouvre quand le seuil est dépassé pendant la durée indiquée.</p><div class="form-grid">${f('cpu_limit', 'Seuil processeur (%)', 'number', 'min=1 max=100')}${f('ram_limit', 'Seuil mémoire (%)', 'number', 'min=1 max=100')}${f('disk_limit', 'Seuil disque (%)', 'number', 'min=1 max=100')}${f('sustain_seconds', 'Durée avant alerte (s)', 'number', 'min=10')}${f('retention_days', 'Conservation de l’historique (jours)', 'number', 'min=1 max=365')}</div>
      <h3>Notifications</h3><p class="form-help">Renseignez au moins un canal pour être prévenu d’une panne et de son rétablissement.</p><div class="form-grid">${f('webhook_url', 'Webhook HTTPS', 'url')}${f('telegram_token', 'Jeton du bot Telegram', 'password', 'autocomplete=off')}${f('telegram_chat', 'Identifiant du chat Telegram')}${f('smtp_host', 'Serveur SMTP')}${f('smtp_port', 'Port SMTP', 'number')}${f('smtp_user', 'Utilisateur SMTP')}${f('smtp_password', 'Mot de passe SMTP', 'password', 'autocomplete=off')}${f('smtp_from', 'Expéditeur')}${f('smtp_to', 'Destinataire')}</div>
      <label class="check-label"><input type="checkbox" name="public_enabled" ${s.public_enabled ? 'checked' : ''} style="width:auto;min-height:0;margin-right:8px"> Activer la page de statut publique (<a href="/status" target="_blank" rel="noopener">/status</a>)</label>
      <div class="form-actions"><button class="primary" type="submit">Enregistrer</button><button type="button" id="test-notify">Envoyer une notification de test</button></div></section></form>
      <div class="panel settings-section"><h2>Livraisons des alertes</h2><p>Suivez les derniers envois, leurs tentatives et les éventuels échecs.</p><div id="notification-deliveries"><button type="button" id="show-deliveries">Consulter les livraisons</button></div></div>
      <form id="password-form"><section class="panel settings-section"><h2>Mot de passe administrateur</h2><p>Changer le mot de passe déconnecte toutes les sessions (12 caractères minimum).</p><div class="form-grid"><label>Mot de passe actuel<input name="current" type="password" autocomplete="current-password" required></label><label>Nouveau mot de passe<input name="new" type="password" autocomplete="new-password" minlength="12" required></label></div><div class="form-actions"><button class="primary" type="submit">Changer le mot de passe</button></div></section></form></div>
      <aside class="settings-aside panel settings-section"><h2>Données</h2><p>Votre historique est conservé sur ce serveur, dans une base SQLite.</p><a class="button-link" href="/api/export">Exporter les contrôles (JSON)</a><br><a class="button-link" href="/api/backup">Sauvegarde complète de la base</a><br><label class="button-link" style="cursor:pointer">Importer des contrôles<input id="import-file" type="file" accept="application/json" hidden></label></aside></div>`;
  } },
};

// ---------- Rendu ----------
async function render() {
  const name = S.page, page = PAGES[name], content = $('#content');
  const version = S.renderVersion = (S.renderVersion || 0) + 1;
  const current = () => S.page === name && S.renderVersion === version;
  try {
    const html = await page.render();
    if (!current()) return;
    content.innerHTML = html;
  } catch (e) {
    if (!current()) return;
    if (e.message !== 'Connexion requise') content.innerHTML = `<div class="notice error">${esc(e.message)}</div>`;
  }
  decorate(content);
}
const liveRender = () => { if (PAGES[S.page].live && !document.activeElement?.closest?.('#content input,#content textarea')) render(); };

async function go() {
  const name = (location.hash || '#overview').slice(1).split('/')[0];
  S.page = PAGES[name] ? name : 'overview'; S.q = '';
  const p = PAGES[S.page];
  $('#page-title').textContent = p.title; $('#page-description').textContent = p.desc; $('#breadcrumb').textContent = p.title;
  $$('nav a, .sidebar-bottom a').forEach((a) => a.classList.toggle('active', a.dataset.page === S.page));
  $('.sidebar').classList.remove('open');
  if (S.page === 'overview' || S.page === 'server') await loadHistory();
  render(); $('#main').focus({ preventScroll: true });
}

async function loadHistory() {
  try { S.history = await api('/api/history?hours=' + (S.page === 'overview' ? 1 : S.hours)); } catch { /* conserve l'ancien */ }
}
async function loadMonitors() {
  try { S.monitors = await api('/api/monitors'); S.incidents = await api('/api/incidents'); $('#incident-count').textContent = S.incidents.filter((i) => !i.closed).length || ''; } catch { /* réessai au prochain tour */ }
}

// ---------- Détail et formulaire d'un contrôle ----------
function openDialog(title, html) { S.inventoryDialog = null; $('#dialog-title').textContent = title; $('#dialog-body').innerHTML = html; decorate($('#dialog')); if (!$('#dialog').open) $('#dialog').showModal(); }
async function showInventoryHistory(id) {
  const collector = (S.inventory?.collectors || []).find((c) => c.id === id);
  openDialog(collector?.title || id, '<div class="empty" role="status">Chargement de l’historique…</div>');
  S.inventoryDialog = id;
  try {
    const query = encodeURIComponent(id);
    const [history, changes] = await Promise.all([api(`/api/inventory/history?collector=${query}&limit=100`), api(`/api/inventory/changes?collector=${query}&limit=30`)]);
    if (!$('#dialog').open || S.inventoryDialog !== id) return;
    $('#dialog-body').innerHTML = MonitorInventory.renderHistory(history, changes, id);
    if (id === 'pressure' || id === 'psi') {
      try {
        const points = await api(`/api/inventory/metrics?collector=${query}&hours=24`);
        if (!$('#dialog').open || S.inventoryDialog !== id) return;
        const series = MonitorInventory.metricSeries(points);
        const graph = series.some((s) => s.values.some((p) => p[1] != null)) ? chart(series, { max: 100, height: 180, fmt: (v) => Math.round(v) + '%' }) : '<div class="empty">Aucune mesure de pression disponible sur cette période.</div>';
        $('#dialog-body').insertAdjacentHTML('afterbegin', `<h3 class="inventory-section-title">Pression des ressources · 24 heures</h3><div class="chart-wrap">${graph}</div>${legend(...series.map((s) => s.name))}`);
      } catch (error) {
        if ($('#dialog').open && S.inventoryDialog === id) $('#dialog-body').insertAdjacentHTML('afterbegin', `<p class="notice error">Historique des mesures indisponible : ${esc(error.message)}</p>`);
      }
    }
  } catch (error) {
    if ($('#dialog').open && S.inventoryDialog === id) $('#dialog-body').innerHTML = `<p class="notice error">${esc(error.message)}</p>`;
  }
}
async function showDetail(id) {
  S.inventoryDialog = null;
  const m = S.monitors.find((x) => x.id === id); if (!m) return;
  const h = await api(`/api/monitors/${id}/history?hours=24`);
  openDialog(m.name, `<div class="detail-summary">${monitorBadge(m)}<div><strong>${esc(KIND[m.kind])}</strong><p>${esc(m.target || 'Signal de vie')}</p></div></div>
    <div class="metric-grid" style="grid-template-columns:repeat(3,1fr)"><div class="metric"><div class="metric-label">Disponibilité 24 h</div><div class="metric-value">${m.uptime ?? '—'}<small> %</small></div></div><div class="metric"><div class="metric-label">Latence</div><div class="metric-value">${m.latency != null ? Math.round(m.latency) : '—'}<small> ms</small></div></div><div class="metric"><div class="metric-label">Intervalle</div><div class="metric-value">${m.interval}<small> s</small></div></div></div>
    ${chart([{ name: 'Latence', values: h.points.map((p) => [p.ts, p.latency]) }], { fmt: (v) => Math.round(v) + ' ms', height: 160 })}
    ${m.kind === 'push' ? `<p class="form-help">Appelez cette adresse depuis votre tâche :</p><div class="code-value">${esc(location.origin)}/api/push/${esc(m.push_token)}</div>` : ''}
    <div class="detail-actions"><button data-act="check">Vérifier maintenant</button><button data-act="pause">${m.paused ? 'Reprendre' : 'Mettre en pause'}</button><button data-act="maintenance">Maintenance 1 h</button><button data-act="edit">Modifier</button><button class="danger" data-act="delete">Supprimer</button></div>
    ${table(['Date', 'Résultat', 'Latence', 'Message'], h.recent.slice(0, 12).map((c) => `<tr><td>${stamp(c.ts)}</td><td>${badge(c.ok ? '' : 'down', c.ok ? 'OK' : 'Échec')}</td><td>${Math.round(c.latency)} ms</td><td class="log-message">${esc(c.message)}</td></tr>`))}`);
  S.detail = m;
}
function monitorForm(m = {}) {
  const v = { kind: 'http', interval: 60, timeout: 10, retries: 2, expected: '200-299', group_name: 'Applications', ...m };
  openDialog(m.id ? 'Modifier le contrôle' : 'Nouveau contrôle', `<form id="monitor-form" class="form-grid">
    <label class="full">Nom<input name="name" value="${esc(v.name)}" required maxlength="100"></label>
    <label>Type<select name="kind">${Object.entries(KIND).map(([k, t]) => `<option value="${k}" ${v.kind === k ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
    <label>Groupe<input name="group_name" value="${esc(v.group_name)}" required></label>
    <label class="full">Cible<input name="target" value="${esc(v.target)}" placeholder="https://exemple.fr · hôte:port · nom du conteneur"></label>
    <label>Intervalle (s)<input name="interval" type="number" min="10" value="${v.interval}"></label><label>Délai max (s)<input name="timeout" type="number" min="1" max="30" value="${v.timeout}"></label>
    <label>Échecs avant alerte<input name="retries" type="number" min="0" max="10" value="${v.retries}"></label><label>Codes HTTP acceptés<input name="expected" value="${esc(v.expected)}"></label>
    <label class="full">Mot-clé, adresse attendue, ou chemin=valeur (JSON)<input name="keyword" value="${esc(v.keyword)}"></label>
    <label class="check-label full"><input type="checkbox" name="public" ${v.public ? 'checked' : ''} style="width:auto;min-height:0;margin-right:8px"> Afficher sur la page de statut publique</label>
    <p class="error full" id="form-error" role="alert"></p><div class="form-actions full"><button class="primary" type="submit">Enregistrer</button><button type="button" id="cancel">Annuler</button></div></form>`);
  $('#cancel').onclick = () => $('#dialog').close();
  $('#monitor-form').onsubmit = async (ev) => {
    ev.preventDefault(); const d = new FormData(ev.target);
    const body = { name: d.get('name'), kind: d.get('kind'), target: d.get('target'), group_name: d.get('group_name'), interval: +d.get('interval'), timeout: +d.get('timeout'), retries: +d.get('retries'), expected: d.get('expected'), keyword: d.get('keyword'), public: d.has('public') };
    try { await api(m.id ? `/api/monitors/${m.id}` : '/api/monitors', { method: m.id ? 'PUT' : 'POST', body }); $('#dialog').close(); await loadMonitors(); render(); toast('Contrôle enregistré'); }
    catch (e) { $('#form-error').textContent = e.message; }
  };
}

function projectForm(p = {}) {
  openDialog(p.id ? 'Modifier le projet' : 'Nouveau projet', `<form id="project-form" class="form-grid">
    <label class="full">Nom<input name="name" value="${esc(p.name)}" required maxlength="100"></label>
    <label class="full">Adresse de l’application<input name="url" type="url" value="${esc(p.url)}" placeholder="https://app.exemple.fr"></label>
    <label class="full">Dépôt GitHub (ou autre)<input name="repo_url" type="url" value="${esc(p.repo_url)}" placeholder="https://github.com/utilisateur/projet"></label>
    <label>Conteneur Docker<input name="container" value="${esc(p.container)}" placeholder="nom-du-conteneur" pattern="[A-Za-z0-9_.\-]*"></label>
    <label>Dossier de déploiement (facultatif)<input name="repo_dir" value="${esc(p.repo_dir)}" pattern="[A-Za-z0-9_.\-]*"></label>
    <label class="full">Description<input name="description" value="${esc(p.description)}" maxlength="500"></label>
    <fieldset class="full"><legend>Contrôles rattachés au projet</legend>${S.monitors.map(m=>`<label class="check-label"><input type="checkbox" name="monitor_ids" value="${m.id}" ${p.monitor_ids?.includes(m.id)?'checked':''} style="width:auto;min-height:0"> ${esc(m.name)} (${esc(m.kind)})</label>`).join('')||'Aucun contrôle disponible.'}</fieldset>
    <p class="error full" id="form-error" role="alert"></p><div class="form-actions full"><button class="primary" type="submit">Enregistrer</button><button type="button" id="cancel">Annuler</button></div></form>`);
  $('#cancel').onclick = () => $('#dialog').close();
  $('#project-form').onsubmit = async (ev) => {
    ev.preventDefault(); const formData=new FormData(ev.target), d=Object.fromEntries(formData); d.monitor_ids=formData.getAll('monitor_ids').map(Number);
    try { await api(p.id ? `/api/projects/${p.id}` : '/api/projects', { method: p.id ? 'PUT' : 'POST', body: d }); $('#dialog').close(); render(); toast('Projet enregistré'); }
    catch (e) { $('#form-error').textContent = e.message; }
  };
}

// ---------- Actions ----------
document.addEventListener('click', async (ev) => {
  const t = ev.target.closest('button,a'); if (!t) return;
  try {
    if (t.id === 'inventory-refresh') { S.inventoryLoadedAt = 0; await render(); }
    else if (t.dataset.inventoryHistory) await showInventoryHistory(t.dataset.inventoryHistory);
    else if (t.dataset.manual) { S.manualOnly = !S.manualOnly; render(); }
    else if (t.dataset.hours) { S.hours = +t.dataset.hours; await loadHistory(); render(); }
    else if (t.dataset.slaDays) { S.slaDays = +t.dataset.slaDays; render(); }
    else if (t.dataset.logs) { const data=await api('/api/containers/'+encodeURIComponent(t.dataset.logs)+'/logs?tail=150'); openDialog('Logs Docker : '+data.name, '<pre class="log-view">'+esc(data.logs)+'</pre>'); }
    else if (t.dataset.detail) await showDetail(+t.dataset.detail);
    else if (t.dataset.check || t.dataset.pause) { const id = +(t.dataset.check || t.dataset.pause); await api(`/api/monitors/${id}/action`, { method: 'POST', body: { action: t.dataset.check ? 'check' : 'pause' } }); toast(t.dataset.check ? 'Vérification lancée' : 'Contrôle mis à jour'); setTimeout(async () => { await loadMonitors(); render(); }, t.dataset.check ? 2500 : 200); }
    else if (t.dataset.ack) { await api(`/api/incidents/${t.dataset.ack}/ack`, { method: 'POST' }); await loadMonitors(); render(); }
    else if (t.dataset.act && S.detail) {
      const m = S.detail, act = t.dataset.act;
      if (act === 'edit') monitorForm(m);
      else if (act === 'delete') { if (t.dataset.confirm) { await api(`/api/monitors/${m.id}`, { method: 'DELETE' }); $('#dialog').close(); await loadMonitors(); render(); toast('Contrôle supprimé'); } else { t.dataset.confirm = '1'; t.textContent = 'Confirmer la suppression'; } }
      else { await api(`/api/monitors/${m.id}/action`, { method: 'POST', body: { action: act, minutes: 60 } }); $('#dialog').close(); toast('Contrôle mis à jour'); setTimeout(async () => { await loadMonitors(); render(); }, 2500); }
    }
    else if (t.id === 'add-monitor') monitorForm();
    else if (t.id === 'add-project') projectForm();
    else if (t.dataset.editProject) projectForm(S.projects.find((p) => p.id === +t.dataset.editProject));
    else if (t.dataset.adopt) projectForm({ name: t.dataset.adopt.replace(/-api$/, ''), container: t.dataset.adopt });
    else if (t.dataset.deleteProject) { if (t.dataset.confirm) { await api(`/api/projects/${t.dataset.deleteProject}`, { method: 'DELETE' }); render(); toast('Projet supprimé'); } else { t.dataset.confirm = '1'; t.textContent = 'Confirmer'; } }
    else if (t.id === 'test-notify') toast((await api('/api/notifications/test', { method: 'POST' })).message);
    else if (t.id === 'show-deliveries') { const items=await api('/api/notifications/deliveries'); const host=$('#notification-deliveries'); if(host)host.innerHTML=table(['Date','Canal','Statut','Essais'],items.map(x=>`<tr><td>${stamp(x.created)}</td><td>${esc(x.channel)}</td><td>${esc(x.status)}</td><td>${x.attempts}</td></tr>`)); }
  } catch (e) { toast(e.message, true); }
});
document.addEventListener('input', (ev) => {
  if (ev.target.id === 'inventory-search') {
    S.inventoryFilters = { ...S.inventoryFilters, query: ev.target.value };
    const pos = ev.target.selectionStart;
    render().then(() => { const input = $('#inventory-search'); if (input) { input.focus(); input.setSelectionRange(pos, pos); } });
  } else if (ev.target.id === 'filter') { S.q = ev.target.value; const pos = ev.target.selectionStart; render().then?.(() => {}); setTimeout(() => { const f = $('#filter'); if (f) { f.focus(); f.setSelectionRange(pos, pos); } }, 0); }
});
document.addEventListener('submit', async (ev) => {
  const form = ev.target;
  try {
    if (form.id === 'inventory-compare') {
      ev.preventDefault();
      const fields = new FormData(form), before = Number(fields.get('before')), after = Number(fields.get('after'));
      if (!Number.isSafeInteger(before) || !Number.isSafeInteger(after) || before < 1 || after < 1 || before === after) { toast('Choisissez deux inventaires différents.', true); return; }
      const id = S.inventoryDialog;
      const diff = await api(`/api/inventory/compare?before=${before}&after=${after}`);
      if (S.inventoryDialog === id && $('#inventory-comparison')) $('#inventory-comparison').innerHTML = MonitorInventory.renderComparison(diff);
    }
    else if (form.id === 'settings-form') { ev.preventDefault(); const d = new FormData(form), body = {}; for (const [k, v] of Object.entries(S.settings)) body[k] = typeof v === 'number' ? +d.get(k) : typeof v === 'boolean' ? d.has(k) : (d.get(k) ?? v); body.maintenance_until = S.settings.maintenance_until || 0; await api('/api/settings', { method: 'PUT', body }); toast('Paramètres enregistrés'); }
    else if (form.id === 'password-form') { ev.preventDefault(); const d = new FormData(form); await api('/api/password', { method: 'POST', body: { current: d.get('current'), new: d.get('new') } }); toast('Mot de passe modifié, reconnexion…'); setTimeout(showLogin, 1200); }
  } catch (e) { toast(e.message, true); }
});
document.addEventListener('change', async (ev) => {
  if (ev.target.id === 'inventory-category' || ev.target.id === 'inventory-status') {
    const key = ev.target.id === 'inventory-category' ? 'category' : 'status';
    S.inventoryFilters = { ...S.inventoryFilters, [key]: ev.target.value }; await render(); $(`#${ev.target.id}`)?.focus(); return;
  }
  if (ev.target.id !== 'import-file') return;
  try { const text = await ev.target.files[0].text(); const r = await api('/api/import', { method: 'POST', body: JSON.parse(text) }); toast(`${r.imported} contrôle(s) importé(s)`); await loadMonitors(); } catch (e) { toast(e.message, true); }
});

// ---------- Connexion, flux temps réel, démarrage ----------
let source = null;
function showLogin() { source?.close(); $('#shell').hidden = true; $('#login').hidden = false; }
function connect() {
  source?.close(); source = new EventSource('/api/stream');
  source.onmessage = (ev) => { S.live = JSON.parse(ev.data); S.last = Date.now(); $('#stale-banner').hidden = !S.live.stale; $('#connection').lastChild.textContent = S.live.stale ? ' Mesures périmées' : ' En direct'; $('#host-label').textContent = S.live.hostname || 'Serveur'; liveRender(); };
  source.onerror = () => { $('#connection').lastChild.textContent = ' Reconnexion…'; };
}
function branding(cfg) {
  const name = cfg.name || 'Serveur';
  $('#ws-name').textContent = name; $('#ws-crumb').textContent = name; $('#ws-icon').textContent = name[0].toUpperCase();
  $('#ws-sub').textContent = cfg.database ? 'Base : ' + cfg.database : 'Infrastructure privée'; document.title = 'Monitor · ' + name;
}
async function start() {
  try { S.cfg = await fetch('/api/config').then((r) => r.json()); branding(S.cfg); } catch { /* marque par défaut */ }
  try { S.live = await api('/api/live'); } catch { return showLogin(); }
  $('#login').hidden = true; $('#shell').hidden = false; decorate();
  await loadMonitors(); connect(); await go();
  setInterval(async () => { if (!document.hidden) { await loadMonitors(); if (['monitors', 'incidents'].includes(S.page) && !$('#dialog').open) render(); } }, 15000);
  setInterval(async () => { if (!document.hidden && (S.page === 'overview' || S.page === 'server')) { await loadHistory(); } else if (!document.hidden && S.page === 'inventory' && !$('#dialog').open && !document.activeElement?.closest?.('#content input,#content select,#content details')) { await render(); } }, 30000);
  setInterval(() => { $('#clock').textContent = new Date().toLocaleTimeString('fr-FR'); const stale = (S.last && Date.now() - S.last > 10000) || S.live?.stale; $('#stale-banner').hidden = !stale; $('#sidebar-dot').classList.toggle('bad', !!stale); }, 1000);
}
$('#login-form').addEventListener('submit', async (ev) => {
  ev.preventDefault(); const d = new FormData(ev.target); $('#login-error').textContent = '';
  try { await api('/api/login', { method: 'POST', body: { username: d.get('username'), password: d.get('password') } }); ev.target.reset(); await start(); }
  catch (e) { $('#login-error').textContent = e.message; }
});
$('#logout').addEventListener('click', async () => { await api('/api/logout', { method: 'POST' }).catch(() => {}); showLogin(); });
$('#menu').addEventListener('click', () => $('.sidebar').classList.toggle('open'));
$('#close-dialog').addEventListener('click', () => $('#dialog').close());
$('#theme').addEventListener('click', () => { const dark = document.documentElement.dataset.theme !== 'dark'; document.documentElement.dataset.theme = dark ? 'dark' : 'light'; try { localStorage.setItem('theme', dark ? 'dark' : 'light'); } catch { /* facultatif */ } });
try { const t = localStorage.getItem('theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); document.documentElement.dataset.theme = t; } catch { /* thème clair par défaut */ }
window.addEventListener('hashchange', go);
decorate(); start();

if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(()=>{});
