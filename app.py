"""Private, single-administrator monitoring application. Run one worker per host."""
import asyncio, csv, hashlib, hmac, io, ipaddress, json, os, re, secrets, socket, sqlite3, time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
import engine as e

ROOT=Path(__file__).parent
ORIGIN=e.ORIGIN  # vide : l'origine des requêtes doit correspondre à l'hôte demandé (fonctionne derrière n'importe quel nom de domaine)
DEPLOY_STATE=Path(os.environ.get('MONITOR_DEPLOY_STATE','/opt/monitor/deploy-state'))
NAME=e.SITE_NAME or socket.gethostname()
ATTEMPTS={}

def hash_password(password,salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+':'+hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),600000).hex()

def verify_password(password,stored):
    try: return hmac.compare_digest(hash_password(password,stored.split(':')[0]),stored)
    except Exception: return False

def bootstrap():
    if not e.rows("SELECT `key` FROM settings WHERE `key`='password_hash'"):
        password=secrets.token_urlsafe(24)
        with e.db() as c: c.execute('INSERT INTO settings VALUES (?,?)',('password_hash',json.dumps(hash_password(password))))
        path=e.DATA/'admin-credentials.txt'
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f: f.write('URL: '+(ORIGIN or 'adresse de votre serveur')+'\nUtilisateur: admin\nMot de passe: '+password+'\n')

def seed_monitors():
    """Premier démarrage : détecte les conteneurs Docker et le SSH du serveur et les surveille (modifiable ensuite dans l'interface)."""
    if e.rows('SELECT id FROM monitors LIMIT 1'): return
    items=[dict(name='SSH',kind='tcp',target='127.0.0.1:22',group_name='Hébergement')]
    try:
        for line in e.run(['docker','ps','--format','{{.Names}}']).splitlines():
            if line.strip() and not line.startswith('monitor'): items.append(dict(name='Conteneur '+line.strip(),kind='docker',target=line.strip(),group_name='Conteneurs'))
    except Exception: pass
    defaults=dict(interval=60,timeout=10,retries=2,keyword='',expected='200-299',public=0)
    with e.db() as c:
        for m in items:
            v={**defaults,**m,'push_token':secrets.token_urlsafe(32),'created':time.time()}
            c.execute('INSERT INTO monitors('+','.join('`'+k+'`' for k in v)+') VALUES ('+','.join('?' for _ in v)+')',tuple(v.values()))

@asynccontextmanager
async def lifespan(app):
    threads=e.start(); e.migrate_from_sqlite(os.environ.get('MONITOR_MIGRATE_FROM','')); bootstrap(); seed_monitors()
    yield
    e.STOP.set()
    for t in threads: t.join(timeout=3)

app=FastAPI(title='Monitor Clubika',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)

@app.middleware('http')
async def security(request,call_next):
    path=request.url.path
    public=path in ('/','/status','/healthz','/api/login','/api/public','/api/config') or path.startswith('/static/') or path.startswith('/api/push/')
    if path.startswith('/api/') and not public:
        token=hashlib.sha256(request.cookies.get('monitor_session','').encode()).hexdigest()
        if not e.rows('SELECT token FROM sessions WHERE token=? AND expires>?',(token,time.time())):
            return JSONResponse({'detail':'Connexion requise'},401)
    if request.method not in ('GET','HEAD','OPTIONS') and not path.startswith('/api/push/'):
        origin=request.headers.get('origin')
        if origin and (origin!=ORIGIN if ORIGIN else urlsplit(origin).netloc!=request.headers.get('host')):
            return JSONResponse({'detail':'Origine interdite'},403)
        if request.headers.get('x-requested-with')!='Monitor':
            return JSONResponse({'detail':'En-tête de protection requis'},403)
    try: response=await call_next(request)
    except e.DBError: return JSONResponse({'detail':'Base occupée, veuillez réessayer.'},503)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    response.headers['Cache-Control']='no-store' if not path.startswith('/static/') else 'public,max-age=3600'
    return response

