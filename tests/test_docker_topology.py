"""Docker topology never reads environment, bind source paths or arbitrary labels."""
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collector_runtime import CollectorUnavailable, OutputLimitExceeded
from docker_topology import collect_topology, make_spec, INSPECT_FORMAT


ID='a'*64


def row(**overrides):
    return {'id':ID,'name':'/web','image':'example/web:1','compose_project':'site','compose_service':'web',
            'networks':['frontend','storage'],'volumes':[{'name':'uploads','type':'volume'}],
            'ports':{'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':'8000'}]},**overrides}


def runner(data=None,listing=None):
    calls=[]
    def run(args,timeout):
        calls.append(args)
        if args[1]=='ps':return listing if listing is not None else ID+'\n'
        return '\n'.join(json.dumps(value) for value in (data if data is not None else [row()]))
    return run,calls


def test_allowlist_commands_and_only_observed_resource_links():
    run,calls=runner();result=collect_topology(run)
    assert calls[0]==['docker','ps','-aq','--no-trunc']
    assert calls[1][:4]==['docker','inspect','--format',INSPECT_FORMAT]
    assert '.Config.Env' not in INSPECT_FORMAT and '.Args' not in INSPECT_FORMAT
    assert '.HostConfig' not in INSPECT_FORMAT and '.Source' not in INSPECT_FORMAT
    assert 'json .Config.Labels' not in INSPECT_FORMAT
    assert 'index .Config.Labels "com.docker.compose.project"' in INSPECT_FORMAT
    assert 'index .Config.Labels "com.docker.compose.service"' in INSPECT_FORMAT
    container=result['containers'][0]
    assert container['name']=='web' and container['compose_project']=='site'
    assert container['ports']==[{'container_port':8080,'protocol':'tcp','host_address':'127.0.0.1','host_port':8000}]
    assert len(result['relations'])==3
    assert {r['target_type'] for r in result['relations']}=={'network','volume'}
    assert all(r['confidence']=='observed' and r['source_type']=='container' for r in result['relations'])
    assert not any(r['target_type']=='database' for r in result['relations'])


def test_bind_mounts_extra_fields_and_arbitrary_labels_are_not_exported():
    data=row(volumes=[{'type':'bind','name':'private','source':'/private/passwords','destination':'/run/secrets'},
                      {'type':'volume','name':'uploads','source':'/var/lib/docker/private','password':'hidden'}],
             env=['TOKEN=hidden'],labels={'token':'hidden'},args=['secret-argument'])
    run,_=runner([data]);text=json.dumps(collect_topology(run))
    assert all(secret not in text for secret in ('private','secrets','hidden','TOKEN','secret-argument'))


def test_empty_daemon_is_available_without_inventing_resources():
    run,calls=runner(listing='')
    result=collect_topology(run)
    assert result['containers']==[] and result['relations']==[]
    assert len(calls)==1 and not result['partial']


def test_command_failure_is_not_converted_to_empty_success():
    def fail(args,timeout):raise PermissionError('password=not-exported')
    with pytest.raises(CollectorUnavailable) as exc:collect_topology(fail)
    assert 'not-exported' not in str(exc.value)


def test_invalid_ids_are_never_passed_to_inspect():
    run,calls=runner(listing='--help\n../../private\n'+ID+'\n')
    result=collect_topology(run)
    assert calls[1][4:]==[ID]
    assert result['partial'] and result['invalid_records']==2


def test_missing_inspect_rows_and_foreign_ids_are_partial_not_fabricated():
    run,_=runner([row(id='b'*64)],listing=ID+'\n')
    result=collect_topology(run)
    assert result['containers']==[] and result['partial']


def test_containers_relations_and_output_have_hard_limits():
    ids=[f'{i:064x}' for i in range(205)]
    data=[row(id=ident,name='/web'+str(i),networks=['net'+str(j) for j in range(12)]) for i,ident in enumerate(ids[:200])]
    run,calls=runner(data,listing='\n'.join(ids))
    result=collect_topology(run)
    assert len(calls[1][4:])==200
    assert len(result['containers'])<=200 and len(result['relations'])<=1000
    assert len(json.dumps(result,ensure_ascii=False).encode())<=256*1024
    assert result['partial'] and result['truncated']


def test_raw_output_limit_is_rejected_before_json_parsing():
    run,_=runner(listing='x'*(256*1024+1))
    with pytest.raises(OutputLimitExceeded):collect_topology(run)


def test_malformed_json_is_not_an_empty_healthy_topology():
    def run(args,timeout):return ID if args[1]=='ps' else 'broken-json'
    with pytest.raises(CollectorUnavailable):collect_topology(run)


def test_spec_declares_readonly_capability_and_limits():
    spec=make_spec()
    assert spec.id=='docker_topology' and spec.category=='topology'
    assert spec.dependencies==('docker',) and spec.permissions
    assert spec.limits['max_bytes']==256*1024 and spec.limits['max_relations']==1000
