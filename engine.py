"""Host collectors, bounded check scheduler and persistent incident state."""
import queue, concurrent.futures, contextlib, json, logging, os, platform, re, socket, sqlite3, ssl, subprocess, threading, time
from pathlib import Path
from urllib.parse import urlsplit
import httpx
import psutil

log = logging.getLogger('monitor')
DATA = Path(os.environ.get('MONITOR_DATA', './data'))
DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
# Base de données : SQLite par défaut (aucune configuration), MariaDB/MySQL si MONITOR_DB est défini :
#   mysql://utilisateur:motdepasse@hôte:3306/base   ou   mysql://utilisateur:motdepasse@localhost/base?socket=/run/mysqld/mysqld.sock
DB_URL = os.environ.get('MONITOR_DB', '').strip()
MARIA = DB_URL.startswith(('mysql://', 'mariadb://'))
DB = DATA / 'monitor.db'
if MARIA:
    import pymysql, pymysql.cursors
    DBError = (pymysql.err.OperationalError, pymysql.err.InterfaceError)
else:
    DBError = (sqlite3.OperationalError,)
STOP = threading.Event()
LOCK = threading.Lock()
LIVE = {}
CACHE = {'containers': [], 'services': [], 'system': {}, 'errors': {}}
HOST_ROOT = os.environ.get('HOST_ROOT', '')  # racine de l'hôte montée en lecture seule (conteneur) ; vide = exécution directe
CGROUP_SERVICES = Path(os.environ.get('CGROUP_SERVICES', '/sys/fs/cgroup/system.slice'))
REAL_FS = {'ext2','ext3','ext4','xfs','btrfs','zfs','vfat','f2fs','ntfs','exfat'}
ORIGIN = os.environ.get('MONITOR_ORIGIN', '')
SITE_NAME = os.environ.get('MONITOR_NAME', '')
DEFAULTS = {'cpu_limit': 90, 'ram_limit': 90, 'disk_limit': 90, 'sustain_seconds': 60,
 'retention_days': 30, 'webhook_url': '', 'telegram_token': '', 'telegram_chat': '',
 'smtp_host': '', 'smtp_port': 587, 'smtp_user': '', 'smtp_password': '', 'smtp_from': '', 'smtp_to': '',
 'public_enabled': False, 'maintenance_until': 0}

class Conn:
    """Connexion uniforme : écrit en SQL SQLite (placeholders ?), traduit pour MariaDB."""
    def __init__(self, raw): self.raw = raw
    @staticmethod
    def translate(sql):
        return sql.replace('?', '%s').replace('INSERT OR REPLACE', 'REPLACE').replace('INSERT OR IGNORE', 'INSERT IGNORE')
    def execute(self, sql, args=()):
        if not MARIA: return self.raw.execute(sql, args)
        cur = self.raw.cursor(); cur.execute(self.translate(sql), args); return cur
    def __getattr__(self, name): return getattr(self.raw, name)

def connect():
    if not MARIA:
        c = sqlite3.connect(DB, timeout=15); c.row_factory = sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return Conn(c)
    u = urlsplit(DB_URL); q = dict(x.split('=', 1) for x in u.query.split('&') if '=' in x)
    from urllib.parse import unquote
    return Conn(pymysql.connect(user=unquote(u.username or ''), password=unquote(u.password or ''), database=u.path.lstrip('/'),
        host=u.hostname or 'localhost', port=u.port or 3306, unix_socket=unquote(q.get('socket', '')) or None, charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor, autocommit=False, connect_timeout=10))

@contextlib.contextmanager
def db():
    c = connect()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def rows(sql, args=()):
    with db() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]