class Login(BaseModel):
    username:str=Field(max_length=50)
    password:str=Field(max_length=300)

@app.post('/api/login')
def login(body:Login,request:Request):
    # Derrière Caddy (socket Unix) : la vraie adresse est la dernière de X-Forwarded-For, ajoutée par Caddy.
    key=request.headers.get('x-forwarded-for','').split(',')[-1].strip() or (request.client.host if request.client else 'unknown'); now=time.time()
    ATTEMPTS[key]=[t for t in ATTEMPTS.get(key,[]) if now-t<600]
    if len(ATTEMPTS[key])>=15: raise HTTPException(429,'Trop de tentatives. Réessayez dans 10 minutes.')
    ATTEMPTS[key].append(now)
    valid=verify_password(body.password,e.settings()['password_hash'])
    if body.username!='admin' or not valid: raise HTTPException(401,'Identifiants incorrects')
    ATTEMPTS.pop(key,None)
    token=secrets.token_urlsafe(48)
    with e.db() as c: c.execute('INSERT INTO sessions VALUES (?,?)',(hashlib.sha256(token.encode()).hexdigest(),now+86400))
    response=JSONResponse({'ok':True})
    response.set_cookie('monitor_session',token,max_age=86400,httponly=True,secure=os.environ.get('MONITOR_SECURE_COOKIE','1')=='1',samesite='strict')
    e.event('auth','Connexion administrateur')
    return response

@app.post('/api/logout')
def logout(request:Request):
    with e.db() as c: c.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(request.cookies.get('monitor_session','').encode()).hexdigest(),))
    r=JSONResponse({'ok':True}); r.delete_cookie('monitor_session'); return r

class Password(BaseModel):
    current:str
    new:str=Field(min_length=12,max_length=300)

@app.post('/api/password')
def password(body:Password):
    if not verify_password(body.current,e.settings()['password_hash']): raise HTTPException(400,'Mot de passe actuel incorrect')
    with e.db() as c:
        c.execute('UPDATE settings SET value=? WHERE `key`=?',(json.dumps(hash_password(body.new)),'password_hash'))
        c.execute('DELETE FROM sessions')
    (e.DATA/'admin-credentials.txt').unlink(missing_ok=True)
    return {'ok':True}

@app.get('/healthz')
def health():
    age=time.time()-e.LIVE.get('ts',0)
    return JSONResponse({'status':'ok' if age<15 else 'starting'},200 if age<15 else 503)

@app.get('/api/live')
def live():
    with e.LOCK: return {**e.LIVE,**e.CACHE,'open_incidents':e.rows('SELECT * FROM incidents WHERE is_open=1 ORDER BY opened DESC')}

@app.get('/api/stream')
async def stream(request:Request):
    async def events():
        start=time.time()
        while not await request.is_disconnected() and time.time()-start<3600:
            with e.LOCK: data={**e.LIVE,**e.CACHE}
            yield 'data: '+json.dumps(data)+'\n\n'
            await asyncio.sleep(2)
    return StreamingResponse(events(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})

@app.get('/api/history')
def history(hours:int=1):
    if hours not in (1,6,24,168,720): raise HTTPException(400,'Période invalide')
    bucket=max(10,int(hours*3600/360))
    return e.rows('''SELECT FLOOR(ts/?)*? AS ts,AVG(cpu) AS cpu,AVG(ram) AS ram,MAX(disk) AS disk,
     AVG(rx) AS rx,AVG(tx) AS tx,AVG(`read`) AS `read`,AVG(`write`) AS `write`,AVG(`load`) AS `load` FROM samples
     WHERE ts>? GROUP BY FLOOR(ts/?) ORDER BY ts''',(bucket,bucket,time.time()-hours*3600,bucket))

@app.get('/api/monitors')
def monitors(): return e.monitors()

