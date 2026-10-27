"""Bind already installed research runtimes to verified release source.

Only source is copied from the immutable engineering image; no container is
started, no dependencies are installed and no model or broker action is made.
"""

import hashlib
import json
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/srv/tauric-quant')
SYSTEMD = Path('/etc/systemd/system')
UNITS = ('tauric-quant-lean.service', 'tauric-quant-qlib.service')
PATHS = ('backend/tauric', 'backend/runners', 'backend/requirements.lock', 'backend/requirements.qlib.lock')
UTC = timezone.utc
IDLE = "from sqlalchemy import text;from tauric.database import engine;\nwith engine().connect() as d:\n d.execute(text('SET TRANSACTION READ ONLY'));print(d.scalar(text(\"SELECT count(*) FROM quant_records WHERE kind='quant_job' AND payload->>'status' IN ('queued','running')\")))"


def run(args):
    return subprocess.run(args, check=True, capture_output=True, timeout=120).stdout


def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def idle(server, compose, *, frozen=False):
    if not (ROOT / 'control/config.json').exists():
        return True
    action = ['run', '--rm', '--no-deps', '-T'] if frozen else ['exec', '-T']
    return server.run(compose + action + ['api', 'python', '-c', IDLE], stdout=subprocess.PIPE).stdout.strip() == b'0'


def active_units():
    result = []
    for unit in UNITS:
        status = run(['systemctl', 'show', unit, '--property=ActiveState', '--value']).decode().strip()
        if status == 'failed':
            raise ValueError('Recover the existing failed research worker before installing another source.')
        if status in {'active', 'activating'}:
            result.append(unit)
    return result


def source_files(tree):
    result = {}
    for row in tree:
        name = row['path']
        if row['type'] == 'blob' and any(name == p or name.startswith(p + '/') for p in PATHS):
            if row.get('mode') not in {'100644', '100755'} or '..' in Path(name).parts or Path(name).is_absolute():
                raise ValueError('Research source must contain only regular repository files.')
            result[name] = row['sha']
    required = {'backend/tauric/quant_worker.py', 'backend/runners/qlib_runner.py', *PATHS[2:]}
    if not required <= result.keys():
        raise ValueError('The checked research source tree is incomplete.')
    return result


def install_source(staging, destination, expected):
    actual = {}
    for path in staging.rglob('*'):
        if path.is_symlink():
            raise ValueError('Research image source contains a symbolic link.')
        if path.is_file():
            name = path.relative_to(staging).as_posix()
            data = path.read_bytes()
            if name not in expected or blob(data) != expected[name]:
                raise ValueError('Research image source differs from the checked repository tree.')
            actual[name] = hashlib.sha256(data).hexdigest()
    if set(actual) != set(expected):
        raise ValueError('Research image source is incomplete.')
    if destination.is_symlink():
        raise ValueError('The immutable research source destination cannot be a symbolic link.')
    destination.mkdir(parents=True, exist_ok=True, mode=0o755)
    for name, digest in actual.items():
        target = destination / name
        for parent in target.parents:
            if parent == destination:
                break
            if parent.is_symlink():
                raise ValueError('Research source parent is a symbolic link.')
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError('The original immutable research source changed.')
            continue
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        pending = target.with_name(target.name + '.release.pending')
        if not pending.exists():
            with pending.open('xb') as output:
                output.write((staging / name).read_bytes())
            pending.chmod(0o644)
        if pending.is_symlink() or hashlib.sha256(pending.read_bytes()).hexdigest() != digest:
            raise ValueError('Interrupted research source copy needs reconciliation.')
        pending.replace(target)
    verify_inventory(destination, actual)
    return actual


def verify_inventory(root, inventory):
    files = {}
    if root.is_symlink():
        raise ValueError('Immutable research source root changed to a symbolic link.')
    for path in root.rglob('*'):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError('Immutable research source contains an unsafe entry.')
        if path.is_file():
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if files != inventory:
        raise ValueError('Immutable research source inventory changed.')


