"""Bounded collector execution and safe Linux metadata, without engine startup."""
import json
import sys
import threading
import time
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector_runtime import CollectorContext, CollectorRuntime, CollectorSpec, Detection, CollectionResult, CollectorUnavailable, OutputLimitExceeded
import host_collectors as h


def wait_for(runtime, status='available', ident='test', seconds=1):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        runtime.tick()
        result=next(x for x in runtime.snapshot()['collectors'] if x['id']==ident)
        if result['status']==status:return result
        time.sleep(.005)
    pytest.fail('collector status not reached')


def eventually(predicate,seconds=1):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.003)
    pytest.fail('asynchronous callback state not reached')


def spec(collect, **kwargs):
    return CollectorSpec('test','Test','test',lambda ctx:Detection(),collect,**kwargs)


def test_snapshot_and_callback_are_detached_and_include_contract():
    saved=[]
    with CollectorRuntime([spec(lambda ctx:{'actual':42})],on_result=saved.append) as runtime:
        initial=runtime.snapshot()['collectors'][0]
        assert initial['status']=='pending' and initial['last_success'] is None
        row=wait_for(runtime)
        assert row['data']=={'actual':42} and not row['stale']
        assert row['last_success'] and row['duration_ms']>=0
        assert row['next_run']>=row['last_attempt']
        assert {'dependencies','permissions','limits','schema','interval','timeout'} <= row.keys()
        row['data']['actual']=99
        assert runtime.snapshot()['collectors'][0]['data']['actual']==42
        eventually(lambda:bool(saved))
        assert saved[0]['data']=={'actual':42}


def test_unavailable_is_retested_and_errors_keep_last_good_data():
    available=[False]; fail=[False]
    def collect(ctx):
        if fail[0]:raise RuntimeError('password=must-not-leak')
        return {'value':1}
    s=spec(collect,interval=.02,timeout=.2)
    s=CollectorSpec(s.id,s.title,s.category,lambda ctx:Detection('available' if available[0] else 'unavailable','outil absent'),collect,interval=.02,timeout=.2)
    with CollectorRuntime([s]) as runtime:
        wait_for(runtime,'unavailable')
        available[0]=True;time.sleep(.03);good=wait_for(runtime)
        fail[0]=True;time.sleep(.03);bad=wait_for(runtime,'error')
        assert bad['data']==good['data'] and bad['stale']
        assert bad['last_success']==good['last_success']
        assert 'must-not-leak' not in json.dumps(bad)


def test_timeout_is_nonblocking_does_not_overlap_and_close_is_bounded():
    gate=threading.Event();started=[]
    def slow(ctx):started.append(1);gate.wait(2);return {'late':True}
    runtime=CollectorRuntime([spec(slow,interval=.01,timeout=.03)])
    try:
        t=time.monotonic();runtime.tick();assert time.monotonic()-t<.1
        timed=wait_for(runtime,'error')
        assert timed['reason_code']=='timeout' and timed['data']=={}
        for _ in range(8):runtime.tick();time.sleep(.01)
        assert len(started)==1
        t=time.monotonic();runtime.close(timeout=.01);assert time.monotonic()-t<.15
    finally:gate.set();runtime.close()


def test_concurrency_is_limited_even_after_timeouts():
    gate=threading.Event();running=[]
    def slow(ctx):running.append(1);gate.wait(2);return {}
    specs=[CollectorSpec(str(i),'x','x',lambda ctx:Detection(),slow,timeout=.02) for i in range(5)]
    runtime=CollectorRuntime(specs,max_workers=2)
    try:
        for _ in range(12):runtime.tick();time.sleep(.01)
        assert len(running)==2
    finally:gate.set();runtime.close()


def test_configuration_validates_unknown_duplicate_and_invalid_intervals():
    with pytest.raises(ValueError):CollectorRuntime([spec(lambda c:{})],intervals={'missing':3})
    with pytest.raises(ValueError):CollectorRuntime([spec(lambda c:{})],intervals={'test':0})
    with pytest.raises(ValueError):CollectorRuntime([spec(lambda c:{}),spec(lambda c:{})])
    with pytest.raises(ValueError):CollectorRuntime([spec(lambda c:{})],max_workers=9)


def test_callback_failure_is_visible_without_exception_text():
    def broken(row):raise RuntimeError('token=never-export')
    with CollectorRuntime([spec(lambda c:{'x':1})],on_result=broken) as runtime:
        wait_for(runtime)
        eventually(lambda:runtime.snapshot()['collectors'][0]['persistence_error'])
        row=runtime.snapshot()['collectors'][0]
        assert row['persistence_error'] and 'never-export' not in json.dumps(row)


