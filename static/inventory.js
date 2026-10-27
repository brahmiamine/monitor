/* Rendus de l'inventaire : fonctions sans accès réseau, DOM ni données de démonstration. */
(function (root) {
  'use strict';
  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const list = (value) => Array.isArray(value) ? value : [];
  const entries = (value) => value && typeof value === 'object' && !Array.isArray(value) ? Object.entries(value) : [];
  const privateField = (key) => /(?:^|_)(?:env|environment|password|passwd|token|secret|credential|private_key|cmdline|command_line)(?:$|_)/i.test(key);
  const statuses = { available: ['available', 'Disponible'], unavailable: ['unavailable', 'Indisponible'], unsupported: ['unsupported', 'Non pris en charge'], error: ['error', 'Erreur'], pending: ['pending', 'En attente'] };
  const categories = { resources: 'Ressources', performance: 'Performance', system: 'Système', services: 'Services', databases: 'Bases de données', security: 'Sécurité', containers: 'Conteneurs', network: 'Réseau', storage: 'Stockage', software: 'Logiciels', inventory: 'Inventaire', topology: 'Cartographie' };
  const labels = { cpu: 'CPU', memory: 'Mémoire', io: 'Entrées/sorties', some: 'Attente partielle', full: 'Attente totale', avg10: 'Moyenne 10 s (%)', avg60: 'Moyenne 1 min (%)', avg300: 'Moyenne 5 min (%)', total: 'Total (µs)', unit: 'Unité', name: 'Nom', state: 'État', active_state: 'Activité', sub_state: 'Sous-état', unit_file_state: 'Démarrage automatique', restarts: 'Redémarrages', n_restarts: 'Redémarrages', memory_current: 'Mémoire actuelle (octets)', memory_max: 'Limite mémoire (octets)', cpu_usage_nsec: 'CPU cumulé (ns)', cpu_quota: 'Quota CPU', cpu_period: 'Période CPU', pids_current: 'Processus actuels', pids_max: 'Limite de processus', detected: 'Détecté', evidence: 'Preuve de détection', status: 'Statut', reason: 'Explication', port: 'Port', socket: 'Socket', engine: 'Moteur', source: 'Source', enabled: 'Activé', next: 'Prochaine exécution', last: 'Dernière exécution', activates: 'Service déclenché', timers: 'Tâches planifiées', services: 'Services', engines: 'Moteurs détectés', apparmor: 'AppArmor', selinux: 'SELinux', available: 'Disponible', limits: 'Limites', warnings: 'Limites de visibilité', notes: 'Précisions' };
  const label = (key) => labels[key] || categories[key] || String(key).replace(/[_./]+/g, ' ');
  Object.assign(labels, { dependencies: 'Dépendances', permissions: 'Permissions nécessaires', timeout_seconds: 'Délai maximal (secondes)', current_bytes: 'Utilisation actuelle (octets)', effective_limit_bytes: 'Limite effective (octets)', quota_cores: 'Quota CPU (cœurs)', effective_limit_cores: 'Limite CPU effective (cœurs)', cpuset_count: 'CPU autorisés', host_logical_cpus: 'CPU logiques visibles', effective_limit: 'Limite effective', current: 'Utilisation actuelle', scope: 'Périmètre observé', version: 'Version', note: 'Précision', missing: 'Informations inaccessibles', partial: 'Visibilité partielle', count: 'Éléments collectés', truncated: 'Liste limitée', next_at: 'Prochaine exécution (date système)', last_at: 'Dernière exécution (date système)', enabled: 'Démarrage automatique', memory_bytes: 'Mémoire (octets)', cpu_seconds: 'CPU cumulé (secondes)', tasks: 'Tâches', result: 'Dernier résultat', requires: 'Services requis', wants: 'Services souhaités', after: 'Ordre de démarrage', processes: 'Processus détectés', pid: 'PID', listeners: 'Ports attribués au processus', unattributed_listeners: 'Ports sans processus identifié', candidate_engine: 'Moteur possible, à vérifier', confirmed: 'Association confirmée', metrics_status: 'Métriques du moteur', metrics_reason: 'Limite d’accès aux métriques', database_connections_opened: 'Connexions ouvertes aux bases', rules_status: 'Lecture des règles', effective_uid: 'Identifiant effectif du collecteur', tools: 'Outils exécutables détectés', firewall: 'Pare-feu', total_us: 'Attente cumulée (µs)' });
  const stamp = (ts) => typeof ts === 'number' && Number.isFinite(ts) && ts > 0 ? new Date(ts * 1000).toLocaleString('fr-FR') : 'jamais';
  const formatValue = (value) => {
    if (value == null) return 'Indisponible';
    if (typeof value === 'boolean') return value ? 'oui' : 'non';
    if (typeof value === 'number') return Number.isFinite(value) ? value.toLocaleString('fr-FR', { maximumFractionDigits: 3 }) : 'Indisponible';
    const states = { unknown: 'inconnu', unavailable: 'indisponible', unsupported: 'non pris en charge', enabled: 'activé', disabled: 'désactivé', enforcing: 'appliqué', permissive: 'permissif', monitor_process: 'processus Monitor uniquement', kernel_state_only: 'états du noyau accessibles', not_collected: 'non collectées', default_port_only: 'port habituel seulement, association à vérifier', process_pid: 'PID du processus', observed: 'observé' };
    return Object.hasOwn(states, value) ? states[value] : String(value).slice(0, 500) || 'Non renseigné';
  };
  const cell = (value) => esc(formatValue(value));
  const badge = (status) => {
    const [kind, text] = statuses[status] || ['pending', 'Statut inconnu'];
    return `<span class="inventory-status inventory-${kind}"><span class="status-dot"></span>${text}</span>`;
  };
  const table = (headers, rows, empty) => `<div class="table-wrap"><table class="data-table"><thead><tr>${headers.map((h) => `<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('') || `<tr><td colspan="${headers.length}"><div class="empty">${esc(empty || 'Aucune donnée disponible.')}</div></td></tr>`}</tbody></table></div>`;
  const notice = (text) => `<p class="inventory-note">${esc(text)}</p>`;
  const bytes = (value) => {
    if (value == null || !Number.isFinite(value) || value < 0) return 'Taille inconnue';
    const units = ['o', 'Ko', 'Mo', 'Go', 'To'];
    let index = 0, n = value;
    while (n >= 1024 && index < units.length - 1) { n /= 1024; index++; }
    return `${n.toLocaleString('fr-FR', { maximumFractionDigits: index ? 1 : 0 })} ${units[index]}`;
  };
  const section = (title, body, note = '') => `<section class="panel"><div class="panel-heading"><div><h2>${esc(title)}</h2>${note ? `<p>${esc(note)}</p>` : ''}</div></div>${body}</section>`;

  function renderData(data, depth = 0) {
    if (depth > 3) return '<span class="muted">Détail limité pour préserver la lisibilité.</span>';
    if (data == null || typeof data !== 'object') return cell(data);
    if (Array.isArray(data)) {
      if (!data.length) return '<p class="inventory-note">Aucun élément dans cette collecte.</p>';
      const shown = data.slice(0, 60);
      const capped = data.length > shown.length ? notice(`${shown.length} premiers éléments sur ${data.length}. L’export contient les données collectées complètes.`) : '';
      if (shown.every((row) => row && typeof row === 'object' && !Array.isArray(row))) {
        const keys = [...new Set(shown.flatMap((row) => Object.keys(row)))].filter((k) => !privateField(k)).slice(0, 10);
        const rows = shown.map((row) => `<tr>${keys.map((key) => `<td>${row[key] && typeof row[key] === 'object' ? `<details><summary>Voir</summary>${renderData(row[key], depth + 1)}</details>` : cell(row[key])}</td>`).join('')}</tr>`);
        return table(keys.map(label), rows) + capped;
      }
      return `<ul class="inventory-list">${shown.map((value) => `<li>${typeof value === 'object' && value != null ? renderData(value, depth + 1) : cell(value)}</li>`).join('')}</ul>${capped}`;
    }
    const shown = entries(data).filter(([key]) => !privateField(key)).slice(0, 24);
    if (!shown.length) return '<p class="inventory-note">Aucune donnée détaillée disponible.</p>';
    return `<dl class="inventory-fields">${shown.map(([key, value]) => `<div><dt>${esc(label(key))}</dt><dd>${value && typeof value === 'object' ? `<details${depth === 0 ? ' open' : ''}><summary>${Array.isArray(value) ? `${value.length} élément(s)` : 'Détail'}</summary>${renderData(value, depth + 1)}</details>` : cell(value)}</dd></div>`).join('')}</dl>`;
  }

  function renderPSI(data) {
    const resources = data.resources || data.pressure || data;
    const rows = [];
    for (const resource of ['cpu', 'memory', 'io']) {
      const groups = resources[resource];
      if (!groups || typeof groups !== 'object') continue;
      for (const scope of ['some', 'full']) {
        if (!groups[scope]) continue;
        const values = groups[scope];
        rows.push(`<tr><th scope="row">${esc(label(resource))} · ${esc(label(scope))}</th>${['avg10', 'avg60', 'avg300'].map((key) => `<td>${values[key] == null ? 'Indisponible' : cell(values[key]) + ' %'}</td>`).join('')}</tr>`);
      }
    }
    if (!rows.length) return renderData(data);
    const missing = list(data.missing).map(label);
    const visibility = data.partial ? notice(`Visibilité partielle${missing.length ? ' : ' + missing.join(', ') + ' indisponible(s)' : ''}.`) : '';
    return visibility + table(['Ressource', '10 secondes', '1 minute', '5 minutes'], rows) + notice('Part du temps où des tâches attendent une ressource. Ce n’est pas le pourcentage d’utilisation du CPU ou de la mémoire.') + (data.notes ? renderData({ notes: data.notes }) : '');
  }

  function renderTopology(data) {
    const containers = list(data.containers), relations = list(data.relations);
    const names = new Map(containers.map((c) => [c.id, c.name || c.id]));
    const joined = (values) => values.length ? values.map(esc).join('<br>') : '<span class="muted">Aucun élément visible</span>';
    const port = (p) => {
      const address = p.host_address == null ? 'Adresse inconnue' : String(p.host_address).includes(':') ? `[${p.host_address}]` : String(p.host_address);
      return `${address}:${p.host_port == null ? '?' : p.host_port} → ${p.container_port == null ? '?' : p.container_port}/${p.protocol || '?'}`;
    };
    const kinds = { mounts: 'Monte le volume', mounted_volume: 'Monte le volume', uses_volume: 'Utilise le volume', attached_to: 'Rattaché au réseau', connected_to: 'Connecté au réseau', joins_network: 'Rejoint le réseau', belongs_to: 'Appartient au projet', compose_project: 'Projet Compose', exposes: 'Publie le port' };
    const proof = (r) => r.confidence === 'observed' ? `Observée · ${r.evidence || 'source non renseignée'}` : `À vérifier · ${r.evidence || r.confidence || 'preuve non renseignée'}`;
    return (data.partial ? notice('Visibilité partielle : certaines métadonnées Docker ne sont pas accessibles.') : '') +
      '<h3 class="inventory-section-title">Ressources Docker</h3>' + table(['Conteneur', 'Image', 'Projet / service Compose', 'Réseaux', 'Volumes', 'Ports publiés'], containers.slice(0, 60).map((c) => `<tr><th scope="row">${esc(c.name || c.id)}</th><td>${cell(c.image)}</td><td>${joined([c.compose_project, c.compose_service].filter((v) => v != null && v !== ''))}</td><td>${joined(list(c.networks))}</td><td>${joined(list(c.volumes).map((v) => `${v.name || 'Nom indisponible'} (${v.type || 'type inconnu'})`))}</td><td>${joined(list(c.ports).map(port))}</td></tr>`), 'Aucun conteneur visible.') +
      '<h3 class="inventory-section-title">Relations observées</h3>' + table(['Source', 'Relation', 'Cible', 'Preuve'], relations.slice(0, 100).map((r) => `<tr><td>${esc(names.get(r.source_id) || r.source_id)}<small>${esc(r.source_type)}</small></td><td>${esc(kinds[r.kind] || label(r.kind || 'relation inconnue'))}</td><td>${esc(names.get(r.target_id) || r.target_id)}<small>${esc(r.target_type)}</small></td><td>${esc(proof(r))}</td></tr>`), 'Aucune relation établie par les métadonnées disponibles.') +
      (containers.length > 60 || relations.length > 100 ? notice('Affichage limité aux 60 premiers conteneurs et aux 100 premières relations. Consultez l’export pour l’inventaire complet.') : '') +
      (data.limits ? renderData({ limits: data.limits }) : '') + notice('Les relations décrivent les métadonnées Docker observées. Elles ne prouvent pas à elles seules une dépendance applicative ou un accès effectif à une base de données.');
  }

  function renderSoftware(data, { query = '', manualOnly = false } = {}) {
    const packages = list(data.packages), filtered = packages.filter((p) => (!manualOnly || p.manual === true) && String(p.name).toLocaleLowerCase('fr-FR').includes(query.toLocaleLowerCase('fr-FR')));
    const shown = filtered.slice(0, 300);
    const knownSize = !data.error && packages.every((p) => typeof p.size_kb === 'number' && Number.isFinite(p.size_kb) && p.size_kb >= 0);
    const totalSize = knownSize ? bytes(packages.reduce((sum, p) => sum + p.size_kb * 1024, 0)) : 'Inconnu';
    const knownOrigins = !data.error && packages.every((p) => typeof p.manual === 'boolean');
    const manual = knownOrigins ? `${packages.filter((p) => p.manual === true).length} installés à la main` : 'Origine d’installation inconnue ou partielle';
    const up = typeof data.updates?.count === 'number' && Number.isFinite(data.updates.count) && data.updates.count >= 0 ? data.updates.count : null;
    const security = data.updates?.security;
    const securityNote = security === 0 ? 'aucune de sécurité signalée' : typeof security === 'number' && security > 0 ? `${security} de sécurité` : 'Correctifs de sécurité : inconnus';
    const metric = (title, value, note, bad = false) => `<div class="metric"><div class="metric-label">${esc(title)}</div><div class="metric-value${bad ? ' bad' : ''}">${esc(value)}</div><div class="metric-note">${esc(note)}</div></div>`;
    const updatesStatus = { available: 'disponible', unavailable: 'inaccessible', unsupported: 'non pris en charge' };
    const packageRows = shown.map((p) => `<tr><th scope="row">${esc(p.name)}</th><td>${cell(p.version)}</td><td>${p.size_kb == null ? 'Taille inconnue' : bytes(p.size_kb * 1024)}</td><td>${p.manual === true ? 'Manuel' : p.manual === false ? 'Dépendance' : 'Origine inconnue'}</td><td>${p.upgradable === true ? badge('pending') + ' Mise à jour disponible' : p.upgradable === false ? 'Aucune mise à jour signalée' : 'Mise à jour inconnue'}</td></tr>`);
    return `<div class="metric-grid">${metric('Paquets installés', data.error ? 'Inconnu' : packages.length, manual)}${metric('Espace occupé', totalSize, knownSize ? 'par les paquets système' : 'Tailles des paquets absentes ou inventaire incomplet')}${metric('Mises à jour', up == null ? 'Inconnu' : up, securityNote, up != null && up > 0)}${metric('Docker — éléments visibles', list(data.docker_images).length, `${list(data.docker_volumes).length} volume(s) visible(s)`)}</div>` +
      notice(`Gestionnaire : ${data.manager || 'non identifié'} · État du contrôle des mises à jour : ${updatesStatus[data.updates_status] || 'inconnu'} · Inventaire du ${stamp(data.checked)}`) +
      list(data.notes).map(notice).join('') +
      section('Environnements d’exécution et outils', table(['Outil', 'Version', 'Chemin'], list(data.runtimes).map((r) => `<tr><th scope="row">${esc(r.name)}</th><td>${r.version == null || r.version === '' || r.version_status === 'unavailable' ? 'Version inaccessible' : esc(r.version)}</td><td>${cell(r.path)}</td></tr>`), 'Aucun outil détecté.')) +
      '<div class="inventory-section-title"></div>' + section('Images Docker', table(['Image', 'Étiquette', 'Taille', 'Créée'], list(data.docker_images).map((i) => `<tr><th scope="row">${esc(i.repository)}</th><td>${cell(i.tag)}</td><td>${cell(i.size)}</td><td>${cell(i.created)}</td></tr>`), 'Aucune image visible : absence ou accès Docker indisponible.'), `${list(data.docker_volumes).length} volume(s) visible(s) : ${list(data.docker_volumes).slice(0, 8).join(', ')}`) +
      `<div class="toolbar inventory-section-title"><input id="filter" type="search" placeholder="Filtrer les paquets…" value="${esc(query)}" aria-label="Filtrer les paquets"><button data-manual="1" class="${manualOnly ? 'primary' : ''}">Installés à la main</button><span class="muted">${filtered.length} paquet(s) visible(s)${filtered.length > shown.length ? ' · 300 premiers affichés' : ''}</span></div>` +
      (data.error ? `<div class="notice error">${esc(data.error)}</div>` : section('Paquets système', table(['Paquet', 'Version', 'Taille', 'Origine', 'Mise à jour'], packageRows, manualOnly ? 'Aucun paquet identifié comme installé manuellement.' : 'Aucun paquet dans l’inventaire local.')));
  }

  function renderCollector(collector) {
    const id = String(collector.id || '');
    const fresh = collector.stale ? '<span class="inventory-stale">Dernières données conservées · périmées</span>' : '';
    const persistence = collector.persistence_error ? '<p class="inventory-reason">L’historique n’a pas pu être enregistré. La collecte reste disponible.</p>' : collector.persistence_pending ? '<p class="inventory-note">Enregistrement de l’historique en attente.</p>' : '';
    const cacheError = collector.sample_error ? '<p class="inventory-reason">La mise à jour du cache de compatibilité a échoué.</p>' : '';
    const reason = persistence + cacheError + (collector.reason ? `<p class="inventory-reason">${esc(collector.reason)}</p>` : '');
    const detail = collector.data == null ? '<p class="inventory-note">Aucune donnée collectée. Le statut ci-dessus explique la disponibilité.</p>' : ['pressure', 'psi'].includes(id) ? renderPSI(collector.data) : id === 'docker_topology' ? renderTopology(collector.data) : renderData(collector.data);
    return `<section class="panel inventory-card" data-collector="${esc(id)}"><div class="panel-heading"><div><h2>${esc(collector.title || id)}</h2><p>${esc(categories[collector.category] || collector.category || 'Inventaire')}</p></div>${badge(collector.status)}</div>${fresh}${reason}<div class="inventory-timing"><span>Dernière réussite <strong>${esc(stamp(collector.last_success))}</strong></span><span>Dernière tentative ${esc(stamp(collector.last_attempt))}</span><span>${collector.duration_ms == null ? 'Durée indisponible' : cell(collector.duration_ms) + ' ms'} · toutes les ${cell(collector.interval)} s</span></div><details class="inventory-detail"><summary>Consulter les données</summary><div class="inventory-data">${detail}</div></details><details class="inventory-detail"><summary>Méthode et limites</summary><div class="inventory-data">${renderData({ dependencies: collector.dependencies || [], permissions: collector.permissions || [], timeout_seconds: collector.timeout, ...(collector.limits ? { limits: collector.limits } : {}) })}</div></details><div class="inventory-actions"><button type="button" data-inventory-history="${esc(id)}">Historique et changements</button></div></section>`;
  }

  function filterCollectors(collectors, { query = '', category = '', status = '' } = {}) {
    const q = query.toLocaleLowerCase('fr-FR').trim();
    return list(collectors).filter((c) => (!category || c.category === category) && (!status || c.status === status) && (!q || [c.id, c.title, c.category, c.reason].join(' ').toLocaleLowerCase('fr-FR').includes(q)));
  }

  function renderInventory(data, filters = {}) {
    const all = list(data.collectors), shown = filterCollectors(all, filters);
    const available = all.filter((c) => c.status === 'available').length;
    const stale = all.filter((c) => c.stale).length;
    const failed = all.filter((c) => c.status === 'error').length;
    const cats = [...new Set(all.map((c) => c.category).filter(Boolean))].sort();
    const option = (value, text, selected) => `<option value="${esc(value)}"${selected === value ? ' selected' : ''}>${esc(text)}</option>`;
    return `<div class="metric-grid inventory-summary" aria-label="Résumé de l’inventaire"><div class="metric"><div class="metric-label">Groupes détectés</div><div class="metric-value">${all.length}</div></div><div class="metric"><div class="metric-label">Disponibles</div><div class="metric-value">${available}</div></div><div class="metric"><div class="metric-label">Données périmées</div><div class="metric-value">${stale}</div></div><div class="metric"><div class="metric-label">Erreurs de collecte</div><div class="metric-value">${failed}</div></div></div><div class="toolbar inventory-toolbar"><label>Rechercher<input id="inventory-search" type="search" value="${esc(filters.query || '')}" placeholder="Ressource, collecteur…" aria-label="Rechercher dans l’inventaire"></label><label>Catégorie<select id="inventory-category" aria-label="Catégorie">${option('', 'Toutes les catégories', filters.category)}${cats.map((c) => option(c, categories[c] || c, filters.category)).join('')}</select></label><label>Statut<select id="inventory-status" aria-label="Statut">${option('', 'Tous les statuts', filters.status)}${Object.entries(statuses).map(([k, [, text]]) => option(k, text, filters.status)).join('')}</select></label><button type="button" id="inventory-refresh">Actualiser l’affichage</button><a class="button-link" href="/api/inventory/export?format=json">Export JSON</a><a class="button-link" href="/api/inventory/export?format=csv">Export CSV</a></div><p class="inventory-note" aria-live="polite">${shown.length} groupe(s) affiché(s) · Vue du ${esc(stamp(data.collected_at))}. Les collecteurs s’exécutent automatiquement ; actualiser cette page ne lance aucune commande.</p><div class="inventory-grid">${shown.map(renderCollector).join('') || '<div class="empty">Aucun groupe ne correspond aux filtres.</div>'}</div>`;
  }

  function diffRows(part, kind) {
    const items = Array.isArray(part) ? part : entries(part).map(([path, value]) => ({ path, ...(kind === 'changed' && value && typeof value === 'object' ? value : { value }) }));
    return items.slice(0, 60).map((item) => `<tr><th scope="row">${esc(item.path ?? item.name ?? '')}</th>${kind === 'changed' ? `<td>${renderData(item.before, 1)}</td><td>${renderData(item.after, 1)}</td>` : `<td>${renderData(item.value, 1)}</td>`}</tr>`);
  }

  function renderComparison(diff) {
    return `<div class="inventory-comparison"><h3>Ajouts</h3>${table(['Élément', 'Valeur'], diffRows(diff.added, 'added'), 'Aucun ajout.')}<h3>Suppressions</h3>${table(['Élément', 'Ancienne valeur'], diffRows(diff.removed, 'removed'), 'Aucune suppression.')}<h3>Modifications</h3>${table(['Élément', 'Avant', 'Après'], diffRows(diff.changed, 'changed'), 'Aucune modification.')}${diff.truncated ? notice('Comparaison limitée : utilisez les exports pour consulter les inventaires complets.') : ''}</div>`;
  }

  function renderHistory(history, changes, collectorId) {
    const snapshots = list(history).slice(0, 100);
    const options = snapshots.map((r) => `<option value="${esc(r.id)}">${esc(stamp(r.ts))} · #${esc(r.id)}</option>`).join('');
    const compare = snapshots.length > 1 ? `<form id="inventory-compare" class="inventory-compare" data-collector-id="${esc(collectorId)}"><label>Avant<select name="before" aria-label="Inventaire avant">${[...snapshots].reverse().map((r) => `<option value="${esc(r.id)}">${esc(stamp(r.ts))} · #${esc(r.id)}</option>`).join('')}</select></label><label>Après<select name="after" aria-label="Inventaire après">${options}</select></label><button type="submit">Comparer</button></form><div id="inventory-comparison" aria-live="polite"></div>` : notice('Deux inventaires enregistrés sont nécessaires pour comparer les changements.');
    return `<p class="inventory-note">L’historique conserve les changements de l’inventaire ; les mesures fluctuantes disposent d’un historique séparé.</p>${compare}<h3 class="inventory-section-title">Inventaires enregistrés</h3>${table(['Date', 'Statut', 'Référence'], snapshots.map((r) => `<tr><td>${esc(stamp(r.ts))}</td><td>${badge(r.status)}</td><td>#${esc(r.id)}</td></tr>`), 'Pas encore d’inventaire enregistré.')}<h3 class="inventory-section-title">Changements récents</h3>${list(changes).slice(0, 30).map((change) => `<details class="inventory-change"><summary>${esc(stamp(change.ts))} · ${esc(change.message || 'Inventaire modifié')}</summary>${change.diff ? renderComparison(change.diff) : change.message ? notice(change.message) : renderData(change)}</details>`).join('') || notice('Pas encore de changement détecté.')}`;
  }

  function metricSeries(points) {
    return [['cpu.some.avg10', 'Attente CPU'], ['memory.some.avg10', 'Attente mémoire'], ['io.some.avg10', 'Attente E/S']].map(([key, name]) => ({ name, values: list(points).filter((p) => typeof p.ts === 'number' && Number.isFinite(p.ts)).map((p) => [p.ts, typeof p.metrics?.[key] === 'number' && Number.isFinite(p.metrics[key]) ? p.metrics[key] : null]) }));
  }

  const api = { formatValue, filterCollectors, renderData, renderPSI, renderTopology, renderSoftware, renderCollector, renderInventory, renderHistory, renderComparison, metricSeries };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MonitorInventory = api;
})(typeof window !== 'undefined' ? window : globalThis);
