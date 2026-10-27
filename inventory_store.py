"""Bounded inventory persistence over the application's existing DB context manager.

This module never opens a database itself or reads environment/configuration files.
Only technical collector output is accepted; secrets and unbounded JSON are rejected.
"""
import hashlib
import json
import math
import re
import threading
import time
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
MAX_JSON_BYTES = 256 * 1024
MAX_DEPTH = 20
MAX_ITEMS = 20000
MAX_DIFF = 100
STATUSES = {'available', 'unavailable', 'unsupported', 'error', 'pending'}
TABLES = ('inventory_changes', 'inventory_metrics', 'inventory_snapshots', 'inventory_relations',
          'inventory_config', 'inventory_state', 'inventory_migrations')
FORBIDDEN = re.compile(r'(?:password|passwd|secret|credential|private.?key|(?:^|_)token(?:$|_)|^env(?:ironment)?$|^cmdline$|^argv$)', re.I)
IDENTIFIER = re.compile(r'^[a-z][a-z0-9_.-]{0,79}$')


def migration_statements(maria=False):
    """Version 1 is additive; applying it never alters the existing historical tables."""
    ident = 'BIGINT AUTO_INCREMENT PRIMARY KEY' if maria else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    text = 'MEDIUMTEXT' if maria else 'TEXT'
    key = 'VARCHAR(80)' if maria else 'TEXT'
    real = 'DOUBLE' if maria else 'REAL'
    suffix = ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4' if maria else ''
    definitions = [
        ('inventory_migrations', f'version INTEGER PRIMARY KEY, applied_at {real} NOT NULL', []),
        ('inventory_state', f'collector_id {key} PRIMARY KEY, status VARCHAR(20) NOT NULL, reason VARCHAR(300) NOT NULL, last_attempt {real}, last_success {real}, duration_ms {real}, data {text} NOT NULL', []),
        ('inventory_config', f'collector_id {key} PRIMARY KEY, config {text} NOT NULL, updated_at {real} NOT NULL', []),
        ('inventory_snapshots', f'id {ident}, collector_id {key} NOT NULL, ts {real} NOT NULL, status VARCHAR(20) NOT NULL, fingerprint VARCHAR(64) NOT NULL, data {text} NOT NULL', [('inventory_snapshots_collector_ts', 'collector_id, ts')]),
        ('inventory_metrics', f'id {ident}, collector_id {key} NOT NULL, ts {real} NOT NULL, data {text} NOT NULL', [('inventory_metrics_collector_ts', 'collector_id, ts')]),
        ('inventory_changes', f'id {ident}, collector_id {key} NOT NULL, ts {real} NOT NULL, before_id BIGINT NOT NULL, after_id BIGINT NOT NULL, diff {text} NOT NULL', [('inventory_changes_collector_ts', 'collector_id, ts')]),
        ('inventory_relations', f'collector_id {key} PRIMARY KEY, data {text} NOT NULL, updated_at {real} NOT NULL', []),
    ]
    statements = []
    for table, fields, indexes in definitions:
        if maria:
            fields += ''.join(f', KEY {name} ({columns})' for name, columns in indexes)
        statements.append(f'CREATE TABLE IF NOT EXISTS {table} ({fields}){suffix}')
        if not maria:
            statements.extend(f'CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})' for name, columns in indexes)
    return statements


