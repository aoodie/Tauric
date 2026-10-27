"""Reproducibly backport pip's private libraries and its venv bootstrap wheel.

Only the pinned upstream pip assembly on Python 3.13 is supported. Normal pip
and venv operations remain available; its deprecated pkg_resources backend is
removed in favour of the existing default importlib metadata backend.
"""

import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import re
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

INPUTS = {
    'msgpack-1.2.1.tar.gz': '04c721c2c7448767e9e3f2520a475663d8ee0f09c31890f6d2bd70fd636a9647',
    'urllib3-2.8.0-py3-none-any.whl': '0cf3cae568d36aa9576b28dfb35f11328f1cb974ca7647d9475ebb86c75ac6e3',
}


def sources(folder):
    for name, checksum in INPUTS.items():
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != checksum:
            raise ValueError('Python tooling backport input checksum differs.')
    result = {}
    with tarfile.open(folder / 'msgpack-1.2.1.tar.gz') as archive:
        for member in archive.getmembers():
            prefix = 'msgpack-1.2.1/msgpack/'
            if member.name.startswith(prefix) and member.name.endswith(('.py', '.pyi')):
                if not member.isfile() or '..' in PurePosixPath(member.name).parts:
                    raise ValueError('Tooling source must contain regular package files.')
                result['msgpack/' + member.name.removeprefix(prefix)] = archive.extractfile(member).read()
    with zipfile.ZipFile(folder / 'urllib3-2.8.0-py3-none-any.whl') as archive:
        for name in archive.namelist():
            if name.startswith('urllib3/') and not name.endswith('/'):
                if '..' in PurePosixPath(name).parts:
                    raise ValueError('Tooling source cannot escape its package.')
                result[name] = archive.read(name)
    if not {'msgpack/__init__.py', 'msgpack/fallback.py', 'urllib3/__init__.py'} <= result.keys():
        raise ValueError('The Python tooling source inventory is incomplete.')
    return result


def record(rows):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    for name, data in sorted(rows.items()):
        checksum = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        writer.writerow([name, 'sha256=' + checksum, len(data)])
    return stream.getvalue()


