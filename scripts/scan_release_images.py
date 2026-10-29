"""Scan both published platforms by immutable digest; no Docker socket is exposed."""

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from security_policy import evaluate

TRIVY = 'aquasec/trivy:0.76.1@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c'
KINDS = ('api', 'worker', 'web', 'openclaw', 'engineering')
PLATFORMS = ('linux/amd64', 'linux/arm64')


def scan(images, output, revision, repository):
    output.mkdir(parents=True, exist_ok=True)
    cache = output / 'cache'
    cache.mkdir(exist_ok=True)
    with Path('deploy/security-exceptions.json').open() as source:
        exceptions = json.load(source)
    summaries = []
    failed = False
    for kind in KINDS:
        entry = json.loads((images / (kind + '.json')).read_text())
        if (entry.get('revision') != revision or entry.get('repository') != repository.lower()
                or not re.fullmatch(re.escape('ghcr.io/' + repository.lower() + '-' + kind) + r'@sha256:[a-f0-9]{64}', entry['image'])):
            raise ValueError('Scan input is not the exact published source image.')
        for platform in PLATFORMS:
            report = output / (kind + '-' + platform.split('/')[1] + '.json')
            if report.exists():
                raise ValueError('Do not reuse a report from an earlier scan.')
            command = ['docker', 'run', '--rm', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                       '--user', str(os.getuid()) + ':' + str(os.getgid()),
                       '-e', 'HOME=/tmp',
                       '-v', str(cache.resolve()) + ':/cache',
                       '-v', str(output.resolve()) + ':/reports',
                       '-v', str(Path(os.environ.get('DOCKER_CONFIG', str(Path.home() / '.docker'))).resolve()) + ':/registry:ro',
                       '-e', 'DOCKER_CONFIG=/registry', TRIVY, 'image', '--cache-dir', '/cache', '--image-src', 'remote', '--platform', platform,
                       '--scanners', 'vuln', '--ignorefile', '/dev/null', '--format', 'json', '--output', '/reports/' + report.name,
                       '--parallel', '1', '--timeout', '10m', entry['image']]
            with tempfile.TemporaryDirectory(prefix='scratch-', dir=output) as scratch:
                # Native executable analysis can exceed memory-backed /tmp.
                command[3:3] = ['-v', str(Path(scratch).resolve()) + ':/tmp']
                result = subprocess.run(command, check=False, timeout=660)
            if result.returncode or not report.is_file():
                failed = True
                summaries.append({'kind': kind, 'platform': platform, 'scan_failed': True})
                continue
            value = json.loads(report.read_text())
            if value.get('Metadata', {}).get('ImageConfig', {}).get('architecture') != platform.split('/')[1]:
                raise ValueError('The scanned image architecture does not match its required platform.')
            assessment = evaluate(value, exceptions, kind=kind, platform=platform)
            failed |= bool(assessment['blocked'])
            summaries.append({'kind': kind, 'platform': platform, 'image': entry['image'], **assessment})
            print(json.dumps({'kind': kind, 'platform': platform, 'blocked': len(assessment['blocked']), 'accepted_exceptions': len(assessment['accepted_exceptions'])}), flush=True)
    (output / 'summary.json').write_text(json.dumps({'revision': revision, 'scans': summaries, 'passed': not failed}, indent=2) + '\n')
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--repository', required=True)
    args = parser.parse_args()
    return scan(args.images_dir, args.output, args.revision, args.repository)


if __name__ == '__main__':
    raise SystemExit(main())
