"""Inventories use temporary databases; these tests never import the host engine."""
import contextlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from inventory_store import InventoryStore, migration_statements


@pytest.fixture
def store(tmp_path):
    path = tmp_path / 'inventory.sqlite'
    @contextlib.contextmanager
    def db():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    item = InventoryStore(db, clock=lambda: 1000, max_snapshots=3, max_metric_rows=4)
    item.migrate()
    return item


def result(data, ts=1000, status='available'):
    return {'id': 'services', 'status': status, 'data': data,
            'last_attempt': ts, 'last_success': ts if status == 'available' else None,
            'duration_ms': 4, 'reason': ''}


def test_schema_is_additive_idempotent_and_reversible(store):
    with store.db() as conn:
        conn.execute('CREATE TABLE settings (`key` TEXT PRIMARY KEY, value TEXT)')
        conn.execute("INSERT INTO settings VALUES ('legacy','preserved')")
    store.migrate()
    store.rollback()
    store.rollback()
    with store.db() as conn:
        assert conn.execute('SELECT value FROM settings').fetchone()[0] == 'preserved'
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'inventory_%'").fetchall()
    store.migrate()
    assert store.history() == []


def test_snapshot_dedup_keeps_success_and_failure_freshness(store):
    store.record(result({'items': [{'name': 'ssh', 'active': True}]}))
    store.record(result({'items': [{'active': True, 'name': 'ssh'}]}, 1010))
    assert len(store.history()) == 1
    assert 'data' not in store.history()[0]
    store.record(result({}, 1020, 'error'))
    state = store.states()[0]
    assert state['last_success'] == 1010
    assert state['last_attempt'] == 1020
    assert state['status'] == 'error'
    assert state['data']['items'][0]['name'] == 'ssh'
    assert len(store.history()) == 1


def test_changes_and_compare_are_structured_and_same_collector(store):
    store.record(result({'items': [{'name': 'ssh', 'active': True}]}))
    store.record(result({'items': [{'name': 'ssh', 'active': False}, {'name': 'docker', 'active': True}]}, 1010))
    history = store.history(include_data=True)
    diff = store.compare(history[1]['id'], history[0]['id'])
    assert diff['changed'] and diff['added']
    assert not diff['removed']
    assert len(store.changes()) == 1
    store.record({**result({'x': 1}), 'id': 'other'})
    with pytest.raises(ValueError, match='collecteur'):
        store.compare(history[0]['id'], store.history('other')[0]['id'])
    with pytest.raises(LookupError):
        store.compare(-1, history[0]['id'])


def test_metrics_do_not_generate_inventory_changes_and_are_bounded(store):
    for i in range(9):
        store.record(result({'cpu': i}, 1000+i), snapshot=False, metrics={'cpu.some.avg10': float(i), 'memory.some.avg10': 0})
    assert store.history() == []
    assert store.changes() == []
    store.prune(now=1010)
    series = store.metrics('services', hours=1, limit=3, now=1010)
    assert len(series) <= 3
    assert all('ts' in row and 'metrics' in row for row in series)
    with store.db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM inventory_metrics').fetchone()[0] <= 4


def test_retention_and_snapshot_caps(store):
    for i in range(8):
        store.record(result({'value': i}, 1000+i))
    store.prune(now=1010)
    assert len(store.history()) == 3
    store.prune(now=1000 + 31*86400)
    assert store.history() == []
    assert store.changes() == []
    assert store.states()[0]['last_success'] == 1007


@pytest.mark.parametrize('data', [
    {'password': 'NEVER_PERSIST_THIS'}, {'safe': {'telegram_token': 'NEVER_PERSIST_THIS'}},
    {'Env': ['PRIVATE=NEVER_PERSIST_THIS']}, {'url': 'https://user:NEVER_PERSIST_THIS@example.org/'},
    {'number': float('nan')}, {'number': float('inf')}, {'huge': 'a'*262145},
])
def test_rejects_sensitive_invalid_or_oversize_payloads(store, data):
    with pytest.raises(ValueError):
        store.record(result(data))
    assert store.history() == []
    assert 'NEVER_PERSIST_THIS' not in json.dumps(store.states())


def test_depth_limit_and_metric_validation(store):
    data = {'a': {}}
    node = data['a']
    for _ in range(30):
        node['a'] = {}; node = node['a']
    with pytest.raises(ValueError):
        store.record(result(data))
    with pytest.raises(ValueError):
        store.record(result({}), snapshot=False, metrics={'x': 'not-a-number'})


def test_relations_and_configuration_accept_only_whitelisted_fields(store):
    relation = {'source_id': 'container:web', 'source_type': 'container',
                'target_id': 'network:internal', 'target_type': 'network',
                'kind': 'attached_to', 'evidence': 'docker.inspect.network', 'confidence': 'observed'}
    store.record(result({'ok': True}), relations=[relation])
    assert store.relations('services')[0] == relation
    store.configure('services', {'enabled': True, 'interval': 120, 'timeout': 3})
    assert store.configurations()['services']['interval'] == 120
    with pytest.raises(ValueError):
        store.configure('services', {'password': 'forbidden'})
    with pytest.raises(ValueError):
        store.record(result({}), relations=[{**relation, 'env': 'forbidden'}])


def test_bounds_identifiers_and_query_limits(store):
    with pytest.raises(ValueError): store.record({**result({}), 'id': '../private'})
    with pytest.raises(ValueError): store.history(limit=201)
    with pytest.raises(ValueError): store.metrics('services', hours=99999)
    with pytest.raises(ValueError): store.configure('services', {'interval': 0})


def test_large_inventory_changes_stay_bounded_and_resource_reordering_is_ignored(store):
    before = {'items': [{'name': str(i), 'value': 'a'*300} for i in range(400)]}
    after = {'items': [{'name': str(i), 'value': 'b'*300} for i in range(400)]}
    store.record(result(before))
    store.record(result(after, 1010))
    change = store.changes()[0]['diff']
    assert change['truncated'] and len(change['changed']) == 100
    assert len(json.dumps(change).encode()) < 256*1024
    from inventory_store import diff_values
    assert diff_values({'items': before['items']}, {'items': list(reversed(before['items']))}) == {
        'added': [], 'removed': [], 'changed': [], 'truncated': False}


def test_mariadb_schema_has_explicit_primary_keys_and_indexes():
    sql = '\n'.join(migration_statements(maria=True))
    assert 'AUTO_INCREMENT' in sql and 'MEDIUMTEXT' in sql
    assert 'inventory_snapshots' in sql and 'collector_id, ts' in sql
    assert 'DROP' not in sql and 'ALTER' not in sql
