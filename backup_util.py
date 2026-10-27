"""Scheduled portable SQLite/MariaDB backups. Run as the monitor system user."""
import contextlib
import gzip
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, unquote, parse_qs

DATA=Path(os.environ.get('MONITOR_DATA','./data'))
BACKUPS=DATA/'backups'
DB_URL=os.environ.get('MONITOR_DB','')
MARIA=DB_URL.startswith(('mysql://','mariadb://'))
RETENTION_DAYS=int(os.environ.get('MONITOR_BACKUP_RETENTION_DAYS','14'))

@contextlib.contextmanager
def mysql_config():
    """Never pass credentials in process arguments or print them."""
    url=urlsplit(DB_URL)
    query=parse_qs(url.query)
    options={'user':unquote(url.username or ''),'password':unquote(url.password or ''),
             'host':url.hostname or 'localhost','port':str(url.port or 3306)}
    if query.get('socket'): options['socket']=query['socket'][0]
    fd,path=tempfile.mkstemp(prefix='monitor-db-',suffix='.cnf')
    os.fchmod(fd,0o600)
    def quote(value): return '"'+str(value).replace('\\','\\\\').replace('"','\\"')+'"'
    try:
        with os.fdopen(fd,'w') as f:
            f.write('[client]\n'+''.join(k+'='+quote(v)+'\n' for k,v in options.items()))
        yield path
    finally:
        Path(path).unlink(missing_ok=True)

def backup():
    BACKUPS.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(BACKUPS,0o700)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    final=BACKUPS/('monitor-'+stamp+('.sql.gz' if MARIA else '.sqlite'))
    tmp=BACKUPS/(final.name+'.tmp')
    try:
        if MARIA:
            executable=shutil.which('mariadb-dump') or shutil.which('mysqldump')
            if not executable: raise RuntimeError('mariadb-dump or mysqldump is required')
            database=urlsplit(DB_URL).path.lstrip('/')
            with mysql_config() as conf, tmp.open('xb') as raw, gzip.GzipFile(fileobj=raw,mode='wb') as output:
                proc=subprocess.Popen([executable,'--defaults-extra-file='+conf,'--single-transaction','--quick',
                                        database],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                try:
                    shutil.copyfileobj(proc.stdout,output)
                    stderr=proc.stderr.read()
                    if proc.wait()!=0: raise RuntimeError('MariaDB backup failed (database client exited nonzero)')
                finally:
                    if proc.poll() is None: proc.kill(); proc.wait()
        else:
            source=sqlite3.connect(DATA/'monitor.db')
            destination=sqlite3.connect(tmp)
            try: source.backup(destination)
            finally: source.close(); destination.close()
        if tmp.stat().st_size<100: raise RuntimeError('Backup is unexpectedly empty')
        os.chmod(tmp,0o600)
        os.replace(tmp,final)
        for entry in BACKUPS.glob('monitor-*'):
            if entry.is_file() and entry.suffix in ('.sqlite','.gz') and time.time()-entry.stat().st_mtime>RETENTION_DAYS*86400:
                entry.unlink()
        print(str(final))
        return final
    finally:
        tmp.unlink(missing_ok=True)

def restore(path,confirm=False):
    if not confirm: raise ValueError('Stop Monitor and explicitly confirm an offline restore')
    path=Path(path)
    if MARIA:
        if not path.name.endswith('.sql.gz'): raise ValueError('Expected .sql.gz MariaDB backup')
        executable=shutil.which('mariadb') or shutil.which('mysql')
        if not executable: raise RuntimeError('mariadb or mysql command required for restore')
        with mysql_config() as conf, gzip.open(path,'rb') as source:
            proc=subprocess.Popen([executable,'--defaults-extra-file='+conf,urlsplit(DB_URL).path.lstrip('/')],
                                  stdin=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                shutil.copyfileobj(source,proc.stdin)
                proc.stdin.close()
                error=proc.stderr.read()
                if proc.wait()!=0: raise RuntimeError('MariaDB restore failed')
            finally:
                if proc.poll() is None: proc.kill(); proc.wait()
    else:
        if path.suffix!='.sqlite': raise ValueError('Expected SQLite backup')
        with sqlite3.connect(path) as test:
            if test.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Corrupted SQLite backup')
        if (DATA/'monitor.db').exists(): raise FileExistsError('Move the current monitor.db aside before restoring')
        shutil.copy2(path,DATA/'monitor.db')

if __name__=='__main__':
    if len(sys.argv)==1: backup()
    elif len(sys.argv)==4 and sys.argv[1]=='restore' and sys.argv[3]=='--confirm': restore(sys.argv[2],True)
    else: raise SystemExit('Usage: backup_util.py [restore FILE --confirm]')
