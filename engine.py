"""Host collectors, bounded check scheduler and persistent incident state."""
import queue, concurrent.futures, contextlib, json, logging, os, platform, re, socket, sqlite3, ssl, subprocess, threading, time
from pathlib import Path
from urllib.parse import urlsplit
import httpx
import psutil

log = logging.getLogger('monitor')
DATA = Path(os.environ.get('MONITOR_DATA', './data'))
DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
DB = DATA / 'monitor.db'
STOP = threading.Event()
LOCK = threading.Lock()
LIVE = {}
CACHE = {'containers': [], 'services': [], 'errors': {}}
HOST_ROOT = os.environ.get('HOST_ROOT', '')  # racine de l'hôte montée en lecture seule (conteneur) ; vide = exécution directe
CGROUP_SERVICES = Path(os.environ.get('CGROUP_SERVICES', '/sys/fs/cgroup/system.slice'))
REAL_FS = {'ext2','ext3','ext4','xfs','btrfs','zfs','vfat','f2fs','ntfs','exfat'}
DEFAULTS = {'cpu_limit': 90, 'ram_limit': 90, 'disk_limit': 90, 'sustain_seconds': 60,
 'retention_days': 30, 'webhook_url': '', 'telegram_token': '', 'telegram_chat': '',
 'smtp_host': '', 'smtp_port': 587, 'smtp_user': '', 'smtp_password': '', 'smtp_from': '', 'smtp_to': '',
 'public_enabled': False, 'maintenance_until': 0}

@contextlib.contextmanager
def db():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
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
        return [dict(r) for r in c.execute(sql, args)]