KINDS={'http','keyword','json','tcp','dns','ping','docker','systemd','push','tls'}
class Monitor(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    kind:str
    target:str=Field(max_length=1000,default='')
    interval:int=Field(default=60,ge=10,le=86400)
    timeout:int=Field(default=10,ge=1,le=30)
    retries:int=Field(default=2,ge=0,le=10)
    keyword:str=Field(default='',max_length=500)
    expected:str=Field(default='200-299',max_length=100)
    group_name:str=Field(default='Applications',min_length=1,max_length=60)
    public:bool=False
    @model_validator(mode='after')
    def validate_target(self):
        if self.kind not in KINDS: raise ValueError('Type de contrôle invalide')
        self.name=self.name.strip(); self.target=self.target.strip()
        if not self.name: raise ValueError('Nom obligatoire')
        if self.kind!='push' and (not self.target or self.target.startswith('-')): raise ValueError('Cible obligatoire')
        if self.kind in ('http','keyword','json'):
            u=urlsplit(self.target)
            if u.scheme not in ('http','https') or not u.hostname or u.username or u.password: raise ValueError('URL HTTP(S) sans identifiants requise')
            if any(not re.fullmatch(r'[1-5][0-9]{2}(-[1-5][0-9]{2})?',x.strip()) for x in self.expected.split(',')): raise ValueError('Codes HTTP invalides')
            if self.kind=='json' and '=' not in self.keyword: raise ValueError('JSON : utilisez chemin=valeur')
        elif self.kind=='tcp':
            host,sep,port=self.target.rpartition(':')
            if not sep or not host or not port.isdigit() or not 1<=int(port)<=65535: raise ValueError('Cible TCP : hôte:port')
        elif self.kind in ('docker','systemd','dns','ping','tls'):
            if not re.fullmatch(r'[A-Za-z0-9_.:\-]+',self.target): raise ValueError('Nom ou adresse invalide')
        return self

def required_monitor(ident):
    data=e.rows('SELECT * FROM monitors WHERE id=?',(ident,))
    if not data: raise HTTPException(404,'Contrôle introuvable')
    return data[0]

@app.post('/api/monitors')
def create_monitor(body:Monitor):
    if len(e.rows('SELECT id FROM monitors'))>=200: raise HTTPException(400,'Limite de 200 contrôles')
    values=body.model_dump(); values['push_token']=secrets.token_urlsafe(32); values['created']=time.time()
    with e.db() as c:
        keys=','.join('`'+k+'`' for k in values); marks=','.join('?' for _ in values)
        cursor=c.execute(f'INSERT INTO monitors({keys}) VALUES ({marks})',tuple(values.values())); ident=cursor.lastrowid
    e.event('config','Contrôle ajouté : '+body.name)
    return {'id':ident}

@app.put('/api/monitors/{ident}')
def update_monitor(ident:int,body:Monitor):
    required_monitor(ident); values=body.model_dump()
    with e.db() as c: c.execute('UPDATE monitors SET '+','.join('`'+k+'`=?' for k in values)+' WHERE id=?',(*values.values(),ident))
    with e.LOCK: e.RERUN.add(ident)
    e.TLS_CACHE.clear()
    return {'ok':True}

@app.delete('/api/monitors/{ident}')
def delete_monitor(ident:int):
    m=required_monitor(ident)
    with e.db() as c:
        c.execute('DELETE FROM monitors WHERE id=?',(ident,))
        c.execute('UPDATE incidents SET closed=?,is_open=NULL WHERE source IN (?,?) AND is_open=1',(time.time(),'monitor:'+str(ident),'tls:'+str(ident)))
    e.event('config','Contrôle supprimé : '+m['name']); return {'ok':True}

class Action(BaseModel):
    action:str
    minutes:int=Field(default=60,ge=0,le=10080)

@app.post('/api/monitors/{ident}/action')
def monitor_action(ident:int,body:Action):
    m=required_monitor(ident)
    with e.db() as c:
        if body.action=='pause': c.execute('UPDATE monitors SET paused=? WHERE id=?',(0 if m['paused'] else 1,ident))
        elif body.action=='maintenance': c.execute('UPDATE monitors SET maintenance_until=? WHERE id=?',(time.time()+body.minutes*60 if body.minutes else 0,ident))
        elif body.action!='check': raise HTTPException(400,'Action invalide')
    with e.LOCK: e.RERUN.add(ident)
    return {'ok':True}

@app.get('/api/monitors/{ident}/history')
def monitor_history(ident:int,hours:int=24):
    required_monitor(ident)
    hours=max(1,min(720,hours)); cutoff=time.time()-hours*3600; bucket=max(60,hours*30)
    points=e.rows('SELECT FLOOR(ts/?)*? AS ts,MIN(ok) AS ok,AVG(latency) AS latency FROM checks WHERE monitor_id=? AND ts>? GROUP BY FLOOR(ts/?) ORDER BY ts',(bucket,bucket,ident,cutoff,bucket))
    recent=e.rows('SELECT * FROM checks WHERE monitor_id=? ORDER BY ts DESC LIMIT 100',(ident,))
    return {'points':points,'recent':recent}

@app.api_route('/api/push/{token}',methods=['GET','POST'])
def heartbeat(token:str,status:str='up',message:str=''):
    if status not in ('up','down'): raise HTTPException(400,'Statut up ou down requis')
    found=e.rows("SELECT id FROM monitors WHERE push_token=? AND kind='push'",(token,))
    if not found: raise HTTPException(404,'Signal introuvable')
    with e.db() as c: c.execute('INSERT OR REPLACE INTO heartbeats VALUES (?,?,?,?)',(found[0]['id'],time.time(),status=='up',message[:300]))
    return {'ok':True}

@app.get('/api/incidents')
def incidents(): return e.rows('SELECT * FROM incidents ORDER BY opened DESC LIMIT 300')

@app.post('/api/incidents/{ident}/ack')
def acknowledge(ident:int):
    with e.db() as c:
        if not c.execute('UPDATE incidents SET acknowledged=1 WHERE id=?',(ident,)).rowcount: raise HTTPException(404,'Incident introuvable')
    return {'ok':True}

@app.get('/api/events')
def events(): return e.rows('SELECT * FROM events ORDER BY ts DESC LIMIT 200')

SECRET_KEYS={'smtp_password','telegram_token'}
@app.get('/api/settings')
def get_settings():
    s=e.settings()
    return {k:('••••••••' if v else '') if k in SECRET_KEYS else v for k,v in s.items() if k in e.DEFAULTS}

class Settings(BaseModel):
    cpu_limit:int=Field(default=90,ge=1,le=100)
    ram_limit:int=Field(default=90,ge=1,le=100)
    disk_limit:int=Field(default=90,ge=1,le=100)
    sustain_seconds:int=Field(default=60,ge=10,le=3600)
    retention_days:int=Field(default=30,ge=1,le=365)
    webhook_url:str=Field(default='',max_length=2000)
    telegram_token:str=Field(default='',max_length=200)
    telegram_chat:str=Field(default='',max_length=100)
    smtp_host:str=Field(default='',max_length=255)
    smtp_port:int=Field(default=587,ge=1,le=65535)
    smtp_user:str=Field(default='',max_length=255)
    smtp_password:str=Field(default='',max_length=500)
    smtp_from:str=Field(default='',max_length=255)
    smtp_to:str=Field(default='',max_length=255)
    public_enabled:bool=False
    maintenance_until:float=Field(default=0,ge=0)
    @model_validator(mode='after')
    def valid_url(self):
        if self.webhook_url and urlsplit(self.webhook_url).scheme!='https': raise ValueError('Webhook HTTPS requis')
        for field in ('smtp_from','smtp_to'):
            if '\n' in getattr(self,field) or '\r' in getattr(self,field): raise ValueError('Adresse e-mail invalide')
        return self

@app.put('/api/settings')
def set_settings(body:Settings):
    with e.db() as c:
        for k,v in body.model_dump().items():
            if k in SECRET_KEYS and v=='••••••••': continue
            c.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(k,json.dumps(v)))
    e.event('config','Paramètres modifiés'); return {'ok':True}

