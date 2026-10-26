"""Tests de Monitor : authentification, contrôles, paramètres, mesures de l'hôte et incidents (base temporaire)."""
import importlib, os, sys, tempfile, time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
HEADERS = {'X-Requested-With': 'Monitor'}


@pytest.fixture(scope='module')
def client():
    os.environ['MONITOR_DATA'] = tempfile.mkdtemp(prefix='monitor-test-')
    os.environ['MONITOR_SECURE_COOKIE'] = '0'
    for name in ('engine', 'app'):
        sys.modules.pop(name, None)
    app = importlib.import_module('app')
    with TestClient(app.app, base_url='http://testserver') as c:
        c.engine, c.module = importlib.import_module('engine'), app
        # Mot de passe écrit au premier démarrage dans le dossier de données.
        creds = (c.engine.DATA / 'admin-credentials.txt').read_text()
        c.password = [line.split(': ', 1)[1] for line in creds.splitlines() if line.startswith('Mot de passe')][0]
        yield c


def login(client):
    r = client.post('/api/login', json={'username': 'admin', 'password': client.password}, headers=HEADERS)
    assert r.status_code == 200, r.text
    return r


def test_api_requires_login_and_public_pages_do_not(client):
    client.cookies.clear()
    assert client.get('/api/live').status_code == 401
    assert client.get('/api/monitors').status_code == 401
    assert client.get('/').status_code == 200
    assert client.get('/healthz').status_code in (200, 503)
    assert client.get('/api/public').status_code == 404  # page de statut désactivée par défaut
    assert 'frame-ancestors' in client.get('/').headers['content-security-policy']


def test_login_rejects_bad_password_and_protects_writes(client):
    client.cookies.clear()
    assert client.post('/api/login', json={'username': 'admin', 'password': 'faux'}, headers=HEADERS).status_code == 401
    assert client.post('/api/login', json={'username': 'root', 'password': client.password}, headers=HEADERS).status_code == 401
    login(client)
    assert client.get('/api/monitors').status_code == 200
    body = {'name': 'x', 'kind': 'tcp', 'target': 'localhost:1'}
    assert client.post('/api/monitors', json=body).status_code == 403  # en-tête de protection manquant
    assert client.post('/api/monitors', json=body, headers={**HEADERS, 'Origin': 'https://evil.example'}).status_code == 403


def test_default_monitors_are_seeded_once(client):
    login(client)
    monitors = client.get('/api/monitors').json()
    names = {m['name'] for m in monitors}
    assert {'Subly (abonnements)', 'Civi (test civique)', 'Clubika', 'Conteneur civi-api'} <= names
    assert len(monitors) == len({m['id'] for m in monitors})


def test_monitor_crud_validation_and_actions(client):
    login(client)
    bad = [{'name': 'a', 'kind': 'http', 'target': 'ftp://x'}, {'name': 'a', 'kind': 'tcp', 'target': 'sans-port'}, {'name': 'a', 'kind': 'inconnu', 'target': 'x'},
           {'name': 'a', 'kind': 'docker', 'target': '--help'}, {'name': 'a', 'kind': 'http', 'target': 'https://u:p@exemple.fr/'}]
    for body in bad:
        assert client.post('/api/monitors', json=body, headers=HEADERS).status_code == 422, body
    created = client.post('/api/monitors', json={'name': 'Local', 'kind': 'tcp', 'target': '127.0.0.1:1', 'interval': 10, 'retries': 0}, headers=HEADERS)
    assert created.status_code == 200
    ident = created.json()['id']
    assert client.put(f'/api/monitors/{ident}', json={'name': 'Local 2', 'kind': 'tcp', 'target': '127.0.0.1:1'}, headers=HEADERS).status_code == 200
    assert client.post(f'/api/monitors/{ident}/action', json={'action': 'pause'}, headers=HEADERS).status_code == 200
    assert client.post(f'/api/monitors/{ident}/action', json={'action': 'inconnue'}, headers=HEADERS).status_code == 400
    assert client.get(f'/api/monitors/{ident}/history').status_code == 200
    assert client.delete(f'/api/monitors/{ident}', headers=HEADERS).status_code == 200
    assert client.get(f'/api/monitors/{ident}/history').status_code == 404