def test_blocked_persistence_never_blocks_tick_and_other_collector_timeouts():
    gate=threading.Event();started=threading.Event();collected=threading.Event();slow=threading.Event()
    def save(row):started.set();gate.wait(.3)
    def fast(ctx):collected.set();return {'real':1}
    def delayed(ctx):slow.wait(.5);return {'late':True}
    definitions=[spec(fast,interval=.01),CollectorSpec('second','Second','test',lambda c:Detection(),delayed,interval=.01,timeout=.02)]
    runtime=CollectorRuntime(definitions,on_result=save)
    try:
        runtime.tick();assert collected.wait(.3)
        end=time.monotonic()+.3
        while not started.is_set() and time.monotonic()<end:
            began=time.monotonic();runtime.tick();elapsed=time.monotonic()-began
            assert elapsed<.1, 'persistence callback blocked scheduling'
            time.sleep(.002)
        assert started.is_set()
        assert wait_for(runtime,'error','second')['reason_code']=='timeout'
        assert runtime.snapshot()['collectors'][0]['data']=={'real':1}
        began=time.monotonic();runtime.close(timeout=.01)
        assert time.monotonic()-began<.1
    finally:gate.set();slow.set();runtime.close()


def test_callback_queue_is_coalesced_and_pending_visible_with_one_daemon_worker():
    gate=threading.Event();started=threading.Event();saved=[];samples=[];calls=[]
    def save(row):
        saved.append(row);started.set();gate.wait(.5)
    def collect(ctx):calls.append(1);return {'version':len(calls)}
    runtime=CollectorRuntime([spec(collect,interval=.005)],on_result=save,on_sample=samples.append)
    try:
        wait_for(runtime);assert started.wait(.3)
        end=time.monotonic()+.1
        while time.monotonic()<end:runtime.tick();time.sleep(.003)
        view=runtime.snapshot()
        assert len(samples)>2 and len(saved)==1
        assert view['persistence']['queued']==1 and view['persistence']['capacity']==1
        assert view['collectors'][0]['persistence_pending']
        assert view['collectors'][0]['data']['version']==samples[-1]['data']['version']
        assert runtime._callback_thread.daemon
        gate.set()
        end=time.monotonic()+.3
        while time.monotonic()<end and runtime.snapshot()['collectors'][0]['persistence_pending']:time.sleep(.003)
        assert len(saved)==2 and saved[-1]['data']['version']==samples[-1]['data']['version']
        assert not runtime.snapshot()['collectors'][0]['persistence_pending']
    finally:gate.set();runtime.close()


def test_cache_callback_error_is_independent_of_persistence_and_live_data():
    saved=[]
    def cache(row):raise RuntimeError('secret=never-export')
    with CollectorRuntime([spec(lambda c:{'actual':7})],on_sample=cache,on_result=saved.append) as runtime:
        row=wait_for(runtime)
        eventually(lambda:bool(saved))
        assert row['data']=={'actual':7} and row['last_success']
        current=runtime.snapshot()['collectors'][0]
        assert current['sample_error'] and not current['persistence_error']
        assert 'never-export' not in json.dumps(current)


def test_interval_prevents_repeated_work_and_disabled_capabilities_keep_null_success():
    calls=[]
    with CollectorRuntime([spec(lambda ctx:calls.append(1) or {'x':1},interval=.5)]) as runtime:
        wait_for(runtime)
        for _ in range(5):runtime.tick()
        assert len(calls)==1
    unavailable=CollectorSpec('off','off','test',lambda ctx:Detection('unsupported','Hors Linux'),lambda ctx:{'fake':0})
    with CollectorRuntime([unavailable]) as runtime:
        row=wait_for(runtime,'unsupported','off')
        assert row['last_success'] is None and row['data']=={}


def test_oversize_or_nonfinite_results_are_errors_not_success():
    for data in ({'value':float('nan')},{'x':'a'*200}):
        with CollectorRuntime([spec(lambda ctx:data,limits={'max_bytes':30})]) as runtime:
            row=wait_for(runtime,'error')
            assert row['last_success'] is None and row['data']=={}