@app.post('/api/notifications/test')
def test_notifications():
    e.NOTIFICATIONS.put_nowait(('Test Monitor Clubika','Votre canal de notification est configuré.'))
    return {'ok':True,'message':'Test en file d’envoi. Résultat dans le journal.'}

@app.get('/api/export')
def export():
    # Exports deliberately omit push tokens, credentials, URLs of notification channels and password hashes.
    ms=e.rows('SELECT name,kind,target,`interval`,timeout,retries,keyword,expected,group_name,public FROM monitors')
    s=e.settings(); safe={k:s[k] for k in ('cpu_limit','ram_limit','disk_limit','sustain_seconds','retention_days','public_enabled')}
    return Response(json.dumps({'version':1,'monitors':ms,'projects':e.rows('SELECT name,url,repo_url,container,repo_dir,description FROM projects'),'settings':safe},ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="monitor-config.json"'})

def web_url(value):
    if not value: return ''
    u=urlsplit(value)
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password: raise ValueError('URL http(s) sans identifiants requise')
    return value

class Project(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    url:str=Field(default='',max_length=500)
    repo_url:str=Field(default='',max_length=500)
    container:str=Field(default='',max_length=100,pattern=r'^[A-Za-z0-9_.\-]*$')
    repo_dir:str=Field(default='',max_length=100,pattern=r'^[A-Za-z0-9_.\-]*$')
    description:str=Field(default='',max_length=500)
    @model_validator(mode='after')
    def valid(self):
        self.name=self.name.strip(); self.url=web_url(self.url.strip()); self.repo_url=web_url(self.repo_url.strip())
        return self

class Import(BaseModel):
    monitors:list[Monitor]=Field(default=[],max_length=200)
    projects:list[Project]=Field(default=[],max_length=200)

@app.post('/api/import')
def import_monitors(body:Import):
    if len(body.monitors)+len(e.rows('SELECT id FROM monitors'))>200: raise HTTPException(400,'Limite de 200 contrôles')
    with e.db() as c:
        for m in body.monitors:
            v=m.model_dump(); v.update(push_token=secrets.token_urlsafe(32),created=time.time())
            c.execute('INSERT INTO monitors('+','.join('`'+k+'`' for k in v)+') VALUES ('+','.join('?' for _ in v)+')',tuple(v.values()))
    with e.db() as c:
        for p in body.projects:
            v=p.model_dump(); v['created']=time.time()
            c.execute('INSERT INTO projects('+','.join(v)+') VALUES ('+','.join('?' for _ in v)+')',tuple(v.values()))
    return {'ok':True,'imported':len(body.monitors)+len(body.projects)}

@app.get('/api/export/metrics.csv')
def csv_metrics(hours:int=24):
    output=io.StringIO(); writer=csv.writer(output); data=history(hours)
    keys=['ts','cpu','ram','disk','rx','tx','read','write','load']; writer.writerow(keys)
    for r in data: writer.writerow([r[k] for k in keys])
    return Response(output.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="monitor-metrics.csv"'})

@app.get('/api/backup')
def backup():
    if e.MARIA:
        # Les secrets (mot de passe, jetons) ne sont pas exportés : la base MariaDB se sauvegarde avec mysqldump.
        data={'version':1,'monitors':e.rows('SELECT * FROM monitors'),'projects':e.rows('SELECT * FROM projects'),'incidents':e.rows('SELECT * FROM incidents ORDER BY opened DESC LIMIT 1000')}
        for m in data['monitors']: m.pop('push_token',None)
        return Response(json.dumps(data,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="monitor-backup.json"'})
    output=e.DATA/'backup.sqlite'
    with e.db() as c:
        target=sqlite3.connect(output)
        try: c.backup(target)
        finally: target.close()
    os.chmod(output,0o600)
    return FileResponse(output,filename='monitor-backup.sqlite',media_type='application/octet-stream')

@app.get('/api/public')
def public_status():
    if not e.settings()['public_enabled']: raise HTTPException(404,'La page publique est désactivée')
    return [{'name':m['name'],'group':m['group_name'],'ok':m['ok'],'paused':m['paused'],
     'maintenance':m['maintenance_until']>time.time(),'latency':m['latency'],'uptime':m['uptime'],'checked_at':m['checked_at']} for m in e.monitors() if m['public']]

@app.get('/api/software')
def software():
    """Paquets installés, environnements d'exécution, images Docker (liste volumineuse : hors du flux temps réel)."""
    return {**e.SOFTWARE,'updates':e.UPDATES}

@app.get('/api/config')
def config():
    return {'name':NAME,'origin':ORIGIN,'database':'MariaDB' if e.MARIA else 'SQLite'}

def deploy_info(key):
    """Dernier déploiement (optionnel) : fichiers JSON {sha, at} écrits par le script de déploiement automatique."""
    if not key: return None
    try: return json.loads((DEPLOY_STATE/(key+'.json')).read_text())
    except (OSError,ValueError): return None

@app.get('/api/projects')
def projects():
    items=e.rows('SELECT * FROM projects ORDER BY name'); ms=e.monitors()
    with e.LOCK: containers={c['name']:c for c in e.CACHE['containers']}
    for p in items:
        host=urlsplit(p['url']).hostname if p['url'] else None
        linked=[m for m in ms if (host and urlsplit(m['target']).hostname==host) or (p['container'] and m['kind']=='docker' and m['target']==p['container'])]
        p['monitors']=[{'id':m['id'],'name':m['name'],'ok':m['ok'],'paused':m['paused'],'latency':m['latency'],'uptime':m['uptime'],'tls_days':m['tls_days']} for m in linked]
        p['container_info']=containers.get(p['container'])
        p['deploy']=deploy_info(p['repo_dir'] or p['container'])
    return items

@app.post('/api/projects')
def create_project(body:Project):
    if len(e.rows('SELECT id FROM projects'))>=200: raise HTTPException(400,'Limite de 200 projets')
    with e.db() as c:
        v=body.model_dump(); v['created']=time.time()
        ident=c.execute('INSERT INTO projects('+','.join(v)+') VALUES ('+','.join('?' for _ in v)+')',tuple(v.values())).lastrowid
    e.event('config','Projet ajouté : '+body.name); return {'id':ident}

@app.put('/api/projects/{ident}')
def update_project(ident:int,body:Project):
    v=body.model_dump()
    with e.db() as c:
        if not c.execute('UPDATE projects SET '+','.join(k+'=?' for k in v)+' WHERE id=?',(*v.values(),ident)).rowcount: raise HTTPException(404,'Projet introuvable')
    return {'ok':True}

@app.delete('/api/projects/{ident}')
def delete_project(ident:int):
    with e.db() as c:
        if not c.execute('DELETE FROM projects WHERE id=?',(ident,)).rowcount: raise HTTPException(404,'Projet introuvable')
    return {'ok':True}

@app.get('/api/discover')
def discover():
    """Conteneurs non rattachés à un projet : l'administrateur peut les ajouter en un clic (et les compléter avec l'adresse et le dépôt)."""
    known={p['container'] for p in e.rows('SELECT container FROM projects')}
    with e.LOCK: containers=list(e.CACHE['containers'])
    return [{'name':c['name'],'image':c['image'],'state':c['state'],'ports':c['ports'],'compose':c['compose'],'source':c['source']} for c in containers if c['name'] not in known and not c['name'].startswith('monitor')]

@app.get('/')
def index(): return FileResponse(ROOT/'static/index.html')
@app.get('/status')
def status_page(): return FileResponse(ROOT/'static/status.html')
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
