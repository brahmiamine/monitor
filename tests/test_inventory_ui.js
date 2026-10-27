'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const UI = require('../static/inventory.js');

const collector = (extra = {}) => ({ id: 'psi', title: 'Pression des ressources', category: 'resources', status: 'available', last_success: 100, last_attempt: 100, interval: 30, timeout: 2, duration_ms: 0, stale: false, data: {}, ...extra });

test('persistence failure and pending writes remain visible independently of collection', () => {
  assert.match(UI.renderCollector(collector({ persistence_error: true })), /historique.*enregistr/i);
  assert.match(UI.renderCollector(collector({ persistence_pending: true })), /Enregistrement.*attente/);
  assert.match(UI.renderCollector(collector({ sample_error: true })), /cache.*échoué/i);
});

test('unavailable information stays distinct from measured zero and false', () => {
  assert.equal(UI.formatValue(null), 'Indisponible');
  assert.equal(UI.formatValue(0), '0');
  assert.equal(UI.formatValue(false), 'non');
  const html = UI.renderCollector(collector({ data: { cpu_percent: 0, memory_limit: null, enabled: false } }));
  assert.match(html, /0/);
  assert.match(html, /Indisponible/);
  assert.match(html, /non/);
  assert.match(html, /0 ms/);
});

test('renders stale previous results with explicit failure and last success', () => {
  const html = UI.renderCollector(collector({ status: 'error', stale: true, reason: 'Permissions insuffisantes', last_attempt: 200 }));
  assert.match(html, /Erreur/);
  assert.match(html, /Dernières données conservées/);
  assert.match(html, /Dernière réussite/);
  assert.match(html, /Permissions insuffisantes/);
});

test('every status has an explicit French label and pending is not healthy', () => {
  for (const [status, label] of Object.entries({ available: 'Disponible', unavailable: 'Indisponible', unsupported: 'Non pris en charge', error: 'Erreur', pending: 'En attente' })) {
    assert.match(UI.renderCollector(collector({ status, last_success: null })), new RegExp(label));
  }
  assert.match(UI.renderCollector(collector({ status: 'pending', last_success: null })), /jamais/);
});

test('filters search, category and status without losing unavailable groups', () => {
  const all = [collector(), collector({ id: 'systemd_timers', title: 'Tâches planifiées', category: 'services', status: 'unavailable' })];
  assert.equal(UI.filterCollectors(all, { query: 'planifiées' }).length, 1);
  assert.equal(UI.filterCollectors(all, { category: 'services', status: 'unavailable' }).length, 1);
  assert.equal(UI.filterCollectors(all, { category: 'services', status: 'available' }).length, 0);
  const html = UI.renderInventory({ collectors: all }, {});
  assert.match(html, /Tâches planifiées/);
  assert.match(html, /aria-label="Catégorie"/);
  assert.match(html, /format=csv/);
});

test('untrusted values are escaped and sensitive fields are never rendered', () => {
  const html = UI.renderCollector(collector({ id: 'bad"<', title: '<img src=x onerror=alert(1)>', reason: '<script>oops</script>', data: { item: '<svg onload=alert(1)>', token: 'DO_NOT_DISPLAY', env: { PASSWORD: 'DO_NOT_DISPLAY' }, list: [{ name: '<script>bad</script>' }] } }));
  assert.doesNotMatch(html, /<img|<script|<svg onload|DO_NOT_DISPLAY/);
  assert.match(html, /&lt;img/);
  assert.match(html, /&lt;script/);
});

test('large and deeply nested payloads stay bounded, readable and are not raw JSON', () => {
  const html = UI.renderData({ rows: Array.from({ length: 1000 }, (_, i) => ({ name: 'item-' + i, cpu: i })), nested: { a: { b: { c: { d: 'too-deep' } } } } });
  assert.ok(html.length < 40000);
  assert.doesNotMatch(html, /item-999|too-deep|<pre>/);
  assert.match(html, /premier/);
  assert.match(html, /<table/);
});

test('history and changes escape event data and compare needs two distinct snapshots', () => {
  const html = UI.renderHistory([{ id: 1, ts: 100, status: 'available' }, { id: 2, ts: 200, status: 'available' }], [{ ts: 200, kind: 'changed', message: '<script>bad</script>' }], 'psi');
  assert.match(html, /inventory-compare/);
  assert.match(html, /value="1"/);
  assert.match(html, /&lt;script/);
  assert.doesNotMatch(html, /<script/);
  assert.match(UI.renderHistory([], [], 'psi'), /Pas encore/);
});

test('comparison presents added, removed and modified fields safely', () => {
  const html = UI.renderComparison({ collector_id: 'psi', before: 1, after: 2, added: { zero: 0 }, removed: { old: '<script>x</script>' }, changed: { count: { before: 1, after: 2 } } });
  assert.match(html, /Ajouts/);
  assert.match(html, /Suppressions/);
  assert.match(html, /Modifications/);
  assert.doesNotMatch(html, /<script/);
});

test('pressure schema exposes partial visibility and missing resources alongside measured zero', () => {
  const html = UI.renderCollector(collector({ id: 'pressure', data: { resources: { cpu: { some: { avg10: 0, avg60: 1, avg300: 2, total_us: 100 } }, memory: null, io: null }, missing: ['memory', 'io'], partial: true, unit: 'percent' } }));
  assert.match(html, /0 %/);
  assert.match(html, /Visibilité partielle/);
  assert.match(html, /Mémoire/);
  assert.match(html, /Entrées\/sorties/);
});