def _ident(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('Identifiant de collecteur invalide')
    return value


def _number(value, *, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Mesure numérique invalide')
    return value


def _limit(value, maximum=200):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError('Limite de lecture invalide')
    return value


def _json(value):
    count = 0
    def walk(node, depth=0):
        nonlocal count
        count += 1
        if depth > MAX_DEPTH or count > MAX_ITEMS:
            raise ValueError('Structure de données trop volumineuse')
        if isinstance(node, dict):
            for key, child in node.items():
                if not isinstance(key, str) or len(key) > 512 or FORBIDDEN.search(key):
                    raise ValueError('Champ sensible ou invalide refusé')
                walk(child, depth+1)
        elif isinstance(node, list):
            for child in node: walk(child, depth+1)
        elif isinstance(node, str):
            if len(node) > MAX_JSON_BYTES:
                raise ValueError('Données trop volumineuses')
            if '://' in node:
                # No credential-bearing URLs are retained, even in an otherwise safe field.
                try:
                    parts = urlsplit(node)
                    if parts.username or parts.password or re.search(r'(?:token|key|password|secret)=', parts.query, re.I):
                        raise ValueError('Adresse contenant un secret refusée')
                except ValueError:
                    raise ValueError('Adresse sensible ou invalide refusée') from None
        elif isinstance(node, float):
            _number(node)
        elif node is not None and not isinstance(node, (bool, int)):
            raise ValueError('Type JSON invalide')
    walk(value)
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if len(encoded.encode('utf-8')) > MAX_JSON_BYTES:
        raise ValueError('Données trop volumineuses')
    return encoded


def _safe_value(value):
    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return value if len(text) <= 512 else {'preview': text[:512], 'truncated': True}


def diff_values(before, after):
    """A bounded structural diff, matching resource lists by stable identity when possible."""
    result = {'added': [], 'removed': [], 'changed': [], 'truncated': False}
    def emit(kind, path, **values):
        if sum(len(result[k]) for k in ('added', 'removed', 'changed')) >= MAX_DIFF:
            result['truncated'] = True
            return
        result[kind].append({'path': (path or '/')[:512], **{k: _safe_value(v) for k, v in values.items()}})
    def part(key): return str(key).replace('~', '~0').replace('/', '~1')
    def keyed(values, key):
        if values and all(isinstance(v, dict) and isinstance(v.get(key), (str, int)) for v in values):
            names = [str(v[key]) for v in values]
            if len(names) == len(set(names)): return dict(zip(names, values))
        return None
    def walk(a, b, path=''):
        if a == b: return
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(a.keys() | b.keys()):
                new = path + '/' + part(key)
                if key not in a: emit('added', new, value=b[key])
                elif key not in b: emit('removed', new, value=a[key])
                else: walk(a[key], b[key], new)
        elif isinstance(a, list) and isinstance(b, list):
            # Use a common identity field rather than treating reordering as a change.
            ka = kb = None
            for field in ('id', 'name', 'unit'):
                ka, kb = keyed(a, field), keyed(b, field)
                if ka is not None and kb is not None: break
            if ka is not None and kb is not None:
                walk(ka, kb, path)
            else:
                for i in range(max(len(a), len(b))):
                    new = path + '/' + str(i)
                    if i >= len(a): emit('added', new, value=b[i])
                    elif i >= len(b): emit('removed', new, value=a[i])
                    else: walk(a[i], b[i], new)
        else: emit('changed', path, before=a, after=b)
    walk(before, after)
    return result


class InventoryStore:
    """Instantiate with engine.db (or another context manager yielding its SQL adapter)."""
    def __init__(self, db_factory, maria=False, retention_days=30, max_snapshots=100,
                 max_metric_rows=5000, max_changes=1000, clock=time.time):
        self.db = db_factory
        self.maria = maria
        self.retention_days = _limit(retention_days, 365)
        self.max_snapshots = _limit(max_snapshots, 1000)
        self.max_metric_rows = _limit(max_metric_rows, 100000)
        self.max_changes = _limit(max_changes, 10000)
        self.clock = clock
        self._lock = threading.RLock()
        self._pruned = None

    def migrate(self):
        with self._lock, self.db() as conn:
            for statement in migration_statements(self.maria): conn.execute(statement)
            verb = 'INSERT IGNORE' if self.maria else 'INSERT OR IGNORE'
            conn.execute(f'{verb} INTO inventory_migrations (version,applied_at) VALUES (?,?)', (SCHEMA_VERSION, self.clock()))

    def rollback(self):
        """Explicit operator action: drops only inventory v1 tables, never legacy tables."""
        with self._lock, self.db() as conn:
            for table in TABLES: conn.execute(f'DROP TABLE IF EXISTS {table}')

    def states(self):
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute('SELECT * FROM inventory_state ORDER BY collector_id').fetchall()]
        for row in rows: row['data'] = json.loads(row['data'])
        return rows

    def record(self, result, snapshot=True, metrics=None, relations=None):
        ident = _ident(result.get('id'))
        status = result.get('status')
        if status not in STATUSES: raise ValueError('Statut de collecte invalide')
        timestamp = _number(result.get('last_attempt', self.clock()))
        duration = _number(result.get('duration_ms'), optional=True)
        raw_reason = str(result.get('reason') or '')[:300]
        # Reasons must be framework messages, never arbitrary exception strings.
        reason = 'Détail sensible masqué' if FORBIDDEN.search(raw_reason) or '://' in raw_reason else raw_reason
        encoded = _json(result.get('data', {})) if status == 'available' else None
        metric_json = None
        if metrics is not None:
            if not isinstance(metrics, dict) or len(metrics) > 128: raise ValueError('Mesures invalides')
            for name, value in metrics.items():
                if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}', name): raise ValueError('Nom de mesure invalide')
                _number(value)
            metric_json = _json(metrics)
        relation_json = None
        if relations is not None:
            if not isinstance(relations, list) or len(relations) > 1000: raise ValueError('Relations invalides')
            allowed = {'source_id', 'source_type', 'target_id', 'target_type', 'kind', 'evidence', 'confidence'}
            kinds = {'container', 'network', 'volume', 'application', 'database', 'service', 'proxy', 'domain', 'filesystem'}
            for relation in relations:
                if not isinstance(relation, dict) or set(relation) != allowed: raise ValueError('Champs de relation invalides')
                if relation['source_type'] not in kinds or relation['target_type'] not in kinds: raise ValueError('Type de ressource invalide')
                if relation['confidence'] not in {'observed', 'inferred'}: raise ValueError('Preuve de relation invalide')
                if any(not isinstance(value, str) or len(value) > 240 or '\n' in value or value.startswith('/') for value in relation.values()): raise ValueError('Relation non technique refusée')
            relation_json = _json(relations)
        saved = None
        with self._lock, self.db() as conn:
            old = conn.execute('SELECT * FROM inventory_state WHERE collector_id=?', (ident,)).fetchone()
            last_success = timestamp if status == 'available' else (old['last_success'] if old else None)
            data = encoded if encoded is not None else (old['data'] if old else '{}')
            values = (status, reason, timestamp, last_success, duration, data, ident)
            if old:
                conn.execute('UPDATE inventory_state SET status=?,reason=?,last_attempt=?,last_success=?,duration_ms=?,data=? WHERE collector_id=?', values)
            else:
                conn.execute('INSERT INTO inventory_state (status,reason,last_attempt,last_success,duration_ms,data,collector_id) VALUES (?,?,?,?,?,?,?)', values)
            if status == 'available' and snapshot:
                previous = conn.execute('SELECT * FROM inventory_snapshots WHERE collector_id=? ORDER BY ts DESC,id DESC LIMIT 1', (ident,)).fetchone()
                fingerprint = hashlib.sha256(data.encode()).hexdigest()
                if not previous or previous['fingerprint'] != fingerprint:
                    saved = conn.execute('INSERT INTO inventory_snapshots (collector_id,ts,status,fingerprint,data) VALUES (?,?,?,?,?)', (ident,timestamp,status,fingerprint,data)).lastrowid
                    if previous:
                        difference = diff_values(json.loads(previous['data']), json.loads(data))
                        if any(difference[key] for key in ('added','removed','changed')):
                            changes = _json(difference)
                            conn.execute('INSERT INTO inventory_changes (collector_id,ts,before_id,after_id,diff) VALUES (?,?,?,?,?)', (ident,timestamp,previous['id'],saved,changes))
            if status == 'available' and metric_json is not None:
                conn.execute('INSERT INTO inventory_metrics (collector_id,ts,data) VALUES (?,?,?)', (ident,timestamp,metric_json))
            if status == 'available' and relation_json is not None:
                conn.execute('DELETE FROM inventory_relations WHERE collector_id=?', (ident,))
                conn.execute('INSERT INTO inventory_relations (collector_id,data,updated_at) VALUES (?,?,?)', (ident,relation_json,timestamp))
        if self._pruned is None or self.clock() - self._pruned >= 60:
            self.prune()
        return saved

    def history(self, collector_id=None, limit=100, include_data=False):
        _limit(limit)
        clause, args = (' WHERE collector_id=?', [_ident(collector_id)]) if collector_id else ('', [])
        columns = 'id,collector_id,ts,status' + (',data' if include_data else '')
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(f'SELECT {columns} FROM inventory_snapshots{clause} ORDER BY ts DESC,id DESC LIMIT ?', (*args,limit)).fetchall()]
        if include_data:
            for row in rows: row['data'] = json.loads(row['data'])
        return rows

    def changes(self, collector_id=None, limit=100):
        _limit(limit)
        clause, args = (' WHERE collector_id=?', [_ident(collector_id)]) if collector_id else ('', [])
        with self.db() as conn:
            rows = [dict(r) for r in conn.execute(f'SELECT * FROM inventory_changes{clause} ORDER BY ts DESC,id DESC LIMIT ?', (*args,limit)).fetchall()]
        for row in rows: row['diff'] = json.loads(row['diff'])
        return rows

    def compare(self, before_id, after_id):
        if any(isinstance(v,bool) or not isinstance(v,int) for v in (before_id,after_id)): raise ValueError('Instantané invalide')
        with self.db() as conn:
            a = conn.execute('SELECT * FROM inventory_snapshots WHERE id=?',(before_id,)).fetchone()
            b = conn.execute('SELECT * FROM inventory_snapshots WHERE id=?',(after_id,)).fetchone()
        if not a or not b: raise LookupError('Instantané introuvable ou expiré')
        if a['collector_id'] != b['collector_id']: raise ValueError('Les instantanés doivent appartenir au même collecteur')
        return {'collector_id': a['collector_id'], 'before': before_id, 'after': after_id, **diff_values(json.loads(a['data']), json.loads(b['data']))}

    def metrics(self, collector_id, hours=24, limit=360, now=None):
        _ident(collector_id); _limit(hours,720); _limit(limit,1000)
        now = self.clock() if now is None else _number(now)
        bucket = max(1, hours*3600/limit)
        with self.db() as conn:
            data = conn.execute('SELECT ts,data FROM inventory_metrics WHERE collector_id=? AND ts>? AND ts<=? ORDER BY ts DESC,id DESC LIMIT ?', (collector_id,now-hours*3600,now,self.max_metric_rows)).fetchall()
        groups = {}
        for row in data:
            key = int((row['ts']-(now-hours*3600))/bucket)
            sums, counts = groups.setdefault(key, ({},{}))
            for metric,value in json.loads(row['data']).items():
                sums[metric] = sums.get(metric,0)+value; counts[metric] = counts.get(metric,0)+1
        return [{'ts': now-hours*3600+key*bucket, 'metrics': {name: total/counts[name] for name,total in sums.items()}} for key,(sums,counts) in sorted(groups.items())][-limit:]

    def relations(self, collector_id=None):
        clause, args = (' WHERE collector_id=?', (_ident(collector_id),)) if collector_id else ('', ())
        with self.db() as conn:
            rows = conn.execute('SELECT data FROM inventory_relations'+clause+' ORDER BY collector_id', args).fetchall()
        return [relation for row in rows for relation in json.loads(row['data'])]

    def configure(self, collector_id, config):
        _ident(collector_id)
        if not isinstance(config,dict) or not set(config) <= {'enabled','interval','timeout'}: raise ValueError('Configuration de collecteur invalide')
        if 'enabled' in config and not isinstance(config['enabled'],bool): raise ValueError('Activation invalide')
        for key,maximum in [('interval',86400),('timeout',60)]:
            if key in config: _limit(config[key],maximum)
        encoded = _json(config)
        with self._lock, self.db() as conn:
            conn.execute('DELETE FROM inventory_config WHERE collector_id=?',(collector_id,))
            conn.execute('INSERT INTO inventory_config (collector_id,config,updated_at) VALUES (?,?,?)',(collector_id,encoded,self.clock()))

    def configurations(self):
        with self.db() as conn:
            rows = conn.execute('SELECT collector_id,config FROM inventory_config ORDER BY collector_id').fetchall()
        return {row['collector_id']:json.loads(row['config']) for row in rows}

    def prune(self, now=None):
        now = self.clock() if now is None else _number(now)
        cutoff = now-self.retention_days*86400
        with self._lock, self.db() as conn:
            for table,maximum in [('inventory_snapshots',self.max_snapshots),('inventory_metrics',self.max_metric_rows),('inventory_changes',self.max_changes)]:
                conn.execute(f'DELETE FROM {table} WHERE ts<?',(cutoff,))
                ids = conn.execute(f'SELECT DISTINCT collector_id FROM {table}').fetchall()
                for row in ids:
                    boundary = conn.execute(f'SELECT ts,id FROM {table} WHERE collector_id=? ORDER BY ts DESC,id DESC LIMIT 1 OFFSET ?', (row['collector_id'],maximum-1)).fetchone()
                    if boundary:
                        conn.execute(f'DELETE FROM {table} WHERE collector_id=? AND (ts<? OR (ts=? AND id<?))',(row['collector_id'],boundary['ts'],boundary['ts'],boundary['id']))
            self._pruned = now
