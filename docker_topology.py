"""Observed Docker membership only, with a formatted inspection allowlist.

No container environment, command, arbitrary label or host bind path is requested.
Sharing a network is never presented as an application/database dependency.
"""
from __future__ import annotations

import ipaddress
import json
import re
import shutil
import subprocess
import time

from collector_runtime import CollectorSpec, CollectionResult, Detection, CollectorUnavailable, OutputLimitExceeded

MAX_BYTES=256*1024
MAX_CONTAINERS=200
MAX_RELATIONS=1000
MAX_MEMBERSHIPS=64
IDENT=re.compile(r'^[0-9a-f]{64}$')
NAME=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,239}$')
IMAGE=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,239}(?:@sha256:[0-9a-fA-F]{64})?$')
PORT=re.compile(r'^(\d{1,5})/(tcp|udp|sctp)$')

# Map keys and Mount.Name are selected in the Go template, so sensitive values
# are absent before JSON even reaches Python. Bind mounts are entirely omitted.
INSPECT_FORMAT=(
    '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Config.Image}},'
    '"compose_project":{{json (index .Config.Labels "com.docker.compose.project")}},'
    '"compose_service":{{json (index .Config.Labels "com.docker.compose.service")}},'
    '"networks":[{{$first := true}}{{range $name, $_ := .NetworkSettings.Networks}}'
    '{{if not $first}},{{end}}{{json $name}}{{$first = false}}{{end}}],'
    '"volumes":[{{$first := true}}{{range .Mounts}}{{if eq .Type "volume"}}'
    '{{if not $first}},{{end}}{"type":"volume","name":{{json .Name}}}{{$first = false}}{{end}}{{end}}],'
    '"ports":{{json .NetworkSettings.Ports}}}'
)


def _name(value):
    return value if isinstance(value,str) and NAME.fullmatch(value) else None


def _port(value):
    if isinstance(value,bool):return None
    try:
        number=int(value)
        return number if 1<=number<=65535 else None
    except (ValueError,TypeError):return None


def _encoded(value):return json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')


def _run(run,args,timeout):
    try:output=run(args,timeout=timeout)
    except (TimeoutError,subprocess.TimeoutExpired):raise TimeoutError from None
    except OutputLimitExceeded:raise
    except Exception:raise CollectorUnavailable from None
    if not isinstance(output,str):raise CollectorUnavailable
    if len(output.encode('utf-8'))>MAX_BYTES:raise OutputLimitExceeded
    return output


def _published_ports(value):
    if value is None:return [],0
    if not isinstance(value,dict):return [],1
    ports=[];invalid=0
    for key,bindings in list(value.items())[:128]:
        match=PORT.fullmatch(key) if isinstance(key,str) else None
        if not match or not _port(match[1]):invalid+=1;continue
        if bindings is None:continue
        if not isinstance(bindings,list):invalid+=1;continue
        for binding in bindings[:16]:
            if not isinstance(binding,dict):invalid+=1;continue
            host_port=_port(binding.get('HostPort'));host=binding.get('HostIp')
            if not host_port or not isinstance(host,str):invalid+=1;continue
            try:ipaddress.ip_address(host)
            except ValueError:invalid+=1;continue
            ports.append({'container_port':int(match[1]),'protocol':match[2],
                          'host_address':host,'host_port':host_port})
        if len(bindings)>16:invalid+=len(bindings)-16
    if len(value)>128:invalid+=len(value)-128
    # Stable ordering avoids inventory changes caused only by Docker map order.
    ports.sort(key=lambda item:(item['container_port'],item['protocol'],item['host_address'],item['host_port']))
    return ports,invalid


def _container(raw):
    if not isinstance(raw,dict) or not isinstance(raw.get('id'),str) or not IDENT.fullmatch(raw['id']):raise CollectorUnavailable
    invalid=0
    name=_name(raw.get('name','').removeprefix('/')) if isinstance(raw.get('name'),str) else None
    image=raw.get('image');image=image if isinstance(image,str) and IMAGE.fullmatch(image) else None
    networks=raw.get('networks') or []
    if not isinstance(networks,list):networks=[];invalid+=1
    clean_networks=sorted(set(network_name for value in networks if (network_name:=_name(value))))[:MAX_MEMBERSHIPS]
    invalid+=len(networks)-len(clean_networks)
    volumes=raw.get('volumes') or []
    if not isinstance(volumes,list):volumes=[];invalid+=1
    clean_volumes=sorted(set(value['name'] for value in volumes if isinstance(value,dict) and value.get('type')=='volume' and _name(value.get('name'))))[:MAX_MEMBERSHIPS]
    # Bind mounts are intentionally not part of this inventory, even if a test or
    # unexpected daemon response provides one. Neither endpoint is retained.
    invalid+=sum(1 for value in volumes if not isinstance(value,dict) or (value.get('type')=='volume' and not _name(value.get('name'))))
    if len(volumes)>MAX_MEMBERSHIPS:invalid+=len(volumes)-MAX_MEMBERSHIPS
    ports,bad_ports=_published_ports(raw.get('ports'));invalid+=bad_ports
    return {'id':raw['id'],'name':name,'image':image,'compose_project':_name(raw.get('compose_project')),
            'compose_service':_name(raw.get('compose_service')),'networks':clean_networks,
            'volumes':[{'name':name,'type':'volume'} for name in clean_volumes], 'ports':ports},invalid


