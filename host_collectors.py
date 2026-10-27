"""Optional Linux collectors. No credentials, database queries, logs or scans.

All reads target explicitly named kernel files or allowlisted command properties.
Installed tools, running engines and default-port candidates remain distinct facts.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil

import psutil
from collector_runtime import CollectorSpec, CollectorContext, Detection, CollectionResult, CollectorUnavailable

UNIT=re.compile(r'^[A-Za-z0-9_.@:\\-]{1,240}$')


def linux(ctx):
    return Detection() if platform.system()=='Linux' else Detection('unsupported','Collecteur réservé aux interfaces Linux.')


def systemd(ctx):
    found=linux(ctx)
    if found.status!='available':return found
    return Detection() if shutil.which('systemctl') else Detection('unavailable','systemctl n’est pas installé ou absent du chemin du service.')


def optional_text(ctx,path,max_bytes=65536):
    try:return ctx.read_text(path,max_bytes=max_bytes)
    except OSError:return None


def integer(value):
    try:
        number=int(value)
        return number if 0<=number<2**64-1 else None
    except (TypeError,ValueError):return None


def collect_pressure(ctx):
    resources={};missing=[]
    for resource in ('cpu','memory','io'):
        value=optional_text(ctx,ctx.proc_root/'pressure'/resource,max_bytes=4096)
        parsed={}
        for line in (value or '').splitlines():
            fields=line.split()
            if not fields or fields[0] not in ('some','full'):continue
            try:
                parts=dict(piece.split('=',1) for piece in fields[1:])
                values={key:float(parts[key]) for key in ('avg10','avg60','avg300')}
                total=int(parts['total'])
                if any(not math.isfinite(v) or not 0<=v<=100 for v in values.values()) or total<0:continue
            except (ValueError,KeyError):continue
            parsed[fields[0]]={**values,'total_us':total}
        resources[resource]=parsed or None
        if not parsed:missing.append(resource)
    if len(missing)==3:raise CollectorUnavailable
    return CollectionResult({'resources':resources,'missing':missing,'partial':bool(missing),'unit':'percent_of_stalled_time'},
                            reason='Certains fichiers PSI sont absents, illisibles ou invalides.' if missing else None)


def _cpus(value):
    if not value:return None
    cpus=set()
    for part in value.split(','):
        pair=part.split('-')
        try:
            start=int(pair[0]);end=int(pair[-1])
            if len(pair)>2 or not 0<=start<=end<=65535:return None
            cpus.update(range(start,end+1))
        except ValueError:return None
    return len(cpus) or None


def collect_cgroup(ctx):
    root=ctx.sys_root/'fs/cgroup'
    if optional_text(ctx,root/'cgroup.controllers',4096) is None:raise CollectorUnavailable
    membership=optional_text(ctx,ctx.proc_root/'self/cgroup',4096)
    member=next((line[3:] for line in (membership or '').splitlines() if line.startswith('0::')),None)
    if member is None:raise CollectorUnavailable
    relative=PurePosixPath(member)
    if not relative.is_absolute() or '..' in relative.parts:raise CollectorUnavailable
    leaf=root.joinpath(*relative.parts[1:])
    # No arbitrary file walk: at most 32 explicitly named ancestor directories.
    ancestors=[];path=leaf
    while len(ancestors)<32:
        ancestors.append(path)
        if path==root:break
        path=path.parent
    if ancestors[-1]!=root:raise CollectorUnavailable
    missing=[]
    def read(path):
        value=optional_text(ctx,path,4096)
        if value is None:missing.append(path.name)
        return value
    memory_limits=[];cpu_limits=[];pids_limits=[]
    cpu_quota=None
    for ancestor in ancestors:
        value=read(ancestor/'memory.max');number=integer(value)
        if number is not None:memory_limits.append(number)
        value=read(ancestor/'pids.max');number=integer(value)
        if number is not None:pids_limits.append(number)
        fields=(read(ancestor/'cpu.max') or '').split()
        if len(fields)==2:
            quota,period=integer(fields[0]),integer(fields[1])
            if quota is not None and period:
                limit=quota/period;cpu_limits.append(limit)
                if ancestor==leaf:cpu_quota=limit
    logical=os.cpu_count()
    cpuset=_cpus(read(leaf/'cpuset.cpus.effective'))
    candidates=cpu_limits+[x for x in (logical,cpuset) if x is not None]
    events={}
    for line in (read(leaf/'memory.events') or '').splitlines():
        fields=line.split()
        if len(fields)==2 and fields[0] in ('low','high','max','oom','oom_kill','oom_group_kill'):
            events[fields[0]]=integer(fields[1])
    return {'scope':'monitor_process','version':2,
            'memory':{'current_bytes':integer(read(leaf/'memory.current')),
                      'effective_limit_bytes':min(memory_limits) if memory_limits else None,'events':events},
            'cpu':{'quota_cores':cpu_quota,'effective_limit_cores':min(candidates) if candidates else None,
                   'cpuset_count':cpuset,'host_logical_cpus':logical},
            'pids':{'current':integer(read(leaf/'pids.current')),'effective_limit':min(pids_limits) if pids_limits else None},
            'partial':bool(missing),'missing':sorted(set(missing)),
            'note':'Limites du groupe du processus Monitor et de ses ancêtres ; ne décrit pas toutes les limites de l’hôte.'}


def _epoch(value):
    if isinstance(value,bool):return None
    if isinstance(value,(int,float)) and math.isfinite(value) and value>0 and value<2**64-1:
        return value/1_000_000 if value>10**12 else value
    return None


def collect_timers(ctx):
    values=json.loads(ctx.run(['systemctl','list-timers','--all','--no-pager','--output=json'],max_bytes=262144))
    if not isinstance(values,list):raise CollectorUnavailable
    timers=[]
    for value in values[:300]:
        if not isinstance(value,dict):continue
        unit=value.get('unit','');activates=value.get('activates')
        if not UNIT.fullmatch(unit):continue
        timers.append({'unit':unit,'activates':activates if isinstance(activates,str) and UNIT.fullmatch(activates) else None,
                       'next_at':_epoch(value.get('next')),'last_at':_epoch(value.get('last'))})
    return CollectionResult({'timers':timers,'count':len(timers),'truncated':len(values)>300,
                             'source':'systemctl list-timers'},reason='Liste limitée à 300 timers.' if len(values)>300 else None)


SERVICE_PROPERTIES=('Id','ActiveState','SubState','UnitFileState','NRestarts','MemoryCurrent','CPUUsageNSec',
                    'TasksCurrent','Requires','Wants','After','Result')


def collect_service_metadata(ctx):
    units=json.loads(ctx.run(['systemctl','list-units','--type=service','--all','--no-pager','--output=json'],max_bytes=262144))
    if not isinstance(units,list):raise CollectorUnavailable
    names=[x['unit'] for x in units if isinstance(x,dict) and isinstance(x.get('unit'),str) and UNIT.fullmatch(x['unit'])]
    unit_files_unavailable=False;installed_states={}
    try:
        installed=json.loads(ctx.run(['systemctl','list-unit-files','--type=service','--no-pager','--output=json'],max_bytes=262144))
        if not isinstance(installed,list):raise CollectorUnavailable
        installed_states={x['unit_file']:x.get('state') for x in installed if isinstance(x,dict) and isinstance(x.get('unit_file'),str) and UNIT.fullmatch(x['unit_file'])}
        names+=list(installed_states)
    except (CollectorUnavailable,FileNotFoundError,ValueError,OSError):
        unit_files_unavailable=True
    names=list(dict.fromkeys(names))
    if not names:return CollectionResult({'services':[],'count':0,'truncated':False})
    chosen=names[:200]
    # One unloadable template can make systemctl exit nonzero despite valid
    # properties for other units. Parse only selected properties, never stderr.
    text=ctx.run(['systemctl','show','--no-pager','--property='+','.join(SERVICE_PROPERTIES),'--',*chosen],max_bytes=524288,check=False)
    services=[]
    for block in re.split(r'\n\s*\n',text.strip()):
        values={}
        for line in block.splitlines():
            key,separator,value=line.partition('=')
            if separator and key in SERVICE_PROPERTIES:values[key]=value
        name=values.get('Id')
        if not name or name not in chosen:continue
        dependencies={}
        for key in ('Requires','Wants','After'):
            dependencies[key.lower()]=[x for x in values.get(key,'').split() if UNIT.fullmatch(x)][:80]
        services.append({'name':name,'active':values.get('ActiveState') or None,'state':values.get('SubState') or None,
                         'enabled':values.get('UnitFileState') or None,'restarts':integer(values.get('NRestarts')),
                         'memory_bytes':integer(values.get('MemoryCurrent')),
                         'cpu_seconds':(integer(values.get('CPUUsageNSec'))/1e9 if integer(values.get('CPUUsageNSec')) is not None else None),
                         'tasks':integer(values.get('TasksCurrent')),'result':values.get('Result') or None,'dependencies':dependencies})
    partial=unit_files_unavailable or len(services)!=len(chosen)
    received={row['name'] for row in services}
    for name in chosen:
        if name not in received:
            services.append({'name':name,'active':None,'state':None,'enabled':installed_states.get(name),
                             'restarts':None,'memory_bytes':None,'cpu_seconds':None,'tasks':None,
                             'result':None,'dependencies':{},'metadata_status':'unavailable'})
    return CollectionResult({'services':services,'count':len(services),'truncated':len(names)>200,'partial':partial,
                             'source':'systemctl show (propriétés autorisées)'},
                            reason='Certaines unités ne sont plus présentes ou ne sont pas accessibles.' if partial else None)


DATABASE_PROCESSES={'mariadbd':'MariaDB','mysqld':'MySQL','postgres':'PostgreSQL','postmaster':'PostgreSQL',
                    'redis-server':'Redis','valkey-server':'Valkey','mongod':'MongoDB','memcached':'Memcached'}
DATABASE_PORTS={3306:'MariaDB / MySQL',5432:'PostgreSQL',6379:'Redis / Valkey',27017:'MongoDB',11211:'Memcached'}


def collect_database_engines(ctx):
    engines={};pid_engine={};partial=[]
    try:
        for count,process in enumerate(psutil.process_iter(['pid','name'])):
            ctx.remaining()
            if count>=10000:partial.append('process_limit');break
            try:
                info=process.info
                if info.get('name') is None:partial.append('process_access');continue
                engine=DATABASE_PROCESSES.get(info.get('name'))
                if engine:
                    row=engines.setdefault(engine,{'engine':engine,'processes':[],'listeners':[],
                        'metrics_status':'unavailable','metrics_reason':'Connexion dédiée en lecture seule requise pour les métriques internes ; aucune base n’a été ouverte.'})
                    if len(row['processes'])<32:row['processes'].append({'pid':info['pid'],'name':info['name']})
                    pid_engine[info['pid']]=engine
            except (psutil.NoSuchProcess,psutil.AccessDenied):partial.append('process_access')
    except (psutil.AccessDenied,OSError):partial.append('process_access')
    candidates=[]
    try:
        for conn in psutil.net_connections('inet'):
            ctx.remaining()
            if not conn.laddr or conn.status!='LISTEN' or conn.raddr:continue
            listener={'address':conn.laddr.ip,'port':conn.laddr.port}
            engine=pid_engine.get(conn.pid)
            if engine and len(engines[engine]['listeners'])<32:
                engines[engine]['listeners'].append({**listener,'pid':conn.pid,'evidence':'listener_pid_matches_engine_process'})
            elif conn.laddr.port in DATABASE_PORTS and len(candidates)<64:
                candidates.append({**listener,'candidate_engine':DATABASE_PORTS[conn.laddr.port],
                                   'evidence':'default_port_only','confirmed':False})
    except (psutil.AccessDenied,OSError):partial.append('listener_access')
    return CollectionResult({'engines':sorted(engines.values(),key=lambda x:x['engine']),
                             'unattributed_listeners':candidates,'partial':bool(partial),'limits':sorted(set(partial)),
                             'scope':'visible_processes_and_local_listeners','database_connections_opened':0},
                            reason='Visibilité des processus ou sockets limitée.' if partial else None)


def collect_security_state(ctx):
    apparmor=optional_text(ctx,ctx.sys_root/'module/apparmor/parameters/enabled',32)
    selinux=optional_text(ctx,ctx.sys_root/'fs/selinux/enforce',32)
    tools={tool:bool(shutil.which(tool)) for tool in ('ufw','nft','iptables','fail2ban-client','aa-status','getenforce')}
    return CollectionResult({'apparmor':{'status':{'Y':'enabled','N':'disabled'}.get(apparmor,'unknown'),
                                        'reason':None if apparmor in ('Y','N') else 'État noyau absent ou inaccessible.'},
                             'selinux':{'status':{'1':'enforcing','0':'permissive'}.get(selinux,'unknown'),
                                        'reason':None if selinux in ('1','0') else 'Interface SELinux absente ou inaccessible.'},
                             'tools':tools,'firewall':{'rules_status':'not_collected',
                               'reason':'Aucune règle lue et aucun privilège supplémentaire demandé ; présence d’un outil distincte de l’état du filtrage.'},
                             'effective_uid':os.geteuid(),'scope':'kernel_state_only'})


def default_collectors():
    """Schemas are explicit per collector; optional tools are re-detected each run."""
    definitions=[
        ('pressure','Pression des ressources','performance',linux,collect_pressure,(),30,2,{'resources':'cpu/memory/io -> some/full -> avg10/avg60/avg300/total_us','missing':'list[str]','partial':'bool'}),
        ('cgroup_limits','Limites du processus Monitor','system',linux,collect_cgroup,(),60,2,{'scope':'monitor_process','version':2,'cpu':'effective_limit_cores, quota_cores, cpuset_count, host_logical_cpus','memory':'current_bytes, effective_limit_bytes, events','pids':'current, effective_limit','partial':'bool'}),
        ('systemd_timers','Timers systemd','services',systemd,collect_timers,('systemctl',),120,5,{'timers':'[{unit, activates, next_at, last_at}]','count':'int','truncated':'bool'}),
        ('service_metadata','Configuration et ressources des services','services',systemd,collect_service_metadata,('systemctl',),60,8,{'services':'[{name,active,state,enabled,restarts,memory_bytes,cpu_seconds,tasks,result,dependencies}]','count':'int','partial':'bool','truncated':'bool'}),
        ('database_engines','Moteurs de bases de données détectés','databases',linux,collect_database_engines,('psutil',),120,5,{'engines':'[{engine,processes,listeners,metrics_status,metrics_reason}]','unattributed_listeners':'candidates only','partial':'bool','database_connections_opened':0}),
        ('security_state','États de sécurité accessibles','security',linux,collect_security_state,(),300,2,{'apparmor':'status,reason','selinux':'status,reason','tools':'tool -> detected bool','firewall':'rules_status,reason','scope':'kernel_state_only'}),
    ]
    return [CollectorSpec(ident,title,category,detect,collect,dependencies=dependencies,
                          permissions=('lecture seule avec le compte du service Monitor',),interval=interval,timeout=timeout,
                          limits={'max_bytes':524288,'max_items':200 if ident=='service_metadata' else 300,'max_processes':10000 if ident=='database_engines' else None},schema=schema)
            for ident,title,category,detect,collect,dependencies,interval,timeout,schema in definitions]
