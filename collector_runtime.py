"""Read-only collector scheduling, isolated from HTTP requests and host sampling.

Timeouts stop accepting results, not Python threads. A timed-out worker retains its
slot until it actually exits, so an unhealthy collector cannot create unbounded
threads. Built-ins use CollectorContext to bound child commands and file reads.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import threading
import time
from typing import Callable


class CollectorUnavailable(Exception):
    """Expected absence or unreadability, with no raw command error exported."""


class OutputLimitExceeded(Exception):
    """The collector's bounded output contract was exceeded."""


@dataclass(frozen=True)
class Detection:
    status: str = 'available'
    reason: str | None = None


@dataclass(frozen=True)
class CollectionResult:
    data: dict
    status: str = 'available'
    reason: str | None = None


@dataclass(frozen=True)
class CollectorContext:
    deadline: float
    proc_root: Path = Path('/proc')
    sys_root: Path = Path('/sys')

    def remaining(self):
        remaining=self.deadline-time.monotonic()
        if remaining <= 0: raise TimeoutError
        return remaining

    def read_text(self, path, max_bytes=65536):
        self.remaining()
        with Path(path).open('rb') as stream:
            data=stream.read(max_bytes+1)
        if len(data)>max_bytes:raise OutputLimitExceeded
        self.remaining()
        return data.decode('utf-8',errors='replace').strip()

    def run(self, args, *, timeout=None, max_bytes=262144, check=True, include_stderr=False):
        """No shell, no inherited secrets, bounded stdout; stderr never retained."""
        remaining=self.remaining()
        end=time.monotonic()+min(remaining,timeout if timeout is not None else remaining)
        env={'PATH':os.environ.get('PATH',os.defpath),'LC_ALL':'C','SYSTEMD_COLORS':'0',
             'SYSTEMD_PAGER':'cat','PAGER':'cat'}
        # Preserve the explicitly configured local Docker endpoint, never the
        # process environment or a registry configuration/credential directory.
        for name in ('DOCKER_HOST','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):
            if name in os.environ: env[name]=os.environ[name]
        proc=subprocess.Popen(list(args),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT if include_stderr else subprocess.DEVNULL,env=env,start_new_session=True)
        output=bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout,selectors.EVENT_READ)
                while selector.get_map():
                    wait=end-time.monotonic()
                    if wait<=0:raise TimeoutError
                    for key,_ in selector.select(wait):
                        chunk=os.read(key.fd,min(65536,max_bytes-len(output)+1))
                        if not chunk:selector.unregister(key.fileobj);continue
                        output.extend(chunk)
                        if len(output)>max_bytes:raise OutputLimitExceeded
            wait=end-time.monotonic()
            if wait<=0:raise TimeoutError
            proc.wait(timeout=wait)
            if check and proc.returncode:raise CollectorUnavailable
            return output.decode('utf-8',errors='replace')
        except subprocess.TimeoutExpired:
            raise TimeoutError from None
        finally:
            if proc.poll() is None:
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                try:proc.wait(timeout=.2)
                except subprocess.TimeoutExpired:pass
            if proc.stdout:proc.stdout.close()


@dataclass(frozen=True)
class CollectorSpec:
    id: str
    title: str
    category: str
    detect: Callable[[CollectorContext],Detection]
    collect: Callable[[CollectorContext],dict | CollectionResult]
    dependencies: tuple = ()
    permissions: tuple = ()
    interval: float = 60
    timeout: float = 5
    limits: dict = field(default_factory=lambda:{'max_bytes':524288})
    schema: dict = field(default_factory=dict)


@dataclass
class _Task:
    started: float
    deadline: float
    thread: threading.Thread | None = None
    outcome: CollectionResult | None = None
    reason_code: str | None = None
    done: threading.Event = field(default_factory=threading.Event)
    timed_out: bool = False


