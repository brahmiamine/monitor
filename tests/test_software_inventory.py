"""Read-only package adapters with injected commands; no package manager is changed."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from software_inventory import collect_packages, parse_dpkg, parse_rpm


DPKG = 'bash\t5.2-1\t1500\tinstall ok installed\nlibfoo:amd64\t1.0\t20\tinstall ok installed\nremoved\t1\t5\tdeinstall ok config-files\n'
RPM = 'bash\t(none)\t5.2.26\t1.el9\tx86_64\t1536\nopenssl-libs\t1\t3.2.2\t6.fc41\tx86_64\t4096\npython3\t0\t3.13.2\t1.fc41\taarch64\t0\n'


def fake_tools(outputs, failed=()):
    calls = []
    def which(name): return '/usr/bin/'+name if name in outputs or name in failed else None
    def run(args, timeout=8):
        calls.append((args,timeout))
        name = Path(args[0]).name
        if name in failed: raise RuntimeError('must-not-leak-token=PRIVATE')
        return outputs[name]
    return run,which,calls


def test_dpkg_parser_remains_backward_compatible():
    rows = parse_dpkg(DPKG, manual={'bash'}, upgradable={'bash'})
    assert [r['name'] for r in rows] == ['bash','libfoo:amd64']
    assert rows[0] == {'name':'bash','version':'5.2-1','size_kb':1500,'manual':True,'upgradable':True}
    assert rows[1]['manual'] is False


def test_dpkg_collects_without_optional_apt_mark_and_updates_are_unknown():
    run,which,calls = fake_tools({'dpkg-query':DPKG})
    result = collect_packages(run,which)
    assert result['manager'] == 'dpkg' and result['error'] is None
    assert len(result['packages']) == 2
    assert all(r['manual'] is None and r['upgradable'] is None for r in result['packages'])
    assert result['updates_status'] == 'unavailable'
    assert len(calls) == 1


def test_optional_mark_failure_does_not_hide_packages_or_leak_errors():
    run,which,calls = fake_tools({'dpkg-query':DPKG},failed={'apt-mark'})
    result = collect_packages(run,which,upgradable={'bash'})
    assert len(result['packages']) == 2 and result['error'] is None
    assert result['packages'][0]['upgradable'] is True
    assert result['updates_status'] == 'available'
    assert 'PRIVATE' not in str(result)


def test_cached_update_names_are_single_authority_without_another_apt_query():
    run,which,calls = fake_tools({'dpkg-query':DPKG,'apt-mark':'bash\n','apt':'DO NOT RUN'})
    result = collect_packages(run,which,upgradable=set())
    assert result['updates_status'] == 'available'
    assert result['packages'][0]['manual'] is True
    assert all(r['upgradable'] is False for r in result['packages'])
    assert all(Path(command[0]).name != 'apt' for command,_ in calls)


def test_rpm_parser_preserves_evr_architecture_size_and_unknown_flags():
    rows = parse_rpm(RPM)
    assert rows[0] == {'name':'bash.x86_64','version':'5.2.26-1.el9','size_kb':1.5,'manual':None,'upgradable':None}
    assert rows[1]['version'] == '1:3.2.2-6.fc41'
    assert rows[2]['version'] == '3.13.2-1.fc41'
    assert rows[2]['size_kb'] == 0


def test_rpm_adapter_is_a_local_query_only_and_no_update_probe():
    run,which,calls = fake_tools({'rpm':RPM,'dnf':'DO NOT RUN'})
    result = collect_packages(run,which)
    assert result['manager'] == 'rpm' and result['error'] is None
    assert result['updates_status'] == 'unsupported'
    assert len(calls) == 1
    assert calls[0][0][1:4] == ['--query','--all','--queryformat']
    assert calls[0][1] <= 20


def test_missing_tools_are_explicit_and_do_not_run_commands():
    run,which,calls = fake_tools({})
    result = collect_packages(run,which)
    assert result['manager'] is None and result['packages'] == [] and result['error']
    assert result['updates_status'] == 'unsupported' and calls == []


def test_failed_inventory_is_unknown_not_an_empty_success():
    run,which,_ = fake_tools({},failed={'dpkg-query'})
    result = collect_packages(run,which)
    assert result['manager'] == 'dpkg' and result['error']
    assert 'PRIVATE' not in str(result)


def test_malformed_rows_do_not_invent_sizes_or_hide_valid_entries():
    assert len(parse_dpkg(DPKG+'bad row\n')) == 2
    assert parse_dpkg('unknown\t1\t\tinstall ok installed\n')[0]['size_kb'] is None
    assert parse_rpm('invalid\n'+RPM) == parse_rpm(RPM)
    assert parse_rpm('bash\t0\t1\t2\tx86_64\tbad\n')[0]['size_kb'] is None


def test_parser_resource_bounds():
    with pytest.raises(ValueError): parse_dpkg('x'*(4*1024*1024+1))
    with pytest.raises(ValueError): parse_rpm('x'*(4*1024*1024+1))
