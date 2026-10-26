'use strict';
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
async function load() {
  const list = document.getElementById('list'), summary = document.getElementById('summary');
  try {
    const response = await fetch('/api/public', { cache: 'no-store' });
    if (!response.ok) throw new Error();
    const items = await response.json(), now = Date.now() / 1000;
    const state = (m) => (m.paused ? ['paused', 'En pause'] : m.maintenance ? ['maintenance', 'Maintenance'] : m.ok == null ? ['pending', 'En attente'] : m.ok ? ['', 'Opérationnel'] : ['down', 'Panne']);
    const down = items.filter((m) => !m.paused && !m.maintenance && m.ok === 0).length;
    summary.hidden = false; summary.className = 'notice' + (down ? ' error' : '');
    summary.textContent = down ? `${down} service(s) rencontrent un problème.` : 'Tous les services sont opérationnels.';
    list.innerHTML = '<section class="panel"><div class="mini-list">' + items.map((m) => { const [cls, label] = state(m); return `<div class="mini-row"><div><strong>${esc(m.name)}</strong><small>${m.uptime != null ? esc(m.uptime) + ' % de disponibilité sur 24 h' : ''}${m.checked_at ? ' · vérifié il y a ' + Math.max(0, Math.round(now - m.checked_at)) + ' s' : ''}</small></div><span class="right"><span class="badge ${cls}"><span class="status-dot"></span>${label}</span></span></div>`; }).join('') + '</div></section>';
  } catch { list.innerHTML = '<div class="empty">La page de statut n’est pas disponible.</div>'; summary.hidden = true; }
}
load(); setInterval(load, 30000);
