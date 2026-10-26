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
    if os.environ.get('MONITOR_TEST_DB'):
        # Base MariaDB de test : repartir de tables vides.
        os.environ['MONITOR_DB'] = os.environ['MONITOR_TEST_DB']
        import pymysql
        from urllib.parse import urlsplit, unquote
        u = urlsplit(os.environ['MONITOR_DB']); q = dict(x.split('=', 1) for x in u.query.split('&') if '=' in x)
        conn = pymysql.connect(user=unquote(u.username), password=unquote(u.password or ''), database=u.path.lstrip('/'), unix_socket=unquote(q.get('socket', '')) or None, host=u.hostname or 'localhost')
        with conn.cursor() as cur:
            cur.execute('SET FOREIGN_KEY_CHECKS=0'); cur.execute('SHOW TABLES')
            for (t,) in cur.fetchall(): cur.execute(f'DROP TABLE `{t}`')
        conn.commit(); conn.close()
    else:
        os.environ.pop('MONITOR_DB', None)
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
    assert 'SSH' in names  # surveillance de départ générique : aucun nom propre à un serveur
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
    created = client.post('/api/monitors', json={'name': 'Site public', 'kind': 'tcp', 'target': '127.0.0.1:1', 'public': True}, headers=HEADERS)
    assert created.status_code == 200
    client.cookies.clear()
    public = client.get('/api/public').json()
    assert [m['name'] for m in public] == ['Site public']  # SSH et les autres contrôles ne sont pas publics
    assert all('target' not in m and 'push_token' not in m for m in public)


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


def test_projects_crud_validation_and_links(client):
    login(client)
    for bad in ({'name': 'x', 'url': 'javascript:alert(1)'}, {'name': 'x', 'repo_url': 'ftp://exemple.fr/r'}, {'name': 'x', 'url': 'https://u:p@exemple.fr'}, {'name': 'x', 'container': 'a b'}, {'name': ''}):
        assert client.post('/api/projects', json=bad, headers=HEADERS).status_code == 422, bad
    created = client.post('/api/projects', json={'name': 'Mon app', 'url': 'https://app.exemple.fr', 'repo_url': 'https://github.com/moi/app', 'container': 'app-api', 'repo_dir': 'app'}, headers=HEADERS)
    assert created.status_code == 200
    ident = created.json()['id']
    # Un contrôle sur le même nom d'hôte est rattaché au projet.
    client.post('/api/monitors', json={'name': 'App HTTP', 'kind': 'http', 'target': 'https://app.exemple.fr/health'}, headers=HEADERS)
    project = [p for p in client.get('/api/projects').json() if p['id'] == ident][0]
    assert project['repo_url'] == 'https://github.com/moi/app' and [m['name'] for m in project['monitors']] == ['App HTTP']
    assert client.put(f'/api/projects/{ident}', json={'name': 'Mon app 2', 'url': 'https://app.exemple.fr'}, headers=HEADERS).status_code == 200
    assert client.get('/api/discover').status_code == 200
    assert client.delete(f'/api/projects/{ident}', headers=HEADERS).status_code == 200
    assert client.delete(f'/api/projects/{ident}', headers=HEADERS).status_code == 404


def test_config_is_public_and_generic(client):
    client.cookies.clear()
    config = client.get('/api/config').json()
    assert config['database'] in ('SQLite', 'MariaDB') and config['name']
    assert 'clubika' not in str(config).lower()


def test_system_info_is_collected(client):
    deadline = time.time() + 20
    while time.time() < deadline and not client.engine.CACHE.get('system'):
        time.sleep(0.3)
    info = client.engine.CACHE['system']
    assert info['os'] and info['kernel'] and info['cpu']['logical'] and info['memory']['MemTotal'] > 0
    assert isinstance(info['interfaces'], list) and isinstance(info['listening'], list) and info['processes'] > 0
    assert 'Environment' not in str(info) and 'password' not in str(info).lower()


def test_sql_dialect_translation(client):
    translate = client.engine.Conn.translate
    assert translate('INSERT OR REPLACE INTO settings VALUES (?,?)') == 'REPLACE INTO settings VALUES (%s,%s)'
    assert translate('INSERT OR IGNORE INTO t VALUES (?)') == 'INSERT IGNORE INTO t VALUES (%s)'


def test_dpkg_parser_marks_manual_and_upgradable(client):
    out = 'bash\t5.2-1\t1500\tinstall ok installed\nlibfoo:amd64\t1.0\t20\tinstall ok installed\nremoved\t1\t5\tdeinstall ok config-files\nvim\t9.0\t3000\tinstall ok installed\n'
    packages = client.engine.parse_dpkg(out, manual={'bash', 'vim'}, upgradable={'vim'})
    assert [p['name'] for p in packages] == ['bash', 'libfoo:amd64', 'vim']  # le paquet désinstallé est ignoré
    by = {p['name']: p for p in packages}
    assert by['bash']['manual'] and not by['libfoo:amd64']['manual'] and by['vim']['upgradable'] and not by['bash']['upgradable']
    assert by['bash']['size_kb'] == 1500


def test_software_and_ports_endpoints(client):
    client.cookies.clear()
    assert client.get('/api/software').status_code == 401
    login(client)
    data = client.get('/api/software').json()
    assert {'packages', 'runtimes', 'docker_images', 'docker_volumes', 'updates'} <= set(data)
    ports = client.engine.listening_ports()
    assert isinstance(ports, list)
    for p in ports:
        assert p['exposure'] in ('local', 'public') and 0 < p['port'] < 65536 and p['proto'] in ('tcp', 'tcp6', 'udp', 'udp6')


def test_system_info_has_storage_and_network_details(client):
    deadline = time.time() + 20
    while time.time() < deadline and not client.engine.CACHE.get('system', {}).get('filesystems'):
        time.sleep(0.3)
    info = client.engine.CACHE['system']
    assert info['filesystems'] and all({'mount', 'fstype', 'total', 'percent'} <= set(f) for f in info['filesystems'])
    assert isinstance(info['disk_io'], list) and isinstance(info['block_devices'], list)
