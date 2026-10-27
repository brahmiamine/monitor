"""Portable read-only package inventory using detected local package databases.

No package installation, metadata refresh, repository access or privilege change.
The caller supplies a bounded ``run(args, timeout)`` and ``which(command)``.
``parse_dpkg`` is also the legacy engine parser's single implementation.
"""
import re

MAX_OUTPUT = 4 * 1024 * 1024
MAX_PACKAGES = 50000
DPKG_FORMAT = '${binary:Package}\t${Version}\t${Installed-Size}\t${Status}\n'
RPM_FORMAT = '%{NAME}\t%{EPOCH}\t%{VERSION}\t%{RELEASE}\t%{ARCH}\t%{SIZE}\n'
_PACKAGE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9+_.:-]{0,255}$')


def _lines(output):
    if not isinstance(output, str) or len(output.encode('utf-8')) > MAX_OUTPUT:
        raise ValueError('Inventaire des paquets trop volumineux ou invalide')
    lines = output.splitlines()
    if len(lines) > MAX_PACKAGES:
        raise ValueError('Inventaire des paquets trop volumineux')
    return lines


def _size(value, divisor=1):
    try:
        size = int(value)
        return (size if divisor == 1 else size/divisor) if size >= 0 else None
    except (ValueError, TypeError):
        return None


def _known(name, names):
    """None is unknown; an empty confirmed set is a known negative result."""
    return None if names is None else name in names or name.split(':')[0] in names


def parse_dpkg(output, manual=(), upgradable=()):
    """Parse dpkg-query output; preserve the existing engine parser's interface.

    Optional classifications accept None to mean the information is unavailable,
    rather than treating every package as automatically installed or up-to-date.
    """
    packages = []
    for line in _lines(output):
        parts = line.split('\t')
        if len(parts) != 4 or parts[3].split()[-2:] != ['ok','installed']:
            continue
        name,version,size,_ = parts
        if not _PACKAGE.fullmatch(name) or not version or len(version) > 256:
            continue
        packages.append({'name':name,'version':version,'size_kb':_size(size),
                         'manual':_known(name,manual),'upgradable':_known(name,upgradable)})
    return sorted(packages,key=lambda package:(package['name'],package['version']))


def parse_rpm(output):
    """Parse NAME/EPOCH/VERSION/RELEASE/ARCH/SIZE from a local rpm query.

    SIZE is bytes (RPM header SIZE), converted exactly to KiB. EPOCH=0 or
    ``(none)`` is omitted from the displayed version; no version is inferred.
    """
    packages = []
    for line in _lines(output):
        parts = line.split('\t')
        if len(parts) != 6:
            continue
        name,epoch,version,release,arch,size = parts
        if not _PACKAGE.fullmatch(name) or not _PACKAGE.fullmatch(arch) or not version or not release:
            continue
        if len(version) > 128 or len(release) > 128:
            continue
        if epoch not in ('', '(none)', '(null)', '0'):
            if not epoch.isdigit() or len(epoch) > 12:
                continue
            prefix = str(int(epoch))+':' if int(epoch) else ''
        else:
            prefix = ''
        packages.append({'name':name+'.'+arch,'version':prefix+version+'-'+release,
                         'size_kb':_size(size,1024),'manual':None,'upgradable':None})
    return sorted(packages,key=lambda package:(package['name'],package['version']))


def collect_packages(run, which, *, upgradable=None):
    """Detect dpkg/RPM and query only installed-package metadata.

    ``upgradable`` optionally carries dpkg package names from the engine's
    existing cached update check. This adapter never runs ``apt list`` or dnf.
    A provided empty set is known (available); None is unavailable. RPM has no
    cached-updates adapter in this lot, so both classifications remain unknown.
    """
    result = {'packages':[],'manager':None,'error':None,
              'updates_status':'unsupported','notes':[]}
    dpkg = which('dpkg-query')
    rpm = which('rpm') if not dpkg else None
    if dpkg:
        result['manager'] = 'dpkg'
        result['updates_status'] = 'available' if upgradable is not None else 'unavailable'
        manual = None
        mark = which('apt-mark')
        if mark:
            try:
                manual = set(_lines(run([mark,'showmanual'],10)))
            except Exception:
                result['notes'].append('Origine manuel/dépendance non accessible ; paquets conservés.')
        else:
            result['notes'].append('apt-mark absent : origine manuel/dépendance inconnue.')
        try:
            result['packages'] = parse_dpkg(run([dpkg,'-W','-f',DPKG_FORMAT],20),manual,upgradable)
        except Exception:
            result['error'] = 'Inventaire dpkg indisponible : permissions, délai ou base locale.'
        if upgradable is None:
            result['notes'].append('Mises à jour non vérifiées : aucune liste locale récente fournie.')
    elif rpm:
        result['manager'] = 'rpm'
        try:
            result['packages'] = parse_rpm(run([rpm,'--query','--all','--queryformat',RPM_FORMAT],20))
        except Exception:
            result['error'] = 'Inventaire RPM indisponible : permissions, délai ou base locale.'
        result['notes'].append('RPM : origine manuel/dépendance inconnue ; mises à jour non évaluées sans adaptateur de cache local.')
    else:
        result['error'] = 'Inventaire des paquets non pris en charge : dpkg-query et rpm non détectés.'
    return result
