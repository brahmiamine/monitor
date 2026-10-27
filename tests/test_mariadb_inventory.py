"""Optional real-engine check against an explicitly supplied isolated test socket.

Never uses MONITOR_DB, discovered credentials, or an existing database. A fresh
random database is created and removed on the supplied test instance only.
"""
import contextlib
import os
from pathlib import Path
import secrets
import sys
import time

import pytest
import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from inventory_store import InventoryStore


@pytest.fixture()
def maria_store():
    socket = os.environ.get('MONITOR_MARIADB_TEST_SOCKET')
    if not socket:
        pytest.skip('Isolated MariaDB test socket not supplied')
    # Standard production paths are explicitly refused, even if accidentally set.
    if Path(socket).resolve().parent in (Path('/run/mysqld'), Path('/var/run/mysqld')):
        pytest.fail('Production socket paths cannot be used by this test')
    options = dict(unix_socket=socket, user=os.environ.get('MONITOR_MARIADB_TEST_USER', 'root'),
                   charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
    name = 'monitor_test_' + secrets.token_hex(8)
    admin = pymysql.connect(**options)
    with admin.cursor() as c:
        c.execute(f'CREATE DATABASE `{name}` CHARACTER SET utf8mb4')

    class Adapter:
        def __init__(self, conn): self.conn = conn
        def execute(self, sql, args=()):
            c = self.conn.cursor()
            c.execute(sql.replace('?', '%s').replace('INSERT OR IGNORE','INSERT IGNORE').replace('INSERT OR REPLACE','REPLACE'), args)
            return c

    @contextlib.contextmanager
    def db():
        conn = pymysql.connect(database=name, **options)
        try:
            yield Adapter(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally: conn.close()

    try:
        yield InventoryStore(db, maria=True, max_snapshots=3, max_metric_rows=3)
    finally:
        with admin.cursor() as c: c.execute(f'DROP DATABASE `{name}`')
        admin.close()


def test_real_mariadb_migration_record_prune_and_rollback(maria_store):
    s = maria_store
    with s.db() as c:
        c.execute('CREATE TABLE legacy_samples (id INT PRIMARY KEY, value INT)')
        c.execute('INSERT INTO legacy_samples VALUES (1,7)')
    s.migrate(); s.migrate()
    now = time.time()
    for i in range(5):
        s.record({'id': 'pressure', 'status': 'available', 'last_attempt': now + i,
                  'duration_ms': 1, 'data': {'version': i}}, metrics={'cpu.some.avg10': i})
    s.prune(now=now+10)
    history = s.history('pressure')
    assert len(history) == 3
    assert s.compare(history[-1]['id'], history[0]['id'])['changed']
    assert len(s.metrics('pressure', now=now+10)) > 0
    s.record({'id': 'pressure', 'status': 'error', 'last_attempt': now+10,
              'duration_ms': 1, 'data': {}})
    assert s.states()[0]['data'] == {'version': 4}
    assert s.changes('pressure')
    s.rollback(); s.rollback()
    with s.db() as c:
        assert c.execute('SELECT value FROM legacy_samples').fetchone()['value'] == 7
    s.migrate()
    assert s.history() == []


def test_sqlite_history_transfer_to_real_mariadb_preserves_references(maria_store, tmp_path, monkeypatch):
    import importlib.util
    import sqlite3
    source=tmp_path/'source.sqlite'
    @contextlib.contextmanager
    def source_db():
        c=sqlite3.connect(source);c.row_factory=sqlite3.Row
        try: yield c;c.commit()
        finally:c.close()
    initial=InventoryStore(source_db)
    initial.migrate()
    now=time.time()
    first=initial.record({'id':'resources','status':'available','last_attempt':now-1,'duration_ms':1,'data':{'version':1}})
    second=initial.record({'id':'resources','status':'available','last_attempt':now,'duration_ms':1,'data':{'version':2}})
    monkeypatch.setenv('MONITOR_DATA',str(tmp_path/'engine-data'))
    monkeypatch.setenv('MONITOR_DB','')
    path=Path(__file__).resolve().parent.parent/'engine.py'
    spec=importlib.util.spec_from_file_location('isolated_transfer_engine',path)
    engine=importlib.util.module_from_spec(spec);spec.loader.exec_module(engine)
    engine.MARIA=True
    monkeypatch.setattr(engine,'db',maria_store.db)
    engine.init()
    assert engine.migrate_from_sqlite(str(source))
    assert len(maria_store.history('resources'))==2
    changed=maria_store.changes('resources')[0]
    assert changed['before_id']==first and changed['after_id']==second
    assert maria_store.compare(first,second)['changed']