class CollectorRuntime:
    """tick() only schedules/completes work; collectors run in <=2 daemon workers."""
    def __init__(self,specs,*,intervals=None,on_result=None,on_sample=None,max_workers=2,
                 clock=time.time,monotonic=time.monotonic,context_factory=CollectorContext):
        specs=list(specs)
        if len({s.id for s in specs})!=len(specs):raise ValueError('Identifiants de collecteurs dupliqués')
        if not isinstance(max_workers,int) or isinstance(max_workers,bool) or not 1<=max_workers<=2:raise ValueError('Concurrence invalide')
        self.specs={s.id:s for s in specs};intervals=intervals or {}
        if not isinstance(intervals,dict) or set(intervals)-set(self.specs):raise ValueError('Configuration de collecteur inconnue')
        self._intervals={}
        for s in specs:
            value=intervals.get(s.id,s.interval)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not .005<=value<=86400:raise ValueError('Intervalle de collecte invalide')
            if not isinstance(s.timeout,(int,float)) or not math.isfinite(s.timeout) or not .005<=s.timeout<=300:raise ValueError('Délai de collecte invalide')
            self._intervals[s.id]=value
        self._clock=clock;self._monotonic=monotonic;self._context_factory=context_factory
        self._on_result=on_result;self._on_sample=on_sample;self._max_workers=max_workers;self._lock=threading.RLock()
        self._tasks={};self._closed=False
        self._callback_condition=threading.Condition(self._lock)
        self._callback_pending={};self._callback_active=None;self._callback_thread=None
        self._versions={ident:0 for ident in self.specs}
        self._states={s.id:{'id':s.id,'title':s.title,'category':s.category,'status':'pending',
            'reason':'Première collecte en attente.','reason_code':None,'last_attempt':None,'last_success':None,
            'duration_ms':None,'next_run':0,'interval':self._intervals[s.id],'timeout':s.timeout,
            'dependencies':list(s.dependencies),'permissions':list(s.permissions),'limits':copy.deepcopy(s.limits),
            'schema':copy.deepcopy(s.schema),'data':{},'stale':True,'persistence_error':False,
            'persistence_pending':False,'sample_error':False} for s in specs}
        if self._on_result:
            # A single daemon for persistence, never one thread per result. A
            # blocked DB write holds this worker only, not collector scheduling.
            self._callback_thread=threading.Thread(target=self._callback_worker,daemon=True,name='inventory-persistence')
            self._callback_thread.start()

    def _callback_worker(self):
        while True:
            with self._callback_condition:
                self._callback_condition.wait_for(lambda:self._closed or self._callback_pending)
                if self._closed:return
                ident=next(iter(self._callback_pending))
                version,row=self._callback_pending.pop(ident)
                self._callback_active=(ident,version)
            failed=False
            try:self._on_result(row)
            except Exception:failed=True
            finally:
                with self._callback_condition:
                    self._callback_active=None
                    state=self._states[ident]
                    if failed:state['persistence_error']=True
                    elif version==self._versions[ident]:state['persistence_error']=False
                    # An earlier write finishing must never clear the pending
                    # indication of a newer coalesced result for the same id.
                    state['persistence_pending']=ident in self._callback_pending
                    self._callback_condition.notify_all()

    def _work(self,spec,task):
        try:
            ctx=self._context_factory(task.deadline)
            found=spec.detect(ctx)
            if not isinstance(found,Detection) or found.status not in ('available','unavailable','unsupported'):raise ValueError
            if found.status!='available':
                task.outcome=CollectionResult({},found.status,found.reason)
            else:
                ctx.remaining()
                value=spec.collect(ctx)
                if isinstance(value,dict):value=CollectionResult(value)
                if not isinstance(value,CollectionResult) or not isinstance(value.data,dict) or value.status not in ('available','unavailable','unsupported','error'):raise ValueError
                def validate(node,depth=0,count=None):
                    count=count if count is not None else [0];count[0]+=1
                    if depth>20 or count[0]>20000:raise OutputLimitExceeded
                    if isinstance(node,dict):
                        for child in node.values():validate(child,depth+1,count)
                    elif isinstance(node,list):
                        for child in node:validate(child,depth+1,count)
                validate(value.data)
                serialized=json.dumps(value.data,allow_nan=False,ensure_ascii=False)
                if len(serialized.encode('utf-8'))>spec.limits.get('max_bytes',524288):raise OutputLimitExceeded
                ctx.remaining();task.outcome=value
        except (PermissionError,CollectorUnavailable):
            task.outcome=CollectionResult({},'unavailable','Source absente ou non accessible avec les permissions actuelles.')
            task.reason_code='access'
        except FileNotFoundError:
            task.outcome=CollectionResult({},'unavailable','Dépendance ou source absente.')
            task.reason_code='missing'
        except TimeoutError:
            task.outcome=CollectionResult({},'error','Délai maximal de collecte dépassé.')
            task.reason_code='timeout'
        except OutputLimitExceeded:
            task.outcome=CollectionResult({},'error','Volume maximal de collecte dépassé.')
            task.reason_code='output_limit'
        except Exception:
            task.outcome=CollectionResult({},'error','La collecte a échoué ; aucune sortie de commande sensible n’est conservée.')
            task.reason_code='collection_error'
        finally:task.done.set()

    def _publish(self,ident,task,now,*,timeout=False):
        row=self._states[ident]
        result=CollectionResult({},'error','Délai maximal de collecte dépassé.') if timeout else task.outcome
        row.update(status=result.status,reason=result.reason,reason_code='timeout' if timeout else task.reason_code,
                   duration_ms=round(max(0,self._monotonic()-task.started)*1000,2),
                   next_run=now+row['interval'])
        if result.status=='available':
            row.update(data=copy.deepcopy(result.data),last_success=now,stale=False)
        else:row['stale']=True
        return copy.deepcopy(row)

    def tick(self):
        callbacks=[]
        with self._lock:
            if self._closed:return
            now=self._clock();mono=self._monotonic()
            for ident,task in list(self._tasks.items()):
                if task.done.is_set():
                    if not task.timed_out:callbacks.append(self._publish(ident,task,now))
                    del self._tasks[ident]
                elif not task.timed_out and mono>=task.deadline:
                    task.timed_out=True;callbacks.append(self._publish(ident,task,now,timeout=True))
            due=sorted((row['next_run'],ident) for ident,row in self._states.items() if ident not in self._tasks and now>=row['next_run'])
            for _,ident in due:
                if len(self._tasks)>=self._max_workers:break
                spec=self.specs[ident];task=_Task(mono,mono+spec.timeout)
                self._tasks[ident]=task;self._states[ident]['last_attempt']=now
                task.thread=threading.Thread(target=self._work,args=(spec,task),daemon=True,name='inventory-'+ident)
                task.thread.start()
        # on_sample is explicitly restricted to a lightweight in-memory cache
        # update. Persistent I/O is always queued outside the scheduling path.
        for row in callbacks:
            ident=row['id']
            if self._on_sample:
                try:self._on_sample(copy.deepcopy(row))
                except Exception:
                    with self._lock:self._states[ident]['sample_error']=True
                else:
                    with self._lock:self._states[ident]['sample_error']=False
            if self._on_result:
                with self._callback_condition:
                    if self._closed:continue
                    self._versions[ident]+=1
                    self._callback_pending[ident]=(self._versions[ident],row)
                    self._states[ident]['persistence_pending']=True
                    self._callback_condition.notify()

    def snapshot(self):
        with self._lock:
            now=self._clock();rows=copy.deepcopy(list(self._states.values()))
            persistence={'queued':len(self._callback_pending),'capacity':len(self.specs),
                         'running':self._callback_active is not None,'enabled':self._on_result is not None}
        summary={key:0 for key in ('available','unavailable','unsupported','error','pending')}
        for row in rows:
            row['stale']=row['stale'] or row['last_success'] is None or now-row['last_success']>max(row['interval']*2,row['interval']+row['timeout'])
            summary[row['status']]+=1
        return {'collected_at':max((r['last_success'] or 0 for r in rows),default=0) or None,'collectors':rows,'summary':summary,
                'persistence':persistence}

    def close(self,timeout=.2):
        with self._callback_condition:
            self._closed=True;tasks=list(self._tasks.values())
            for ident in self._callback_pending:
                self._states[ident]['persistence_error']=True
                self._states[ident]['persistence_pending']=bool(self._callback_active and self._callback_active[0]==ident)
            self._callback_pending.clear();self._callback_condition.notify_all()
        end=time.monotonic()+max(0,min(timeout,1))
        for task in tasks:
            if task.thread:task.thread.join(timeout=max(0,end-time.monotonic()))
        if self._callback_thread and self._callback_thread is not threading.current_thread():
            self._callback_thread.join(timeout=max(0,end-time.monotonic()))

    def __enter__(self):return self
    def __exit__(self,*exc):self.close()