def prepare(candidate, tree, previous, *, units=None):
    path = ROOT / 'control/config.json'
    if path.is_symlink() or (ROOT / 'control').is_symlink() or (ROOT / 'source').is_symlink():
        raise ValueError('Research control configuration cannot be a symbolic link.')
    if not path.exists():
        return None
    original = json.loads(path.read_text())
    revision = json.loads((candidate / 'release-manifest.json').read_text())['revision']
    if not re.fullmatch('[a-f0-9]{40}', revision) or original['source_revision'] != previous:
        raise ValueError('The original research source identity changed.')
    expected = source_files(tree)
    helper = next((r['sha'] for r in tree if r['path'] == 'scripts/quant_host.py' and r['type'] == 'blob'), None)
    if blob((ROOT / 'control/quant_host.py').read_bytes()) != helper:
        raise ValueError('The fixed research controller needs separate trusted installation.')
    transition = None
    if any(blob((ROOT / 'source' / previous / name).read_bytes()) != expected[name] for name in PATHS[2:]):
        from research_runtime_transition import validate

        try:
            transition = validate(revision, previous, expected, original)
        except (ValueError, OSError, KeyError) as error:
            raise ValueError('Changed research runtime dependencies need a separate tested installation.') from error
    verify_inventory(ROOT / 'source' / previous, original['source_files'])
    selected = transition['units'] if transition else (active_units() if units is None else list(units))
    if not set(selected) <= set(UNITS) or len(set(selected)) != len(selected):
        raise ValueError('Research recovery cannot broaden the original service scope.')
    for unit in selected:
        run(['systemctl', 'is-enabled', '--quiet', unit])
    image = json.loads((candidate / 'engineering-image.json').read_text())
    inspected = json.loads(run(['docker', 'image', 'inspect', image['image']]))[0]
    if image['revision'] != revision or inspected['Config']['Labels'].get('org.opencontainers.image.revision') != revision or image['image'] not in inspected['RepoDigests']:
        raise ValueError('Research source requires the exact checked immutable engineering image.')
    name = 'tauric-quant-source-' + revision[:12]
    found = subprocess.run(['docker', 'container', 'inspect', name], capture_output=True, check=False, timeout=30)
    if found.returncode == 0:
        container = json.loads(found.stdout)[0]
        if container['Image'] != inspected['Id'] or container['State']['Status'] != 'created':
            raise ValueError('Original source extraction container needs reconciliation.')
    else:
        run(['docker', 'create', '--name', name, '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--entrypoint', '/bin/true', image['image']])
    try:
        with tempfile.TemporaryDirectory(prefix='release-source-', dir=ROOT) as folder:
            staging = Path(folder)
            (staging / 'backend').mkdir()
            for source in PATHS:
                run(['docker', 'cp', name + ':/source/' + source, str(staging / source)])
            inventory = install_source(staging, ROOT / 'source' / revision, expected)
    finally:
        run(['docker', 'rm', name])
    updated = {**original, 'source_revision': revision, 'source_files': inventory, 'host_control_revision': revision}
    updated.pop('source_archive_sha256', None)
    updated['source_tree_sha256'] = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    return {'original': original, 'target': updated, 'units': selected, 'source_revision': revision, 'files_verified': len(inventory), 'controller_sha256': hashlib.sha256((ROOT / 'control/quant_host.py').read_bytes()).hexdigest(), 'dependency_transition': revision if transition else None}


def stop(prepared):
    if prepared:
        detach_automatic_start()
        if prepared['units']:
            run(['systemctl', 'stop', *prepared['units']])


def detach_automatic_start():
    # The legacy WantedBy edge starts paused research workers whenever the
    # engineering service recovers. Keep boot enablement and PartOf shutdown,
    # but let the journaled release/backup restore only the original workers.
    links = []
    for unit in UNITS:
        link = SYSTEMD / 'tauric-engineering.service.wants' / unit
        if link.exists() or link.is_symlink():
            if not link.is_symlink() or link.resolve() != (SYSTEMD / unit).resolve():
                raise ValueError('An unexpected research dependency needs reconciliation.')
            links.append(link)
    for link in links:
        link.unlink()
    if links:
        run(['systemctl', 'daemon-reload'])


def resume_units(units):
    if not set(units) <= set(UNITS) or len(set(units)) != len(units):
        raise ValueError('Research recovery cannot broaden its recorded worker scope.')
    if not units:
        return
    deadline = time.monotonic() + 120
    while True:
        api = json.loads(run(['docker', 'inspect', 'tauric_server-api-1']))[0]
        if api['State'].get('Health', {}).get('Status') == 'healthy':
            break
        if time.monotonic() >= deadline:
            raise ValueError('Research workers wait for the original healthy API after maintenance.')
        time.sleep(1)
    for unit in units:
        run(['systemctl', 'is-enabled', '--quiet', unit])
    run(['systemctl', 'reset-failed', *units])
    run(['systemctl', 'start', *units])