def init():
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS monitors(id INTEGER PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,target TEXT NOT NULL,
          interval INTEGER NOT NULL DEFAULT 60,timeout INTEGER NOT NULL DEFAULT 10,retries INTEGER NOT NULL DEFAULT 2,
          keyword TEXT DEFAULT '',expected TEXT DEFAULT '200-299',group_name TEXT DEFAULT 'Applications',public INTEGER DEFAULT 0,
          paused INTEGER DEFAULT 0,maintenance_until REAL DEFAULT 0,push_token TEXT,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS checks(id INTEGER PRIMARY KEY,monitor_id INTEGER REFERENCES monitors(id) ON DELETE CASCADE,
          ts REAL NOT NULL,ok INTEGER NOT NULL,latency REAL,message TEXT,tls_days INTEGER);
        CREATE INDEX IF NOT EXISTS checks_monitor_ts ON checks(monitor_id,ts);
        CREATE TABLE IF NOT EXISTS samples(ts REAL PRIMARY KEY,cpu REAL,ram REAL,disk REAL,rx REAL,tx REAL,read REAL,write REAL,load REAL);
        CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY,source TEXT NOT NULL,title TEXT NOT NULL,message TEXT,
          opened REAL NOT NULL,closed REAL,acknowledged INTEGER DEFAULT 0);
        CREATE UNIQUE INDEX IF NOT EXISTS incident_open ON incidents(source) WHERE closed IS NULL;
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,ts REAL NOT NULL,kind TEXT NOT NULL,message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS heartbeats(monitor_id INTEGER PRIMARY KEY REFERENCES monitors(id) ON DELETE CASCADE,ts REAL,ok INTEGER,message TEXT);
        ''')
        for k,v in DEFAULTS.items():
            c.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (k,json.dumps(v)))
    os.chmod(DB, 0o600)

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

def notify(title, message):
    s = settings()
    text = f'{title}\n{message}\nhttps://monitor.clubika.com'
    import smtplib
    from email.message import EmailMessage
    channels = []
    if s['webhook_url']: channels.append(('webhook',lambda: httpx.post(s['webhook_url'],json={'title':title,'message':message},timeout=10).raise_for_status()))
    if s['telegram_token'] and s['telegram_chat']:
        channels.append(('Telegram',lambda: httpx.post(f'https://api.telegram.org/bot{s["telegram_token"]}/sendMessage',json={'chat_id':s['telegram_chat'],'text':text},timeout=10).raise_for_status()))
    def email():
        msg = EmailMessage(); msg['Subject']=title; msg['From']=s['smtp_from']; msg['To']=s['smtp_to']; msg.set_content(text)
        with smtplib.SMTP(s['smtp_host'], int(s['smtp_port']), timeout=10) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if s['smtp_user']: smtp.login(s['smtp_user'],s['smtp_password'])
            smtp.send_message(msg)
    if s['smtp_host'] and s['smtp_to']: channels.append(('e-mail',email))
    for name,fn in channels:
        try:
            fn(); event('notification', f'Alerte transmise : {name}')
        except Exception:
            # Never persist exceptions containing notification tokens or credentials.
            log.warning('Notification failed: %s',name)
            event('notification_error',f'Échec d’envoi : {name}. Vérifiez la configuration.')

NOTIFICATIONS = queue.Queue(maxsize=100)
def notification_worker():
    while not STOP.is_set():
        try: title,message=NOTIFICATIONS.get(timeout=1)
        except queue.Empty: continue
        notify(title,message)

def incident(source, active, title, message):
    changed = False
    with db() as c:
        current = c.execute('SELECT id FROM incidents WHERE source=? AND closed IS NULL',(source,)).fetchone()
        if active and not current:
            c.execute('INSERT INTO incidents(source,title,message,opened) VALUES (?,?,?,?)',(source,title,message,time.time())); changed=True
        elif not active and current:
            c.execute('UPDATE incidents SET closed=? WHERE id=?',(time.time(),current['id'])); changed=True
    if changed:
        event('incident' if active else 'recovery',title)
        try: NOTIFICATIONS.put_nowait((('Incident : ' if active else 'Rétabli : ')+title,message))
        except queue.Full: event('notification_error','File de notifications saturée')

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

def inventory():
    while not STOP.is_set():
        for key,fn in [('containers',collect_docker),('services',collect_services)]:
            try:
                value=fn()
                with LOCK: CACHE[key]=value; CACHE['errors'].pop(key,None)
            except Exception:
                with LOCK: CACHE['errors'][key]='Collecte indisponible : vérifier les permissions du service.'
        STOP.wait(10)

def collect_docker():
    listing=[json.loads(x) for x in run(['docker','ps','-a','--format','{{json .}}']).splitlines() if x]
    running=[x['ID'] for x in listing if x['State']=='running']
    stats={}
    if running:
        for line in run(['docker','stats','--no-stream','--format','{{json .}}',*running],15).splitlines():
            r=json.loads(line); stats[r['ID']]=r
    return [{'name':r['Names'],'image':r['Image'],'state':r['State'],'status':r['Status'],
       'cpu':stats.get(r['ID'],{}).get('CPUPerc','—'),'memory':stats.get(r['ID'],{}).get('MemUsage','—'),
       'network':stats.get(r['ID'],{}).get('NetIO','—'),'io':stats.get(r['ID'],{}).get('BlockIO','—')} for r in listing]

def collect_services():
    """Services systemd : `systemctl` d'abord ; à défaut (conteneur), les services en cours lus dans les cgroups."""
    try:
        lines=run(['systemctl','list-units','--type=service','--all','--no-pager','--no-legend','--plain']).splitlines()
    except FileNotFoundError:
        dirs=service_dirs()
        if not dirs: raise RuntimeError('cgroups indisponibles')
        return [service_info(p) for p in dirs]
    result=[]
    for line in lines:
        parts=line.strip().lstrip('●').split(None,4)
        if len(parts)>=4: result.append(dict(zip(['name','load','active','state','description'],parts)))
    return result

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
                with db() as c: c.execute('INSERT OR REPLACE INTO samples VALUES (:ts,:cpu,:ram,:disk,:rx,:tx,:read,:write,:load)',sample)
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