def patch(package, metadata, bootstrap, folder, output):
    replacements = sources(folder)  # Validate every input before mutation.
    vendors = package / '_vendor'
    before = (vendors / 'vendor.txt').read_text()
    expected = {'msgpack': '1.1.2', 'urllib3': '2.7.0', 'setuptools': '70.3.0'}
    for name, version in expected.items():
        if len(re.findall(r'^\s*' + name + '==' + re.escape(version) + r'$', before, re.MULTILINE)) != 1:
            raise ValueError('Unexpected pip private dependency identity; review before patching.')
    backend = package / '_internal/metadata/__init__.py'
    code = backend.read_text()
    if code.count('if sys.version_info >= (3, 14):') != 1:
        raise ValueError('The upstream pip metadata selection changed.')
    bom_path = vendors / 'bom.cdx.json'
    bom_original = bom_path.read_bytes()
    bom = json.loads(bom_original)
    identities = {x['name']: x['version'] for x in bom['components'] if x['name'] in expected}
    if identities != expected:
        raise ValueError('The upstream pip component inventory changed.')
    with zipfile.ZipFile(bootstrap) as archive:
        original = {name: archive.read(name) for name in archive.namelist() if not name.endswith('/')}
    if 'pip-26.2.2.dist-info/METADATA' not in original:
        raise ValueError('The original venv bootstrap wheel identity changed.')
    for name in ('msgpack', 'urllib3', 'pkg_resources'):
        shutil.rmtree(vendors / name)
    for name, data in replacements.items():
        target = vendors / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    updated = re.sub(r'^\s*setuptools==70\.3\.0\n', '', before, flags=re.MULTILINE)
    updated = updated.replace('msgpack==1.1.2', 'msgpack==1.2.1').replace('urllib3==2.7.0', 'urllib3==2.8.0')
    (vendors / 'vendor.txt').write_text(updated)
    # The upstream component inventory describes the bundled code. Keep it
    # truthful when backporting code, including the wheel used by ensurepip.
    changed = {'pkg:pypi/msgpack@1.1.2': 'pkg:pypi/msgpack@1.2.1',
               'pkg:pypi/urllib3@2.7.0': 'pkg:pypi/urllib3@2.8.0'}
    removed = 'pkg:pypi/setuptools@70.3.0'
    bom['components'] = [x for x in bom['components'] if x.get('bom-ref') != removed]
    for component in bom['components']:
        if component.get('bom-ref') in changed:
            reference = changed[component['bom-ref']]
            component.update({'bom-ref': reference, 'purl': reference, 'version': reference.rsplit('@', 1)[1]})
    dependencies = []
    for dependency in bom['dependencies']:
        if dependency['ref'] == removed:
            continue
        dependency['ref'] = changed.get(dependency['ref'], dependency['ref'])
        if 'dependsOn' in dependency:
            dependency['dependsOn'] = [changed.get(x, x) for x in dependency['dependsOn'] if x != removed]
        dependencies.append(dependency)
    bom['dependencies'] = dependencies
    bom['metadata'].setdefault('tools', []).append({'name': 'tauric-python-tooling-backports', 'version': '1'})
    bom['version'] += 1
    bom_path.write_text(json.dumps(bom, indent=2) + '\n')
    backend.write_text(code.replace('if sys.version_info >= (3, 14):', 'if sys.version_info >= (3, 11):'))
    if (backend.parent / '__pycache__').exists():
        shutil.rmtree(backend.parent / '__pycache__')
    files = {'pip/' + p.relative_to(package).as_posix(): p.read_bytes() for p in package.rglob('*')
             if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    # Preserve metadata and script entries while replacing installed package rows.
    entries = list(csv.reader((metadata / 'RECORD').read_text().splitlines()))
    kept = [row for row in entries if not row[0].startswith('pip/')]
    (metadata / 'RECORD').write_text(record(files) + ''.join(','.join(row) + '\n' for row in kept))
    wheel_rows = {name: data for name, data in original.items() if not name.startswith('pip/') and not name.endswith('/RECORD')}
    wheel_rows.update(files)
    wheel_record = 'pip-26.2.2.dist-info/RECORD'
    wheel_rows[wheel_record] = (record(wheel_rows) + wheel_record + ',,\n').encode()
    pending = bootstrap.with_suffix('.patched')
    with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(wheel_rows.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 27, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    pending.replace(bootstrap)
    value = {'upstream_pip': '26.2.2', 'assembly': 'tauric_python_tooling_backports_v1',
             'upstream_vendor_bom_sha256': hashlib.sha256(bom_original).hexdigest(),
             'inputs': INPUTS, 'msgpack': '1.2.1 (pure Python)', 'urllib3': '2.8.0',
             'removed_deprecated_backend': 'pkg_resources', 'metadata_backend': 'importlib',
             'package_sha256': hashlib.sha256(json.dumps({n: hashlib.sha256(d).hexdigest() for n, d in files.items()}, sort_keys=True).encode()).hexdigest(),
             'bootstrap_wheel_sha256': hashlib.sha256(bootstrap.read_bytes()).hexdigest()}
    output.write_text(json.dumps(value, indent=2) + '\n')
    return value


def main():
    if sys.version_info[:2] != (3, 13) or importlib.metadata.version('pip') != '26.2.2':
        raise ValueError('Only the reviewed Python 3.13 / pip 26.2.2 assembly can be patched.')
    import ensurepip

    import pip

    package = Path(pip.__file__).parent
    metadata = package.with_name('pip-26.2.2.dist-info')
    bootstrap = Path(ensurepip.__file__).parent / '_bundled/pip-26.2.2-py3-none-any.whl'
    print(json.dumps(patch(package, metadata, bootstrap, Path('/tooling-inputs'), Path('/python-tooling-assembly.json'))))


if __name__ == '__main__':
    main()