def test_late_success_cannot_clear_a_timeout_or_replace_last_good_value():
    gate=threading.Event();count=[0]
    def collect(ctx):
        count[0]+=1
        if count[0]>1:gate.wait(2)
        return {'version':count[0]}
    with CollectorRuntime([spec(collect,interval=.02,timeout=.03)]) as runtime:
        first=wait_for(runtime);time.sleep(.03)
        timed=wait_for(runtime,'error')
        gate.set();time.sleep(.01);runtime.tick()
        row=runtime.snapshot()['collectors'][0]
        assert row['status']=='error' and row['data']==first['data'] and row['stale']


def context(tmp_path, timeout=2):
    return CollectorContext(time.monotonic()+timeout,proc_root=tmp_path/'proc',sys_root=tmp_path/'sys')


def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)


def test_context_limits_file_size_and_rejects_deadline(tmp_path):
    c=context(tmp_path);write(tmp_path/'long','a'*30)
    with pytest.raises(OutputLimitExceeded):c.read_text(tmp_path/'long',max_bytes=10)
    with pytest.raises(TimeoutError):context(tmp_path,-1).read_text(tmp_path/'long')


def test_command_timeout_and_output_are_bounded_and_environment_is_not_inherited(tmp_path,monkeypatch):
    monkeypatch.setenv('MONITOR_DB','secret-value')
    c=context(tmp_path)
    out=c.run([sys.executable,'-c','import os;print(os.getenv("MONITOR_DB","absent"))'])
    assert out.strip()=='absent'
    with pytest.raises(OutputLimitExceeded):c.run([sys.executable,'-c','print("x"*100000)'],max_bytes=30)
    with pytest.raises(TimeoutError):c.run([sys.executable,'-c','import time;time.sleep(1)'],timeout=.03)


def test_pressure_reads_real_scopes_and_missing_data_is_not_zero(tmp_path):
    write(tmp_path/'proc/pressure/cpu','some avg10=2.20 avg60=1.10 avg300=0.10 total=200\n')
    write(tmp_path/'proc/pressure/memory','some avg10=0.00 avg60=0.20 avg300=0.30 total=300\nfull avg10=0.01 avg60=0.02 avg300=0.03 total=5\n')
    r=h.collect_pressure(context(tmp_path))
    assert r.data['resources']['cpu']['some']['avg10']==2.2
    assert 'full' not in r.data['resources']['cpu']
    assert r.data['resources']['io'] is None
    assert r.data['missing']==['io'] and r.data['partial']


def test_pressure_malformed_is_not_fabricated(tmp_path):
    write(tmp_path/'proc/pressure/cpu','some avg10=bad avg60=1 avg300=2 total=3')
    with pytest.raises(CollectorUnavailable):h.collect_pressure(context(tmp_path))


def test_cgroup_effective_limits_include_ancestors_and_distinguish_process_scope(tmp_path):
    write(tmp_path/'proc/self/cgroup','0::/service.slice/monitor.service\n')
    base=tmp_path/'sys/fs/cgroup'
    write(base/'cgroup.controllers','cpu memory io')
    write(base/'memory.max','max');write(base/'cpu.max','max 100000')
    write(base/'service.slice/memory.max','200000000');write(base/'service.slice/cpu.max','50000 100000')
    leaf=base/'service.slice/monitor.service'
    write(leaf/'memory.max','max');write(leaf/'memory.current','12000000');write(leaf/'cpu.max','max 100000');write(leaf/'pids.max','100');write(leaf/'pids.current','8')
    write(leaf/'cpuset.cpus.effective','0-1')
    write(leaf/'memory.events','oom 0\noom_kill 0\nhigh 12\n')
    r=h.collect_cgroup(context(tmp_path))
    assert r['scope']=='monitor_process' and r['version']==2
    assert r['memory']['effective_limit_bytes']==200000000
    assert r['cpu']['effective_limit_cores']==.5
    assert r['memory']['current_bytes']==12000000
    assert r['pids']['current']==8


def test_cgroup_does_not_escape_mount_root(tmp_path):
    write(tmp_path/'proc/self/cgroup','0::/../../private\n')
    write(tmp_path/'sys/fs/cgroup/cgroup.controllers','cpu')
    with pytest.raises(CollectorUnavailable):h.collect_cgroup(context(tmp_path))


def test_systemd_metadata_uses_only_allowlisted_properties(monkeypatch,tmp_path):
    calls=[]
    def run(self,args,**kw):
        calls.append(args)
        if 'list-units' in args:return json.dumps([{'unit':'ssh.service','active':'active','sub':'running','description':'password=never-read'}])
        return 'Id=ssh.service\nActiveState=active\nSubState=running\nUnitFileState=enabled\nMemoryCurrent=1000\nCPUUsageNSec=1000000000\nNRestarts=2\nRequires=network.target\n\n'
    monkeypatch.setattr(CollectorContext,'run',run)
    data=h.collect_service_metadata(context(tmp_path)).data
    assert data['services'][0]['memory_bytes']==1000
    assert data['services'][0]['restarts']==2
    assert 'never-read' not in json.dumps(data)
    assert all('Environment' not in arg and 'ExecStart' not in arg for args in calls for arg in args)


