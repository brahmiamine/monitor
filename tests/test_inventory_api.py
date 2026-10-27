"""Inventory integration in an isolated DB; never start production probes."""
import hashlib
import importlib
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def inventory_client(tmp_path, monkeypatch):
    monkeypatch.setenv('MONITOR_DATA', str(tmp_path))
    monkeypatch.delenv('MONITOR_DB', raising=False)
    for name in ('engine', 'app'):
        monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module('app')
    e = module.e
    module._original_seed_monitors = module.seed_monitors
    monkeypatch.setattr(e, 'start', lambda: (e.init() or []))
    monkeypatch.setattr(module, 'seed_monitors', lambda: None)
    with TestClient(module.app) as client:
        token = 'isolated-inventory-test-session'
        with e.db() as c:
            c.execute('INSERT INTO sessions VALUES (?,?)',
                      (hashlib.sha256(token.encode()).hexdigest(), time.time() + 60))
        client.cookies.set('monitor_session', token)
        yield client, e


def test_inventory_routes_private_and_do_not_run_collectors(inventory_client, monkeypatch):
    client, e = inventory_client
    for route in ('/api/inventory', '/api/inventory/history', '/api/inventory/changes',
                  '/api/inventory/metrics?collector=psi', '/api/inventory/export'):
        response = client.get(route)
        assert response.status_code == 200, response.text
    client.cookies.clear()
    for route in ('/api/inventory', '/api/inventory/history', '/api/inventory/export'):
        assert client.get(route).status_code == 401


def test_inventory_history_diff_and_export(inventory_client):
    client, e = inventory_client
    store = e.INVENTORY_STORE
    now = time.time()
    base = {'id': 'test_resource', 'status': 'available', 'last_attempt': now - 10,
            'last_success': now - 10, 'duration_ms': 0.5, 'reason': None}
    store.record({**base, 'data': {'items': [{'name': 'alpha', 'version': '1'}]}})
    store.record({**base, 'last_attempt': now, 'last_success': now,
                  'data': {'items': [{'name': 'alpha', 'version': '2'}]}})
    history = client.get('/api/inventory/history?collector=test_resource').json()
    assert len(history) == 2 and all(r['collector_id'] == 'test_resource' for r in history)
    compared = client.get('/api/inventory/compare', params={
        'before': history[-1]['id'], 'after': history[0]['id']})
    assert compared.status_code == 200 and compared.json()['changed']
    assert client.get('/api/inventory/changes?collector=test_resource').json()
    exported = client.get('/api/inventory/export?format=csv')
    assert exported.status_code == 200 and 'text/csv' in exported.headers['content-type']
    assert client.get('/api/inventory/export?format=exe').status_code == 422
    assert client.get('/api/inventory/history?limit=10000').status_code == 422
    assert client.get('/api/inventory/compare?before=1&after=999999').status_code == 404


def test_docker_inspect_does_not_request_arbitrary_labels(inventory_client, monkeypatch):
    _, e = inventory_client
    assert '.Config.Labels}}' not in e.INSPECT
    assert '.Config.Env' not in e.INSPECT and '.Args' not in e.INSPECT
    assert 'com.docker.compose.project' in e.INSPECT


def test_first_start_does_not_invent_ssh_monitor(inventory_client, monkeypatch):
    client, e = inventory_client
    monkeypatch.setattr(e, 'listening_ports', lambda: [])
    monkeypatch.setattr(e, 'run', lambda *a, **k: '')
    module = sys.modules['app']
    module._original_seed_monitors()
    assert e.rows('SELECT * FROM monitors') == []