test('metric series preserves null samples and genuine zero without inventing observations', () => {
  const series = UI.metricSeries([{ ts: 1, metrics: { 'cpu.some.avg10': 0 } }, { ts: 2, metrics: { 'cpu.some.avg10': null } }, { ts: 3, metrics: { 'cpu.some.avg10': NaN } }]);
  assert.deepEqual(series[0].values, [[1, 0], [2, null], [3, null]]);
  assert.ok(series[1].values.every(([, value]) => value === null));
  assert.deepEqual(UI.metricSeries([])[0].values, []);
});

test('navigation loads the inventory renderer before app and exposes its page', () => {
  const index = fs.readFileSync(require.resolve('../static/index.html'), 'utf8');
  assert.ok(index.indexOf('/static/inventory.js') >= 0 && index.indexOf('/static/inventory.js') < index.indexOf('/static/app.js'));
  assert.match(index, /href="#inventory"/);
  const app = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
  assert.match(app, /inventory:\s*\{/);
  assert.match(app, /api\('\/api\/inventory'\)/);
});

test('an inventory response arriving after navigation cannot overwrite the selected page', async () => {
  const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
  const renderCode = source.slice(source.indexOf('async function render() {'), source.indexOf('const liveRender'));
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const content = { innerHTML: '' };
  const state = { page: 'inventory' };
  const context = vm.createContext({ S: state, PAGES: { inventory: { render: () => pending }, system: { render: async () => 'selected system page' } }, $: () => content, decorate: () => {}, esc: (value) => value });
  vm.runInContext(renderCode, context);
  const first = context.render();
  state.page = 'system';
  await context.render();
  release('old inventory response');
  await first;
  assert.equal(content.innerHTML, 'selected system page');
});

async function softwarePage(data) {
  const source = fs.readFileSync(require.resolve('../static/app.js'), 'utf8');
  const pageCode = source.slice(source.indexOf('const PAGES = {'), source.indexOf('// ---------- Rendu'));
  const context = vm.createContext({ S: {}, MonitorInventory: UI, api: async () => data, bytes: (n) => n == null ? '—' : `${n} octets`, esc: (v) => String(v ?? ''), stamp: () => 'date', table: (_heads, rows) => rows.join(''), panel: (title, body) => `<h2>${title}</h2>${body}`, badge: (_kind, title) => title });
  vm.runInContext(pageCode, context);
  return vm.runInContext('PAGES.software.render()', context);
}

test('software page preserves unknown RPM classification, size and update metadata', async () => {
  const html = await softwarePage({ checked: 100, manager: 'rpm', updates_status: 'unsupported', notes: ['Cache local non disponible'], packages: [{ name: 'service.x86_64', version: '1.0', size_kb: null, manual: null, upgradable: null }], runtimes: [{ name: 'Python', version: null, version_status: 'unavailable', path: '/usr/bin/python3' }], updates: { count: null, security: null } });
  assert.match(html, /Origine inconnue/);
  assert.match(html, /Taille inconnue/);
  assert.match(html, /Mise à jour inconnue/);
  assert.match(html, /Version inaccessible/);
  assert.match(html, /rpm/);
  assert.match(html, /Cache local non disponible/);
  assert.doesNotMatch(html, /aucune de sécurité|0 installés à la main|0 octets/);
});

test('software error does not claim an empty installed inventory or known security updates', async () => {
  const html = await softwarePage({ checked: 100, packages: [], error: 'Paquets inaccessibles', updates: { count: null, security: null } });
  assert.match(html, /Paquets inaccessibles/);
  assert.match(html, /Paquets installés<\/div><div class="metric-value">Inconnu/);
  assert.match(html, /Mises à jour<\/div><div class="metric-value[^>]*>Inconnu/);
  assert.doesNotMatch(html, /aucune de sécurité|0 installés à la main/);
});

test('known package zero values stay visible and update summary uses cached total', async () => {
  const html = await softwarePage({ checked: 100, manager: 'dpkg', packages: [{ name: 'empty-doc', version: '1', size_kb: 0, manual: false, upgradable: false }], updates: { count: 7, security: 0 } });
  assert.match(html, /Mises à jour<\/div><div class="metric-value[^>]*>7/);
  assert.match(html, /aucune de sécurité/);
  assert.match(html, /Dépendance/);
  assert.match(html, /0 o/);
});

test('Docker topology has dedicated resource and observed-relation tables', () => {
  const html = UI.renderCollector(collector({ id: 'docker_topology', category: 'containers', data: { containers: [{ id: 'a', name: '<app>', image: 'app:1', compose_project: 'stack', compose_service: 'api', networks: ['private'], volumes: [{ name: 'store', type: 'volume' }], ports: [{ container_port: 8080, protocol: 'tcp', host_address: '127.0.0.1', host_port: 9000 }] }], relations: [{ source_id: 'a', source_type: 'container', target_id: 'store', target_type: 'volume', kind: 'mounts', evidence: 'docker_inspect', confidence: 'observed' }], partial: true, limits: ['Images sans preuve de lien applicatif'] } }));
  assert.match(html, /Ressources Docker/);
  assert.match(html, /Relations observées/);
  assert.match(html, /Preuve/);
  assert.match(html, /Visibilité partielle/);
  assert.match(html, /127.0.0.1:9000/);
  assert.match(html, /8080\/tcp/);
  assert.match(html, /&lt;app&gt;/);
  assert.doesNotMatch(html, /<app>/);
});