def test_timers_do_not_collect_command_lines(monkeypatch,tmp_path):
    monkeypatch.setattr(CollectorContext,'run',lambda self,args,**kw:json.dumps([{'unit':'backup.timer','activates':'backup.service','next':1800000000000000,'last':1790000000000000,'description':'token=hidden'}]))
    data=h.collect_timers(context(tmp_path)).data
    assert data['timers'][0]['unit']=='backup.timer'
    assert data['timers'][0]['next_at']==1800000000
    assert 'hidden' not in json.dumps(data)


def test_database_detection_does_not_assign_default_port_as_fact(monkeypatch,tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(h.psutil,'process_iter',lambda attrs:iter([SimpleNamespace(info={'pid':10,'name':'mariadbd'}),SimpleNamespace(info={'pid':11,'name':'redis-server'})]))
    sockets=[SimpleNamespace(laddr=SimpleNamespace(ip='127.0.0.1',port=3306),raddr=(),status='LISTEN',pid=None)]
    monkeypatch.setattr(h.psutil,'net_connections',lambda kind:sockets)
    data=h.collect_database_engines(context(tmp_path)).data
    maria=next(e for e in data['engines'] if e['engine']=='MariaDB')
    assert maria['processes'][0]['pid']==10 and maria['listeners']==[]
    assert data['unattributed_listeners'][0]['candidate_engine']=='MariaDB / MySQL'
    assert all(e['metrics_status']=='unavailable' for e in data['engines'])


def test_security_state_permission_failure_is_explicit(tmp_path):
    data=h.collect_security_state(context(tmp_path)).data
    assert data['apparmor']['status']=='unknown'
    assert data['selinux']['status']=='unknown'
    assert data['firewall']['rules_status']=='not_collected'


def test_service_inventory_includes_installed_inactive_units(monkeypatch,tmp_path):
    def run(self,args,**kw):
        if 'list-units' in args:return json.dumps([{'unit':'ssh.service'}])
        if 'list-unit-files' in args:return json.dumps([{'unit_file':'ssh.service','state':'enabled'},{'unit_file':'idle.service','state':'disabled'}])
        names=[name for name in ('ssh.service','idle.service') if name in args]
        return '\n\n'.join('Id='+name+'\nActiveState=inactive\nSubState=dead\nUnitFileState=disabled' for name in names)
    monkeypatch.setattr(CollectorContext,'run',run)
    data=h.collect_service_metadata(context(tmp_path)).data
    assert {s['name'] for s in data['services']}=={'ssh.service','idle.service'}


def test_database_unreadable_process_names_are_not_reported_as_complete(monkeypatch,tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(h.psutil,'process_iter',lambda attrs:iter([SimpleNamespace(info={'pid':10,'name':None})]))
    monkeypatch.setattr(h.psutil,'net_connections',lambda kind:[])
    result=h.collect_database_engines(context(tmp_path))
    assert result.data['partial'] and 'process_access' in result.data['limits']


def test_service_metadata_retains_template_inventory_if_show_cannot_load_it(monkeypatch,tmp_path):
    def run(self,args,**kw):
        if 'list-units' in args:return json.dumps([{'unit':'ssh.service'}])
        if 'list-unit-files' in args:return json.dumps([{'unit_file':'worker@.service','state':'disabled'}])
        if kw.get('check',True):raise CollectorUnavailable
        return 'Id=ssh.service\nActiveState=active\nSubState=running\nMemoryCurrent=1000\n'
    monkeypatch.setattr(CollectorContext,'run',run)
    data=h.collect_service_metadata(context(tmp_path)).data
    assert {s['name'] for s in data['services']}=={'ssh.service','worker@.service'}
    missing=next(s for s in data['services'] if s['name']=='worker@.service')
    assert missing['active'] is None and missing['enabled']=='disabled'
    assert data['partial']


def test_default_specs_are_unique_and_optional_dependencies_are_declared():
    specs=h.default_collectors()
    assert len({x.id for x in specs})==len(specs)==6
    assert all(x.permissions and x.schema and x.limits for x in specs)