def test_checks_detect_up_and_down(client):
    e = client.engine
    import socket
    server = socket.socket(); server.bind(('127.0.0.1', 0)); server.listen(5)
    port = server.getsockname()[1]
    base = {'id': 0, 'timeout': 2, 'keyword': '', 'expected': '200-299', 'interval': 60}
    ok, latency, message, _ = e.check({**base, 'kind': 'tcp', 'target': f'127.0.0.1:{port}'})
    assert ok and latency >= 0
    server.close()  # plus personne n'écoute sur ce port
    ok, _, message, _ = e.check({**base, 'kind': 'tcp', 'target': f'127.0.0.1:{port}'})
    assert not ok and message
    ok, _, message, _ = e.check({**base, 'kind': 'inconnu', 'target': 'x'})
    assert not ok and 'inconnu' in message


def test_settings_mask_secrets_and_require_https_webhook(client):
    login(client)
    settings = client.get('/api/settings').json()
    assert 'password_hash' not in settings
    body = {**settings, 'telegram_token': 'secret-token', 'telegram_chat': '1'}
    assert client.put('/api/settings', json=body, headers=HEADERS).status_code == 200
    assert client.get('/api/settings').json()['telegram_token'] == '••••••••'
    assert client.put('/api/settings', json={**body, 'telegram_token': '••••••••'}, headers=HEADERS).status_code == 200
    assert client.engine.settings()['telegram_token'] == 'secret-token'  # le masque ne l'écrase pas
    assert client.put('/api/settings', json={**body, 'webhook_url': 'http://insecure.example'}, headers=HEADERS).status_code == 422
    assert 'secret-token' not in client.get('/api/export').text


def test_public_status_page_only_lists_public_monitors(client):
    login(client)
    settings = client.get('/api/settings').json()
    client.put('/api/settings', json={**settings, 'public_enabled': True, 'telegram_token': '••••••••'}, headers=HEADERS)
    client.cookies.clear()
    public = client.get('/api/public').json()
    assert public and all('target' not in m and 'push_token' not in m for m in public)
    assert 'phpMyAdmin' not in {m['name'] for m in public}  # contrôle non public


def test_host_collector_publishes_real_measures(client):
    deadline = time.time() + 10
    while time.time() < deadline and not client.engine.LIVE.get('ts'):
        time.sleep(0.2)
    live = dict(client.engine.LIVE)
    assert 0 <= live['cpu'] <= 100 and 0 < live['ram'] <= 100
    assert live['memory']['total'] > 0 and live['cores'] and live['disks'] and live['hostname']
    login(client)
    assert client.get('/api/history?hours=1').status_code == 200
    assert client.get('/api/history?hours=3').status_code == 400


def test_incident_opens_once_and_closes(client):
    e = client.engine
    e.incident('test:1', True, 'Panne test', 'détail')
    e.incident('test:1', True, 'Panne test', 'détail')
    assert len(e.rows("SELECT * FROM incidents WHERE source='test:1' AND closed IS NULL")) == 1
    e.incident('test:1', False, 'Panne test', 'rétabli')
    assert e.rows("SELECT * FROM incidents WHERE source='test:1' AND closed IS NULL") == []
    assert len(e.rows("SELECT * FROM incidents WHERE source='test:1'")) == 1


def test_password_change_invalidates_sessions(client):
    login(client)
    assert client.post('/api/password', json={'current': 'faux', 'new': 'un-nouveau-mot-de-passe'}, headers=HEADERS).status_code == 400
    assert client.post('/api/password', json={'current': client.password, 'new': 'court'}, headers=HEADERS).status_code == 422
    assert client.post('/api/password', json={'current': client.password, 'new': 'un-nouveau-mot-de-passe'}, headers=HEADERS).status_code == 200
    assert client.get('/api/monitors').status_code == 401
    client.password = 'un-nouveau-mot-de-passe'
    login(client)