def test_partial_pressure_and_volatile_metrics_persist_without_change_noise(inventory_client):
    _, e = inventory_client
    now = time.time()
    base = {'status': 'available', 'last_attempt': now, 'last_success': now,
            'duration_ms': 1, 'reason': None}
    e.inventory_result({**base, 'id': 'pressure', 'data': {'resources': {
        'cpu': {'some': {'avg10': 2.5}}, 'memory': None, 'io': None}}})
    assert e.INVENTORY_STORE.metrics('pressure')
    row = {**base, 'id': 'containers', 'data': {'items': [
        {'name': 'test', 'state': 'running', 'cpu': '1%', 'memory': '1 MiB',
         'network': '1 KiB', 'io': '0 B', 'restarts': 0}]}}
    e.inventory_result(row)
    row['data']['items'][0].update(cpu='9%', memory='2 MiB', network='4 KiB')
    e.inventory_result({**row, 'last_attempt': now+1})
    assert len(e.INVENTORY_STORE.history('containers')) == 1
    assert e.INVENTORY_STORE.changes('containers') == []


def test_ssh_seed_tries_portable_unit_name_independently(inventory_client, monkeypatch):
    _, e = inventory_client
    monkeypatch.setattr(e,'listening_ports',lambda:[{'port':22,'proto':'tcp','address':'127.0.0.1'}])
    def run(args,*rest):
        if args==['systemctl','is-active','ssh.service']: raise RuntimeError('unit absent')
        if args==['systemctl','is-active','sshd.service']: return 'active\n'
        return ''
    monkeypatch.setattr(e,'run',run)
    sys.modules['app']._original_seed_monitors()
    assert [m['name'] for m in e.rows('SELECT name FROM monitors')]==['SSH']


def test_backend_transfer_preserves_new_inventory_history(inventory_client, tmp_path, monkeypatch):
    import contextlib
    import sqlite3
    _, e = inventory_client
    source = tmp_path/'source.sqlite'
    with sqlite3.connect(source) as c:
        c.execute('CREATE TABLE inventory_state (collector_id TEXT PRIMARY KEY,status TEXT,reason TEXT,last_attempt REAL,last_success REAL,duration_ms REAL,data TEXT)')
        c.execute('INSERT INTO inventory_state VALUES (?,?,?,?,?,?,?)',('pressure','available','',1,1,1,'{"cpu": 2}'))
    original = e.connect
    @contextlib.contextmanager
    def target():
        c = sqlite3.connect(e.DB); c.row_factory=sqlite3.Row
        class Conn:
            def execute(self,sql,args=()):
                return c.execute(sql.replace('INSERT IGNORE','INSERT OR IGNORE'),args)
        try:
            yield Conn(); c.commit()
        finally: c.close()
    monkeypatch.setattr(e,'MARIA',True)
    monkeypatch.setattr(e,'db',target)
    monkeypatch.setattr(e,'rows',lambda *a,**k:[])
    monkeypatch.setattr(e,'event',lambda *a,**k:None)
    assert e.migrate_from_sqlite(str(source))
    with sqlite3.connect(e.DB) as c:
        row = c.execute("SELECT data FROM inventory_state WHERE collector_id='pressure'").fetchone()
    assert row and json.loads(row[0])=={'cpu':2}


def test_missing_docker_details_do_not_invent_zero_restarts_or_no_oom(inventory_client, monkeypatch):
    _, e = inventory_client
    def run(args,*rest):
        if args[:2]==['docker','ps']:
            return json.dumps({'ID':'abc123','State':'running','Names':'test','Image':'app:latest','Status':'Up'})
        if args[:2]==['docker','stats']: return ''
        raise PermissionError('inspect denied')
    monkeypatch.setattr(e,'run',run)
    data=e.collect_docker()[0]
    assert data['restarts'] is None and data['oom'] is None
    assert data['details_status']=='unavailable'


def test_transfer_completes_before_background_threads_start(inventory_client, monkeypatch):
    client,e = inventory_client
    calls=[]
    module=sys.modules['app']
    monkeypatch.setattr(e,'init',lambda:calls.append('init'))
    monkeypatch.setattr(e,'migrate_from_sqlite',lambda path:calls.append('transfer'))
    monkeypatch.setattr(module,'bootstrap',lambda:calls.append('bootstrap'))
    monkeypatch.setattr(module,'seed_monitors',lambda:calls.append('seed'))
    monkeypatch.setattr(e,'start',lambda:(calls.append('start') or []))
    with TestClient(module.app): pass
    assert calls.index('transfer') < calls.index('start')