SQLITE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings(`key` TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS monitors(id INTEGER PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,target TEXT NOT NULL,
  `interval` INTEGER NOT NULL DEFAULT 60,timeout INTEGER NOT NULL DEFAULT 10,retries INTEGER NOT NULL DEFAULT 2,
  keyword TEXT DEFAULT '',expected TEXT DEFAULT '200-299',group_name TEXT DEFAULT 'Applications',public INTEGER DEFAULT 0,
  paused INTEGER DEFAULT 0,maintenance_until REAL DEFAULT 0,push_token TEXT,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS checks(id INTEGER PRIMARY KEY,monitor_id INTEGER REFERENCES monitors(id) ON DELETE CASCADE,
  ts REAL NOT NULL,ok INTEGER NOT NULL,latency REAL,message TEXT,tls_days INTEGER);
CREATE INDEX IF NOT EXISTS checks_monitor_ts ON checks(monitor_id,ts);
CREATE TABLE IF NOT EXISTS samples(ts REAL PRIMARY KEY,cpu REAL,ram REAL,disk REAL,rx REAL,tx REAL,`read` REAL,`write` REAL,`load` REAL);
CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY,source TEXT NOT NULL,title TEXT NOT NULL,message TEXT,
  opened REAL NOT NULL,closed REAL,acknowledged INTEGER DEFAULT 0,is_open INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS incident_open_key ON incidents(source,is_open);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,ts REAL NOT NULL,kind TEXT NOT NULL,message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS heartbeats(monitor_id INTEGER PRIMARY KEY REFERENCES monitors(id) ON DELETE CASCADE,ts REAL,ok INTEGER,message TEXT);
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY,name TEXT NOT NULL,url TEXT DEFAULT '',repo_url TEXT DEFAULT '',
  container TEXT DEFAULT '',repo_dir TEXT DEFAULT '',description TEXT DEFAULT '',created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS notification_jobs(id INTEGER PRIMARY KEY,channel TEXT NOT NULL,title TEXT NOT NULL,message TEXT NOT NULL,
  created REAL NOT NULL,due REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'pending',last_error TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS notification_due ON notification_jobs(status,due);
CREATE TABLE IF NOT EXISTS project_monitors(project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,PRIMARY KEY(project_id,monitor_id));
'''
MARIA_SCHEMA = [
 "CREATE TABLE IF NOT EXISTS settings(`key` VARCHAR(64) PRIMARY KEY,value MEDIUMTEXT NOT NULL)",
 "CREATE TABLE IF NOT EXISTS monitors(id INT AUTO_INCREMENT PRIMARY KEY,name VARCHAR(100) NOT NULL,kind VARCHAR(20) NOT NULL,target VARCHAR(1000) NOT NULL,"
 "`interval` INT NOT NULL DEFAULT 60,timeout INT NOT NULL DEFAULT 10,retries INT NOT NULL DEFAULT 2,keyword VARCHAR(500) DEFAULT '',expected VARCHAR(100) DEFAULT '200-299',"
 "group_name VARCHAR(60) DEFAULT 'Applications',public TINYINT DEFAULT 0,paused TINYINT DEFAULT 0,maintenance_until DOUBLE DEFAULT 0,push_token VARCHAR(64),created DOUBLE NOT NULL)",
 "CREATE TABLE IF NOT EXISTS checks(id BIGINT AUTO_INCREMENT PRIMARY KEY,monitor_id INT NOT NULL,ts DOUBLE NOT NULL,ok TINYINT NOT NULL,latency DOUBLE,message TEXT,tls_days INT,"
 "KEY checks_monitor_ts(monitor_id,ts),CONSTRAINT checks_monitor_fk FOREIGN KEY(monitor_id) REFERENCES monitors(id) ON DELETE CASCADE)",
 "CREATE TABLE IF NOT EXISTS samples(ts DOUBLE PRIMARY KEY,cpu DOUBLE,ram DOUBLE,disk DOUBLE,rx DOUBLE,tx DOUBLE,`read` DOUBLE,`write` DOUBLE,`load` DOUBLE)",
 "CREATE TABLE IF NOT EXISTS incidents(id INT AUTO_INCREMENT PRIMARY KEY,source VARCHAR(190) NOT NULL,title VARCHAR(255) NOT NULL,message TEXT,opened DOUBLE NOT NULL,"
 "closed DOUBLE NULL,acknowledged TINYINT DEFAULT 0,is_open TINYINT NULL,UNIQUE KEY incident_open_key(source,is_open))",
 "CREATE TABLE IF NOT EXISTS events(id INT AUTO_INCREMENT PRIMARY KEY,ts DOUBLE NOT NULL,kind VARCHAR(40) NOT NULL,message TEXT NOT NULL,KEY events_ts(ts))",
 "CREATE TABLE IF NOT EXISTS sessions(token VARCHAR(64) PRIMARY KEY,expires DOUBLE NOT NULL)",
 "CREATE TABLE IF NOT EXISTS heartbeats(monitor_id INT PRIMARY KEY,ts DOUBLE,ok TINYINT,message TEXT,CONSTRAINT heartbeats_monitor_fk FOREIGN KEY(monitor_id) REFERENCES monitors(id) ON DELETE CASCADE)",
 "CREATE TABLE IF NOT EXISTS projects(id INT AUTO_INCREMENT PRIMARY KEY,name VARCHAR(100) NOT NULL,url VARCHAR(500) DEFAULT '',repo_url VARCHAR(500) DEFAULT '',"
 "container VARCHAR(100) DEFAULT '',repo_dir VARCHAR(255) DEFAULT '',description VARCHAR(500) DEFAULT '',created DOUBLE NOT NULL)",
 "CREATE TABLE IF NOT EXISTS notification_jobs(id BIGINT AUTO_INCREMENT PRIMARY KEY,channel VARCHAR(24) NOT NULL,title VARCHAR(255) NOT NULL,"
 "message TEXT NOT NULL,created DOUBLE NOT NULL,due DOUBLE NOT NULL,attempts INT NOT NULL DEFAULT 0,status VARCHAR(16) NOT NULL DEFAULT 'pending',"
 "last_error VARCHAR(120) NOT NULL DEFAULT '',KEY notification_due(status,due))",
 "CREATE TABLE IF NOT EXISTS project_monitors(project_id INT NOT NULL,monitor_id INT NOT NULL,PRIMARY KEY(project_id,monitor_id),"
 "CONSTRAINT project_monitor_p_fk FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,"
 "CONSTRAINT project_monitor_m_fk FOREIGN KEY(monitor_id) REFERENCES monitors(id) ON DELETE CASCADE)",
]

def init():
    with db() as c:
        if MARIA:
            for statement in MARIA_SCHEMA: c.execute(statement)
        else:
            c.execute('PRAGMA journal_mode=WAL'); c.executescript(SQLITE_SCHEMA)
            # Bases créées avec la première version : colonne is_open et clé d'unicité des incidents ouverts.
            if 'is_open' not in {r['name'] for r in c.execute('PRAGMA table_info(incidents)').fetchall()}:
                c.execute('ALTER TABLE incidents ADD COLUMN is_open INTEGER'); c.execute('UPDATE incidents SET is_open=1 WHERE closed IS NULL')
                c.execute('CREATE UNIQUE INDEX IF NOT EXISTS incident_open_key ON incidents(source,is_open)')
        for k,v in DEFAULTS.items():
            c.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (k,json.dumps(v)))
    if not MARIA: os.chmod(DB, 0o600)

def migrate_from_sqlite(path):
    """Une fois, au passage de SQLite à MariaDB : reprend mot de passe, réglages, contrôles, historique et incidents."""
    if not MARIA or not path or not Path(path).exists(): return False
    if rows("SELECT `key` FROM settings WHERE `key`='password_hash'"): return False
    src=sqlite3.connect(f'file:{path}?mode=ro',uri=True); src.row_factory=sqlite3.Row
    with db() as c:
        for table in ('settings','monitors','checks','samples','incidents','events','projects','heartbeats'):
            try: data=src.execute(f'SELECT * FROM {table}').fetchall()
            except sqlite3.Error: continue
            for r in data:
                row=dict(zip(r.keys(),tuple(r)))
                if table=='incidents' and 'is_open' not in row: row['is_open']=1 if row['closed'] is None else None
                verb='REPLACE' if table=='settings' else 'INSERT IGNORE'
                c.execute(f'{verb} INTO {table}('+','.join('`'+k+'`' for k in row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
    src.close(); event('config','Données reprises depuis SQLite'); return True

def settings():
    return {r['key']:json.loads(r['value']) for r in rows('SELECT * FROM settings')}

def event(kind, message):
    with db() as c:
        c.execute('INSERT INTO events(ts,kind,message) VALUES (?,?,?)',(time.time(),kind,message[:1000]))

def run(args, timeout=8):
    p = subprocess.run(args, text=True, capture_output=True, timeout=timeout, env={**os.environ,'LC_ALL':'C'})
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout or 'Commande indisponible')[:180])
    return p.stdout

def notification_channels(s):
    channels=[]
    if s.get('webhook_url'): channels.append('webhook')
    if s.get('telegram_token') and s.get('telegram_chat'): channels.append('telegram')
    if s.get('smtp_host') and s.get('smtp_to'): channels.append('email')
    return channels

def enqueue_notification(title, message):
    """Persist each delivery before it is attempted; survive a monitor restart."""
    now=time.time(); channels=notification_channels(settings())
    with db() as c:
        for channel in channels:
            c.execute('INSERT INTO notification_jobs(channel,title,message,created,due) VALUES (?,?,?,?,?)',
                      (channel,title[:255],message[:4000],now,now))
    return len(channels)

def send_notification(channel, title, message):
    """True only after the channel confirms delivery; never record secret-bearing errors."""
    s=settings()
    text=f'{title}\n{message}' + (f'\n{ORIGIN}' if ORIGIN else '')
    import smtplib
    from email.message import EmailMessage
    try:
        if channel == 'webhook' and s['webhook_url']:
            httpx.post(s['webhook_url'],json={'title':title,'message':message},timeout=10).raise_for_status()
        elif channel == 'telegram' and s['telegram_token'] and s['telegram_chat']:
            httpx.post(f'https://api.telegram.org/bot{s["telegram_token"]}/sendMessage',
                       json={'chat_id':s['telegram_chat'],'text':text},timeout=10).raise_for_status()
        elif channel == 'email' and s['smtp_host'] and s['smtp_to']:
            msg=EmailMessage(); msg['Subject']=title; msg['From']=s['smtp_from']; msg['To']=s['smtp_to']; msg.set_content(text)
            if int(s['smtp_port'])==465:
                smtp=smtplib.SMTP_SSL(s['smtp_host'],465,timeout=10,context=ssl.create_default_context())
            else:
                smtp=smtplib.SMTP(s['smtp_host'],int(s['smtp_port']),timeout=10)
            with smtp:
                if int(s['smtp_port'])!=465: smtp.starttls(context=ssl.create_default_context())
                if s['smtp_user']: smtp.login(s['smtp_user'],s['smtp_password'])
                smtp.send_message(msg)
        else:
            return False
        return True
    except Exception:
        log.warning('Notification failed on channel %s',channel)
        return False

def notification_worker():
    while not STOP.is_set():
        try:
            jobs=rows("SELECT * FROM notification_jobs WHERE status='pending' AND due<=? ORDER BY due,id LIMIT 20",(time.time(),))
            for job in jobs:
                ok=send_notification(job['channel'],job['title'],job['message'])
                tries=job['attempts']+1
                state='sent' if ok else ('failed' if tries>=8 else 'pending')
                due=time.time() + min(3600,30*(2**(tries-1)))
                with db() as c:
                    c.execute('UPDATE notification_jobs SET status=?,attempts=?,due=?,last_error=? WHERE id=? AND status=?',
                              (state,tries,due,'' if ok else 'Échec de livraison',job['id'],'pending'))
                if ok: event('notification','Alerte transmise : '+job['channel'])
                elif state=='failed': event('notification_error','Livraison abandonnée après 8 essais : '+job['channel'])
        except Exception:
            log.exception('Notification worker failed')
        STOP.wait(3)


def incident(source, active, title, message):
    changed = False
    with db() as c:
        current = c.execute('SELECT id FROM incidents WHERE source=? AND is_open=1',(source,)).fetchone()
        if active and not current:
            c.execute('INSERT INTO incidents(source,title,message,opened,is_open) VALUES (?,?,?,?,1)',(source,title,message,time.time())); changed=True
        elif not active and current:
            c.execute('UPDATE incidents SET closed=?,is_open=NULL WHERE id=?',(time.time(),current['id'])); changed=True
    if changed:
        event('incident' if active else 'recovery',title)
        enqueue_notification(('Incident : ' if active else 'Rétabli : ')+title,message)

def host_file(path, default=''):
    try: return (Path(HOST_ROOT + path)).read_text().strip()
    except OSError: return default

def host_platform():
    name = ''
    for line in host_file('/etc/os-release').splitlines():
        if line.startswith('PRETTY_NAME='): name = line.split('=',1)[1].strip('"')
    return f'{name or platform.system()} · noyau {platform.release()}'

def host_net():
    """Compteurs réseau de l'hôte : /proc/1 appartient à l'espace de noms réseau de l'hôte (pid: host), pas à celui du conteneur."""
    for path in ('/proc/1/net/dev', '/proc/net/dev'):
        try:
            result = {}
            for line in Path(path).read_text().splitlines()[2:]:
                name, _, rest = line.partition(':')
                f = rest.split()
                result[name.strip()] = {'bytes_recv': int(f[0]), 'packets_recv': int(f[1]), 'errin': int(f[2]), 'dropin': int(f[3]),
                                        'bytes_sent': int(f[8]), 'packets_sent': int(f[9]), 'errout': int(f[10]), 'dropout': int(f[11])}
            return result
        except (OSError, ValueError, IndexError): continue
    return {}

def host_disks():
    """Disques réels de l'hôte (partitions montées), avec leur occupation."""
    disks, seen = [], set()
    for path in ('/proc/1/mounts', '/proc/mounts'):
        try: lines = Path(path).read_text().splitlines()
        except OSError: continue
        for line in lines:
            device, mount, fstype = (line.split() + ['', '', ''])[:3]
            if fstype not in REAL_FS or not device.startswith('/dev/') or device in seen: continue
            try: usage = psutil.disk_usage(HOST_ROOT + mount if HOST_ROOT else mount)
            except OSError: continue
            seen.add(device); disks.append({'mount': mount, 'device': device, 'fstype': fstype, **usage._asdict()})
        if disks: break
    return disks

def service_dirs():
    try: return sorted(p for p in CGROUP_SERVICES.glob('*.service') if p.is_dir())
    except OSError: return []

def service_info(path):
    def number(name):
        try: return int((path / name).read_text().split()[0])
        except (OSError, ValueError, IndexError): return None
    try: procs = len((path / 'cgroup.procs').read_text().split())
    except OSError: procs = 0
    usec = None
    try:
        for line in (path / 'cpu.stat').read_text().splitlines():
            if line.startswith('usage_usec'): usec = int(line.split()[1])
    except (OSError, ValueError): pass
    return {'name': path.name, 'load': 'loaded', 'active': 'active' if procs else 'inactive', 'state': 'running' if procs else 'dead',
            'description': f'{procs} processus', 'memory': number('memory.current'), 'cpu_seconds': round(usec / 1e6, 1) if usec is not None else None}

def tls_days(host,port=443):
    with socket.create_connection((host,port),timeout=5) as sock:
        with ssl.create_default_context().wrap_socket(sock,server_hostname=host) as conn:
            return int((ssl.cert_time_to_seconds(conn.getpeercert()['notAfter'])-time.time())/86400)

TLS_CACHE = {}
RERUN = set()
def check(m):
    start = time.monotonic(); days=None
    try:
        kind,target = m['kind'],m['target']
        if kind in ('http','keyword','json'):
            with httpx.Client(timeout=m['timeout'],follow_redirects=True,max_redirects=5,trust_env=False) as client:
                # Stream and bound content so a monitored endpoint cannot exhaust RAM.
                with client.stream('GET',target,headers={'User-Agent':'Clubika-Monitor/1.0'}) as r:
                    status=r.status_code
                    chunks=[]; size=0
                    for chunk in r.iter_bytes():
                        size+=len(chunk)
                        if size>2_000_000: break
                        chunks.append(chunk)
                    body=b''.join(chunks).decode('utf-8','replace')
            accepted = False
            for part in m['expected'].split(','):
                span=part.strip().split('-'); lo=int(span[0]); hi=int(span[-1]); accepted |= lo<=status<=hi
            if not accepted: raise ValueError(f'HTTP {status}')
            if kind=='keyword' and m['keyword'] not in body: raise ValueError('Mot-clé absent de la réponse')
            message=f'HTTP {status}'
            u=urlsplit(target)
            if u.scheme=='https':
                key=(u.hostname,u.port or 443)
                if key not in TLS_CACHE or time.time()-TLS_CACHE[key][0]>3600:
                    try: TLS_CACHE[key]=(time.time(),tls_days(*key))
                    except Exception: TLS_CACHE[key]=(time.time(),None)
                days=TLS_CACHE[key][1]
        elif kind=='tcp':
            host,port=target.rsplit(':',1)
            with socket.create_connection((host.strip('[]'),int(port)),timeout=m['timeout']): pass
            message='Connexion TCP établie'
        elif kind=='dns':
            answer=socket.getaddrinfo(target,None)
            addresses=sorted({a[4][0] for a in answer})
            if m['keyword'] and m['keyword'] not in addresses: raise ValueError('Adresse DNS attendue absente')
            message=', '.join(addresses)[:300]
        elif kind=='ping':
            run(['ping','-n','-c','1','-W',str(m['timeout']),'--',target],m['timeout']+2); message='Hôte joignable'
        elif kind=='docker':
            state=run(['docker','inspect','--format','{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}',target]).strip()
            if not state.startswith('running') or 'unhealthy' in state: raise ValueError(state)
            message=state
        elif kind=='systemd':
            try: message=run(['systemctl','is-active','--',target]).strip()
            except FileNotFoundError:
                unit=target if target.endswith('.service') else target+'.service'
                found=[p for p in service_dirs() if p.name==unit]
                message='active' if found and service_info(found[0])['active']=='active' else 'inactive'
            if message!='active': raise ValueError(message)
        elif kind=='push':
            beats=rows('SELECT * FROM heartbeats WHERE monitor_id=?',(m['id'],))
            if not beats or time.time()-beats[0]['ts']>m['interval']+m['timeout']: raise ValueError('Signal de vie expiré ou absent')
            if not beats[0]['ok']: raise ValueError('Signal de vie en échec')
            message=beats[0]['message'] or 'Signal de vie reçu'
        elif kind=='tls':
            host,_,port=target.partition(':'); days=tls_days(host,int(port or 443))
            if days<14: raise ValueError(f'Certificat expirant dans {days} jours')
            message=f'Certificat valide : {days} jours'
        else: raise ValueError('Type de contrôle inconnu')
        if kind=='json':
            path,expected=m['keyword'].split('=',1); value=json.loads(body)
            for part in path.split('.'): value=value[int(part)] if isinstance(value,list) else value[part]
            if str(value).lower()!=expected.lower(): raise ValueError('Valeur JSON inattendue')
        return True,round((time.monotonic()-start)*1000,1),message,days
    except Exception as exc:
        message = str(exc)[:300] if isinstance(exc,(ValueError,KeyError)) else type(exc).__name__+' : contrôle en échec'
        return False,round((time.monotonic()-start)*1000,1),message,days

def monitors():
    return rows('''SELECT m.*,c.ok,c.ts AS checked_at,c.latency,c.message,c.tls_days,
       (SELECT ROUND(100.0*AVG(ok),2) FROM checks WHERE monitor_id=m.id AND ts>?) AS uptime,
       (SELECT COUNT(*) FROM checks WHERE monitor_id=m.id AND ts>?) AS check_count
       FROM monitors m LEFT JOIN checks c ON c.id=(SELECT id FROM checks WHERE monitor_id=m.id ORDER BY ts DESC LIMIT 1)
       ORDER BY m.group_name,m.name''',(time.time()-86400,time.time()-86400))


INSPECT = ('{"id":{{json .Id}},"name":{{json .Name}},"labels":{{json .Config.Labels}},"started":{{json .State.StartedAt}},'
           '"restarts":{{.RestartCount}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},'
           '"ports":{{json .NetworkSettings.Ports}},"created":{{json .Created}},"policy":{{json .HostConfig.RestartPolicy.Name}},'
           '"exit":{{.State.ExitCode}},"oom":{{json .State.OOMKilled}}}')

def collect_docker():
    listing=[json.loads(x) for x in run(['docker','ps','-a','--format','{{json .}}']).splitlines() if x]
    running=[x['ID'] for x in listing if x['State']=='running']
    stats={}
    if running:
        for line in run(['docker','stats','--no-stream','--format','{{json .}}',*running],15).splitlines():
            r=json.loads(line); stats[r['ID']]=r
    details={}
    if listing:
        try:
            for line in run(['docker','inspect','--format',INSPECT,*[x['ID'] for x in listing]],15).splitlines():
                d=json.loads(line); details[d['id'][:12]]=d
        except Exception: pass
    result=[]
    for r in listing:
        d=details.get(r['ID'][:12],{}); labels=d.get('labels') or {}
        ports=sorted({f"{b['HostIp'] or '0.0.0.0'}:{b['HostPort']}->{k}" for k,v in (d.get('ports') or {}).items() for b in (v or [])})
        result.append({'name':r['Names'],'image':r['Image'],'state':r['State'],'status':r['Status'],
           'cpu':stats.get(r['ID'],{}).get('CPUPerc','—'),'memory':stats.get(r['ID'],{}).get('MemUsage','—'),
           'network':stats.get(r['ID'],{}).get('NetIO','—'),'io':stats.get(r['ID'],{}).get('BlockIO','—'),
           'health':d.get('health'),'restarts':d.get('restarts',0),'started':d.get('started'),'created':d.get('created'),
           'ports':ports,'policy':d.get('policy'),'oom':d.get('oom',False),'exit_code':d.get('exit'),
           'compose':labels.get('com.docker.compose.project'),'source':labels.get('org.opencontainers.image.source')})
    return result

def collect_services():
    """Services systemd : `systemctl` d'abord ; à défaut (conteneur), les services en cours lus dans les cgroups."""
    try:
        lines=run(['systemctl','list-units','--type=service','--all','--no-pager','--no-legend','--plain']).splitlines()
    except (FileNotFoundError, RuntimeError):
        dirs=service_dirs()
        if not dirs: raise RuntimeError('cgroups indisponibles')
        return [service_info(p) for p in dirs]
    result=[]
    for line in lines:
        parts=line.strip().lstrip('●').split(None,4)
        if len(parts)>=4: result.append(dict(zip(['name','load','active','state','description'],parts)))
    return result

def read_text(path, default=''):
    try: return Path(path).read_text().strip()
    except OSError: return default

def cpuinfo():
    info={}
    for line in read_text('/proc/cpuinfo').splitlines():
        if ':' in line:
            k,_,v=line.partition(':'); info.setdefault(k.strip(),v.strip())
    return info

def meminfo():
    wanted=('MemTotal','MemFree','MemAvailable','Buffers','Cached','SwapCached','SwapTotal','SwapFree','Active','Inactive','Dirty','Shmem','Slab','SReclaimable','Mapped','AnonPages','CommitLimit','Committed_AS')
    result={}
    for line in read_text('/proc/meminfo').splitlines():
        k,_,v=line.partition(':')
        if k in wanted: result[k]=int(v.split()[0])*1024
    return result

def block_devices():
    devices=[]
    for p in sorted(Path('/sys/block').glob('*')):
        if p.name.startswith(('loop','ram','dm-','sr','fd','zram')): continue
        try: size=int(read_text(p/'size','0'))*512
        except ValueError: size=0
        devices.append({'name':p.name,'size':size,'rotational':read_text(p/'queue/rotational')=='1','model':read_text(p/'device/model') or None,'scheduler':re.sub(r'.*\[(.*)\].*',r'\1',read_text(p/'queue/scheduler')) or None})
    return devices

WELL_KNOWN = {21:'FTP',22:'SSH',25:'SMTP',53:'DNS',67:'DHCP',80:'HTTP',110:'POP3',123:'NTP',143:'IMAP',443:'HTTPS',465:'SMTPS',587:'SMTP (soumission)',993:'IMAPS',995:'POP3S',
  2019:'Caddy (admin)',2375:'API Docker',3000:'Application web',3306:'MariaDB / MySQL',5432:'PostgreSQL',5672:'RabbitMQ',6379:'Redis',8000:'Application web',8080:'Application web',8443:'HTTPS alternatif',9000:'Application web',11211:'Memcached',27017:'MongoDB'}

def listening_ports():
    """Sockets en écoute (TCP et UDP) : exposition (public/local), service probable et conteneur qui publie le port."""
    found=set()
    for kind,proto in (('tcp','tcp'),('udp','udp')):
        try:
            for c in psutil.net_connections(kind):
                if not c.laddr or (kind=='tcp' and c.status!='LISTEN') or c.raddr: continue
                found.add((proto+('6' if ':' in c.laddr.ip else ''),c.laddr.ip,c.laddr.port))
        except (psutil.AccessDenied,OSError): pass
    with LOCK: containers=list(CACHE.get('containers',[]))
    published={}
    for ctr in containers:
        for mapping in ctr.get('ports',[]):
            m=re.match(r'^(?:\[?([^\]]*)\]?:)?(\d+)->',mapping)
            if m: published.setdefault(int(m.group(2)),ctr['name'])
    result=[]
    for proto,ip,port in sorted(found,key=lambda x:(x[2],x[1],x[0])):
        local=ip.startswith('127.') or ip=='::1'
        result.append({'proto':proto,'address':ip,'port':port,'exposure':'local' if local else 'public','service':WELL_KNOWN.get(port),'container':published.get(port)})
    return result

def connection_states():
    states={}
    try:
        for c in psutil.net_connections('inet'): states[c.status]=states.get(c.status,0)+1
    except (psutil.AccessDenied,OSError): pass
    return states

def default_gateway():
    for line in read_text('/proc/net/route').splitlines()[1:]:
        f=line.split()
        if len(f)>2 and f[1]=='00000000':
            try: return socket.inet_ntoa(int(f[2],16).to_bytes(4,'little'))
            except (ValueError,OverflowError): pass
    return None

UPDATES={'count':None,'security':None,'checked':0}
def check_updates():
    try:
        lines=[l for l in run(['apt','list','--upgradable'],40).splitlines() if '/' in l]
        UPDATES.update(count=len(lines),security=sum(1 for l in lines if 'security' in l.split()[0]),checked=time.time())
    except Exception: UPDATES.update(count=None,security=None,checked=time.time())

def filesystems():
    result=[]
    for p in psutil.disk_partitions(all=True):
        if p.fstype in ('proc','sysfs','cgroup','cgroup2','devpts','mqueue','securityfs','debugfs','tracefs','configfs','fusectl','pstore','bpf','autofs','hugetlbfs','binfmt_misc','nsfs'): continue
        if p.mountpoint.startswith(('/proc','/sys/','/run/docker/','/var/lib/docker/','/run/user')) and p.fstype!='tmpfs': continue
        try: u=psutil.disk_usage((HOST_ROOT+p.mountpoint) if HOST_ROOT else p.mountpoint)
        except OSError: continue
        if not u.total: continue
        result.append({'mount':p.mountpoint,'device':p.device,'fstype':p.fstype,'options':p.opts,'total':u.total,'used':u.used,'percent':u.percent})
    return result[:40]

IO_PREV={}
def disk_io():
    result=[]; now=time.time()
    try: counters=psutil.disk_io_counters(perdisk=True)
    except Exception: return result
    for name,c in counters.items():
        if name.startswith(('loop','ram','dm-')) or not (c.read_count or c.write_count): continue
        prev=IO_PREV.get(name); IO_PREV[name]=(now,c)
        rate=lambda a,b,t:round((a-b)/t,1) if prev and t>0 else None
        t=now-prev[0] if prev else 0
        result.append({'name':name,'reads':c.read_count,'writes':c.write_count,'read_bytes':c.read_bytes,'write_bytes':c.write_bytes,
          'busy_ms':getattr(c,'busy_time',None),'iops':rate(c.read_count+c.write_count,prev[1].read_count+prev[1].write_count,t) if prev else None})
    return result

def collect_system():
    """Informations détaillées sur l'hôte, rafraîchies toutes les 30 s (rien de sensible : pas de variables d'environnement)."""
    ci=cpuinfo(); freq=psutil.cpu_freq(); vm=psutil.virtual_memory()
    virt=''
    try: virt=run(['systemd-detect-virt']).strip()
    except Exception: pass
    interfaces=[]
    stats=psutil.net_if_stats()
    for name,addrs in psutil.net_if_addrs().items():
        if name=='lo': continue
        st=stats.get(name)
        interfaces.append({'name':name,'up':bool(st and st.isup),'speed':st.speed if st else None,'mtu':st.mtu if st else None,
          'ipv4':[a.address for a in addrs if a.family==socket.AF_INET],'ipv6':[a.address.split('%')[0] for a in addrs if a.family==socket.AF_INET6],
          'mac':next((a.address for a in addrs if a.family==psutil.AF_LINK),None)})
    inodes=[]
    for p in LIVE.get('disks',[]):
        try:
            v=os.statvfs((HOST_ROOT+p['mount']) if HOST_ROOT else p['mount'])
            if v.f_files: inodes.append({'mount':p['mount'],'total':v.f_files,'used':v.f_files-v.f_ffree,'percent':round(100*(v.f_files-v.f_ffree)/v.f_files,1)})
        except OSError: pass
    failed=[]
    try:
        for line in run(['systemctl','--failed','--no-legend','--plain','--no-pager']).splitlines():
            parts=line.strip().lstrip('●').split(None,4)
            if parts: failed.append({'name':parts[0],'description':parts[4] if len(parts)>4 else ''})
    except Exception: failed=None
    sync=None
    try: sync=run(['timedatectl','show','-p','NTPSynchronized','--value']).strip()=='yes'
    except Exception: pass
    states={}
    threads=0
    for p in psutil.process_iter(['status','num_threads']):
        try: states[p.info['status']]=states.get(p.info['status'],0)+1; threads+=p.info['num_threads'] or 0
        except (psutil.NoSuchProcess,psutil.AccessDenied): pass
    fh=read_text('/proc/sys/fs/file-nr').split()
    resolvers=[l.split()[1] for l in read_text('/etc/resolv.conf').splitlines() if l.startswith('nameserver') and len(l.split())>1]
    cs=psutil.cpu_stats()
    return {
      'os':host_platform(),'hostname':host_file('/etc/hostname',socket.gethostname()),'arch':platform.machine(),'kernel':platform.release(),
      'boot_time':psutil.boot_time(),'timezone':read_text('/etc/timezone') or time.tzname[0],'virtualization':virt or None,
      'vendor':read_text('/sys/class/dmi/id/sys_vendor') or None,'product':read_text('/sys/class/dmi/id/product_name') or None,
      'cpu':{'model':ci.get('model name') or ci.get('Model') or platform.processor() or None,'physical':psutil.cpu_count(logical=False),'logical':psutil.cpu_count(),
             'mhz':round(freq.current) if freq else (float(ci['cpu MHz']) if 'cpu MHz' in ci else None),'max_mhz':round(freq.max) if freq and freq.max else None,
             'cache':ci.get('cache size'),'bogomips':ci.get('bogomips'),'ctx_switches':cs.ctx_switches,'interrupts':cs.interrupts,'syscalls':cs.syscalls},
      'memory':meminfo(),'swappiness':read_text('/proc/sys/vm/swappiness') or None,
      'block_devices':block_devices(),'inodes':inodes,'filesystems':filesystems(),'disk_io':disk_io(),
      'interfaces':interfaces,'listening':listening_ports(),'connections':connection_states(),'gateway':default_gateway(),'dns':resolvers,
      'process_states':states,'processes':sum(states.values()),'threads':threads or None,
      'file_handles':{'allocated':int(fh[0]),'max':int(fh[2])} if len(fh)>=3 else None,'entropy':read_text('/proc/sys/kernel/random/entropy_avail') or None,
      'users':[{'name':u.name,'terminal':u.terminal,'host':u.host,'started':u.started} for u in psutil.users()],
      'reboot_required':Path('/var/run/reboot-required').exists(),'updates':dict(UPDATES),'failed_units':failed,'ntp_synchronized':sync,
      'docker':{'version':docker_version()},'python':platform.python_version(),
    }

SOFTWARE={'packages':[],'runtimes':[],'docker_images':[],'docker_volumes':[],'checked':0,'error':None}
RUNTIMES=[('Python','python3',['--version']),('Node.js','node',['--version']),('npm','npm',['--version']),('Java','java',['-version']),('Go','go',['version']),
  ('PHP','php',['--version']),('Ruby','ruby',['--version']),('Rust','rustc',['--version']),('Git','git',['--version']),('Docker CLI','docker',['--version']),
  ('Docker Compose','docker',['compose','version']),('MariaDB','mariadbd',['--version']),('MySQL','mysqld',['--version']),('PostgreSQL','postgres',['--version']),
  ('Redis','redis-server',['--version']),('Nginx','nginx',['-v']),('Apache','apache2',['-v']),('OpenSSL','openssl',['version']),('curl','curl',['--version']),
  ('GCC','gcc',['--version']),('Make','make',['--version']),('OpenSSH','ssh',['-V']),('systemd','systemctl',['--version']),('Certbot','certbot',['--version'])]

def parse_dpkg(output, manual=(), upgradable=()):
    """Sortie de `dpkg-query -W -f='${binary:Package}\\t${Version}\\t${Installed-Size}\\t${Status}\\n'` -> liste de paquets installés."""
    packages=[]
    for line in output.splitlines():
        parts=line.split('\t')
        if len(parts)<4 or not parts[3].endswith(' installed'): continue
        name=parts[0]; base=name.split(':')[0]
        packages.append({'name':name,'version':parts[1],'size_kb':int(parts[2] or 0),'manual':base in manual,'upgradable':base in upgradable})
    return sorted(packages,key=lambda p:p['name'])

def collect_software():
    """Inventaire des logiciels (toutes les heures) : paquets système, environnements d'exécution, images et volumes Docker."""
    import shutil
    result={'packages':[],'runtimes':[],'docker_images':[],'docker_volumes':[],'checked':time.time(),'error':None}
    try:
        manual=set(run(['apt-mark','showmanual'],20).split())
        try: upgradable={l.split('/')[0] for l in run(['apt','list','--upgradable'],40).splitlines() if '/' in l}
        except Exception: upgradable=set()
        out=run(['dpkg-query','-W','-f','${binary:Package}\t${Version}\t${Installed-Size}\t${Status}\n'],20)
        result['packages']=parse_dpkg(out,manual,upgradable)
    except Exception: result['error']='Liste des paquets indisponible (gestionnaire de paquets Debian/Ubuntu requis).'
    for label,command,args in RUNTIMES:
        path=shutil.which(command)
        if not path: continue
        try:
            p=subprocess.run([path,*args],text=True,capture_output=True,timeout=4,env={**os.environ,'LC_ALL':'C'})
            text=((p.stdout or '')+(p.stderr or '')).strip().splitlines()
            version=text[0][:120] if text else ''
        except Exception: version=''
        result['runtimes'].append({'name':label,'command':command,'path':path,'version':version})
    try:
        for line in run(['docker','images','--format','{{json .}}'],15).splitlines():
            if line.strip(): d=json.loads(line); result['docker_images'].append({'repository':d['Repository'],'tag':d['Tag'],'id':d['ID'],'size':d['Size'],'created':d['CreatedSince']})
    except Exception: pass
    try: result['docker_volumes']=[l.strip() for l in run(['docker','volume','ls','-q'],10).splitlines() if l.strip()]
    except Exception: pass
    return result

DOCKER_VERSION={'value':None,'at':0}
def docker_version():
    if time.time()-DOCKER_VERSION['at']>3600:
        try: DOCKER_VERSION['value']=run(['docker','version','--format','{{.Server.Version}}']).strip()
        except Exception: DOCKER_VERSION['value']=None
        DOCKER_VERSION['at']=time.time()
    return DOCKER_VERSION['value']

def system_alerts(system):
    failed=system.get('failed_units')
    if failed is not None:
        incident('system:failed-units',bool(failed),'Services systemd en échec','; '.join(f['name'] for f in failed) or '')
    limit=float(settings()['disk_limit'])
    for i in system.get('inodes',[]):
        incident('system:inodes:'+i['mount'],i['percent']>=limit,'Inodes : '+i['mount'],f"{i['percent']} % utilisés")

def inventory():
    system_at=0; updates_at=0; previous_restarts={}
    while not STOP.is_set():
        for key,fn in [('containers',collect_docker),('services',collect_services)]:
            try:
                value=fn()
                with LOCK: CACHE[key]=value; CACHE['errors'].pop(key,None)
                if key=='containers':
                    for ctr in value:
                        name=ctr['name']; state=ctr['state']; health=ctr.get('health')
                        bad=state in ('exited','dead','restarting') or health=='unhealthy' or ctr.get('oom',False)
                        incident('docker:'+name,bad,'Docker : '+name,
                                 'État : '+state+' ; santé : '+str(health or 'non définie')+(' ; OOM' if ctr.get('oom') else ''))
                        previous=previous_restarts.get(name)
                        if previous is not None and ctr.get('restarts',0)>previous:
                            event('docker','Redémarrage du conteneur '+name)
                        previous_restarts[name]=ctr.get('restarts',0)
            except Exception:
                with LOCK: CACHE['errors'][key]='Collecte indisponible : vérifier les permissions du service.'
        if time.time()-system_at>=30:
            system_at=time.time()
            try:
                if time.time()-updates_at>=3600:
                    updates_at=time.time(); threading.Thread(target=check_updates,daemon=True).start()
                    def refresh_software():
                        try: SOFTWARE.update(collect_software())
                        except Exception: log.exception('Software inventory failed')
                    threading.Thread(target=refresh_software,daemon=True).start()
                value=collect_system()
                with LOCK: CACHE['system']=value
                system_alerts(value)
            except Exception: log.exception('System info failed')
        STOP.wait(10)

def collect():
    psutil.cpu_percent(percpu=True)
    prev_net=host_net() or {}; prev_io=psutil.disk_io_counters(); prev=time.monotonic()
    saved=0; cleanup=0; since={}
    while not STOP.is_set():
        started=time.monotonic()
        try:
            now=time.time(); elapsed=max(.1,time.monotonic()-prev); prev=time.monotonic()
            net=host_net(); io=psutil.disk_io_counters(); mem=psutil.virtual_memory(); swap=psutil.swap_memory()
            cores=psutil.cpu_percent(percpu=True); cpu=round(sum(cores)/max(1,len(cores)),1)
            disks=host_disks()
            root=psutil.disk_usage(HOST_ROOT or '/')
            if not disks: disks=[{'mount':'/','device':'root','fstype':'',**root._asdict()}]
            processes=[]
            for p in psutil.process_iter(['pid','name','username','cpu_percent','memory_percent','status']):
                try: processes.append(p.info)
                except (psutil.NoSuchProcess,psutil.AccessDenied): pass
            processes.sort(key=lambda p:(p.get('cpu_percent') or 0,p.get('memory_percent') or 0),reverse=True)
            real=[n for n in net if n!='lo' and not n.startswith(('veth','br-','docker'))]
            def total(counters,key): return sum(counters.get(n,{}).get(key,0) for n in real)
            rx=max(0,(total(net,'bytes_recv')-total(prev_net,'bytes_recv'))/elapsed); tx=max(0,(total(net,'bytes_sent')-total(prev_net,'bytes_sent'))/elapsed)
            read=max(0,(io.read_bytes-prev_io.read_bytes)/elapsed) if io and prev_io else 0
            write=max(0,(io.write_bytes-prev_io.write_bytes)/elapsed) if io and prev_io else 0
            prev_net=net; prev_io=io
            interfaces=[{'name':n,'up':True,'addresses':[],**c} for n,c in net.items() if n!='lo' and not n.startswith(('veth',))]
            sample={'ts':now,'cpu':cpu,'ram':mem.percent,'disk':max(d['percent'] for d in disks),'rx':rx,'tx':tx,'read':read,'write':write,'load':os.getloadavg()[0]}
            sensors={}
            try:
                sensors={k:[x._asdict() for x in v] for k,v in psutil.sensors_temperatures().items()}
            except (AttributeError,OSError): pass
            data={**sample,'hostname':host_file('/etc/hostname',socket.gethostname()),'platform':host_platform(),'cores':cores,'memory':mem._asdict(),'swap':swap._asdict(),
               'uptime':now-psutil.boot_time(),'load_avg':os.getloadavg(),'disks':disks,'processes':processes[:40],'interfaces':interfaces,'sensors':sensors,
               'cpu_times':psutil.cpu_times_percent()._asdict(),'connections':len(psutil.net_connections('inet')) if os.geteuid()==0 else None}
            with LOCK:
                LIVE.clear(); LIVE.update(data)
            if now-saved>=10:
                with db() as c: c.execute('INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?,?)',tuple(sample[k] for k in ('ts','cpu','ram','disk','rx','tx','read','write','load')))
                saved=now
                s=settings(); maintenance=s['maintenance_until']>now
                for metric in ('cpu','ram','disk'):
                    high=sample[metric]>=float(s[metric+'_limit']) and not maintenance
                    since[metric]=since.get(metric,now) if high else now
                    if not high or now-since[metric]>=s['sustain_seconds']:
                        incident('resource:'+metric,high,metric.upper()+' : seuil dépassé',f'{sample[metric]:.1f}% / seuil {s[metric+"_limit"]}%')
            if now-cleanup>3600:
                cutoff=now-settings()['retention_days']*86400
                with db() as c:
                    for table,col in [('samples','ts'),('checks','ts'),('events','ts')]: c.execute(f'DELETE FROM {table} WHERE {col}<?',(cutoff,))
                    c.execute("DELETE FROM notification_jobs WHERE status IN ('sent','failed') AND created<?",(cutoff,))
                    c.execute('DELETE FROM sessions WHERE expires<?',(now,))
                cleanup=now
        except Exception:
            log.exception('Host collector failed')
        STOP.wait(max(.1,2-(time.monotonic()-started)))

def scheduler():
    due={}; pending={}; failures={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        while not STOP.is_set():
            try:
                now=time.time(); s=settings()
                for ident,(future,m) in list(pending.items()):
                    if not future.done(): continue
                    del pending[ident]
                    ok,latency,message,days=future.result()
                    current=rows('SELECT * FROM monitors WHERE id=?',(ident,))
                    if not current: continue
                    m=current[0]
                    with db() as c: c.execute('INSERT INTO checks(monitor_id,ts,ok,latency,message,tls_days) VALUES (?,?,?,?,?,?)',(ident,now,int(ok),latency,message,days))
                    failures[ident]=0 if ok else failures.get(ident,0)+1
                    muted=m['paused'] or m['maintenance_until']>now or s['maintenance_until']>now
                    if not muted:
                        if ok or failures[ident]>m['retries']: incident('monitor:'+str(ident),not ok,m['name'],message)
                        if days is not None: incident('tls:'+str(ident),days<14,'Certificat : '+m['name'],f'Expiration dans {days} jours')
                    due[ident]=now+m['interval']
                with LOCK:
                    requested=set(RERUN); RERUN.clear()
                for ident in requested: due.pop(ident,None)
                for m in rows('SELECT * FROM monitors WHERE paused=0'):
                    if m['id'] not in pending and now>=due.get(m['id'],0): pending[m['id']]=(pool.submit(check,m),m)
            except Exception: log.exception('Scheduler failed')
            STOP.wait(1)

def start():
    init(); STOP.clear()
    threads=[threading.Thread(target=fn,daemon=True,name=fn.__name__) for fn in (collect,inventory,scheduler,notification_worker)]
    for t in threads: t.start()
    return threads