def resume_original(prepared):
    if prepared and prepared.get('dependency_transition'):
        # Dependencies have already changed under a separately checked receipt.
        # Old source may not resume; only activate() can bind the new source.
        return
    if prepared and prepared['units']:
        if json.loads((ROOT / 'control/config.json').read_text()) != prepared['original']:
            raise ValueError('Previous research recovery refuses a changed source configuration.')
        resume_units(prepared['units'])


def process_source(unit):
    pid = int(run(['systemctl', 'show', unit, '--property=MainPID', '--value']))
    if not pid:
        return None
    try:
        values = (Path('/proc') / str(pid) / 'environ').read_bytes().split(b'\0')
    except FileNotFoundError:
        return None
    return next((v.partition(b'=')[2].decode() for v in values if v.startswith(b'PYTHONPATH=')), None)


def activate(prepared, server, compose):
    if not prepared:
        return {'installed': False, 'workers_resumed': []}
    target, original = prepared['target'], prepared['original']
    if prepared.get('dependency_transition'):
        from research_runtime_transition import validate

        expected = {name: blob((ROOT / 'source' / target['source_revision'] / name).read_bytes()) for name in PATHS[2:]}
        validate(target['source_revision'], original['source_revision'], expected, original)
    if hashlib.sha256((ROOT / 'control/quant_host.py').read_bytes()).hexdigest() != prepared['controller_sha256']:
        raise ValueError('The fixed research controller changed before activation.')
    path = ROOT / 'control/config.json'
    if path.is_symlink():
        raise ValueError('Research configuration changed to a symbolic link.')
    current = json.loads(path.read_text())
    if current not in (original, target):
        raise ValueError('Original research configuration changed before activation.')
    verify_inventory(ROOT / 'source' / target['source_revision'], target['source_files'])
    if current != target:
        # All original workers were stopped before the application snapshot.
        # Never replace a source configuration under a running old controller.
        if any(run(['systemctl', 'show', u, '--property=ActiveState', '--value']).strip() == b'active' for u in prepared['units']):
            raise ValueError('Old research workers are still running before source activation.')
        pending = path.with_name('config.release.pending')
        content = json.dumps(target, indent=2) + '\n'
        if pending.is_symlink() or pending.exists() and pending.read_text() != content:
            raise ValueError('Interrupted research configuration needs reconciliation.')
        pending.write_text(content)
        pending.chmod(0o600)
        pending.replace(path)
    units = prepared['units']
    if units:
        for unit in units:
            run(['systemctl', 'is-enabled', '--quiet', unit])
        run(['systemctl', 'reset-failed', *units])
        run(['systemctl', 'start', *units])
        deadline = time.monotonic() + 90
        expected = str(ROOT / 'source' / target['source_revision'] / 'backend')
        while True:
            if all(process_source(unit) == expected for unit in units):
                break
            if time.monotonic() >= deadline:
                raise ValueError('Research controllers did not resume with their checked source.')
            time.sleep(1)
        names = ['lab_' + u.removeprefix('tauric-quant-').removesuffix('.service') for u in units]
        code = "import json;from sqlalchemy import select;from sqlalchemy.orm import Session;from tauric.database import engine;from tauric.models import WorkerStatus;\nwith Session(engine()) as d:\n print(json.dumps({r.id:r.heartbeat_at.timestamp() for r in d.scalars(select(WorkerStatus).where(WorkerStatus.id.in_(" + repr(names) + ")))}))"
        while True:
            beats = json.loads(server.run(compose + ['exec', '-T', 'api', 'python', '-c', code], stdout=subprocess.PIPE).stdout)
            if set(beats) == set(names) and all(prepared['activation_started_epoch'] <= v <= time.time() and time.time() - v <= 30 for v in beats.values()):
                break
            if time.monotonic() >= deadline:
                raise ValueError('The original research workers have no fresh heartbeat after activation.')
            time.sleep(1)
    return {'installed': True, 'source_revision': target['source_revision'], 'files_verified': prepared['files_verified'], 'workers_resumed': units, 'process_source_verified': True, 'verified_at': datetime.now(UTC).isoformat()}