def collect_topology(run):
    """Run two read-only commands; injected run(args, timeout=...) must be bounded.

    The native specification uses CollectorContext.run (bounded subprocess output).
    A custom runner is useful for an existing Docker endpoint or deterministic
    tests; its returned output is independently capped before parsing.
    """
    end=time.monotonic()+8
    listing=_run(run,['docker','ps','-aq','--no-trunc'],timeout=3)
    all_ids=[value.strip() for value in listing.splitlines() if value.strip()]
    invalid=sum(not bool(IDENT.fullmatch(value)) for value in all_ids)
    ids=list(dict.fromkeys(value for value in all_ids if IDENT.fullmatch(value)))
    truncated=len(ids)>MAX_CONTAINERS
    selected=ids[:MAX_CONTAINERS]
    result={'containers':[],'relations':[],'partial':bool(invalid or truncated),'truncated':truncated,
            'invalid_records':invalid,'requested_containers':len(selected),'source':'docker_inspect_allowlist',
            'scope':'container_network_and_named_volume_membership',
            'dependency_inference':False}
    if not selected:return result
    remaining=end-time.monotonic()
    if remaining<=0:raise TimeoutError
    output=_run(run,['docker','inspect','--format',INSPECT_FORMAT,*selected],timeout=remaining)
    inspected={}
    for line in output.splitlines():
        if not line.strip():continue
        try:raw=json.loads(line)
        except (ValueError,TypeError):raise CollectorUnavailable from None
        container,bad=_container(raw)
        result['invalid_records']+=bad
        if container['id'] not in selected or container['id'] in inspected:
            result['invalid_records']+=1;continue
        inspected[container['id']]=container
    if len(inspected)!=len(selected):result['partial']=True
    budget=2048 # Header/formatting reserve, including uncertainty indicators.
    for ident in selected:
        container=inspected.get(ident)
        if container is None:continue
        container_cost=len(_encoded(container))+2
        if budget+container_cost>MAX_BYTES:
            result['truncated']=True;continue
        budget+=container_cost;result['containers'].append(container)
        links=[('network',name,'attached_to','docker_inspect_network_membership') for name in container['networks']]
        links += [('volume',volume['name'],'mounts','docker_inspect_volume_mount') for volume in container['volumes']]
        for kind,target,relation_kind,evidence in links:
            relation={'source_id':ident,'source_type':'container','target_id':target,'target_type':kind,
                      'kind':relation_kind,'evidence':evidence,'confidence':'observed'}
            relation_cost=len(_encoded(relation))+2
            if len(result['relations'])>=MAX_RELATIONS or budget+relation_cost>MAX_BYTES:
                result['truncated']=True;continue
            budget+=relation_cost;result['relations'].append(relation)
    result['partial']=result['partial'] or result['truncated'] or bool(result['invalid_records'])
    if len(_encoded(result))>MAX_BYTES:raise OutputLimitExceeded
    return result


def make_spec(run=None):
    def detect(ctx):
        return Detection() if shutil.which('docker') else Detection('unavailable','Client Docker absent du chemin du service.')
    def collect(ctx):
        def bounded(args,timeout):
            remaining=min(timeout,ctx.remaining())
            return run(args,timeout=remaining) if run else ctx.run(args,timeout=remaining,max_bytes=MAX_BYTES)
        result=collect_topology(bounded)
        return CollectionResult(result,reason='Inventaire partiel ou limité ; certaines ressources ont disparu ou dépassent les limites.' if result['partial'] else None)
    return CollectorSpec('docker_topology','Réseaux et volumes des conteneurs','topology',detect,collect,
        dependencies=('docker',),permissions=('API Docker en lecture seule : liste et inspect formaté des conteneurs',),
        interval=120,timeout=10,limits={'max_bytes':MAX_BYTES,'max_items':MAX_CONTAINERS,'max_relations':MAX_RELATIONS},
        schema={'containers':'[{id,name,image,compose_project,compose_service,networks,volumes,ports}]',
                'relations':'[{source_id,source_type,target_id,target_type,kind,evidence,confidence}]',
                'partial':'bool','truncated':'bool','scope':'container_network_and_named_volume_membership'})
