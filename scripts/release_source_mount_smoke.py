"""Reproduce source-file staging under a real unprivileged Linux bind mount."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from release_bridge import public_source_permissions


def verify(image):
    if os.geteuid() != 0 or sys.platform != 'linux':
        raise ValueError('Run the trusted bind-mount rehearsal as root on Linux.')
    if not re.fullmatch(r'(?:[a-z0-9./:_-]+@)?sha256:[a-f0-9]{64}', image):
        raise ValueError('The bind-mount rehearsal requires an immutable image.')
    with tempfile.TemporaryDirectory(prefix='tauric-source-mode-') as folder:
        root = Path(folder)
        release = root / 'release'
        relative = 'backend/tauric/tradingview_connection.py'
        source = release / relative
        source.parent.mkdir(parents=True, mode=0o700)
        source.write_text('fixture_value = 42\n')
        source.chmod(0o600)
        private = root / 'private-intent'
        private.write_text('Private fixture; never included in source')
        private.chmod(0o600)
        code = "from pathlib import Path;p=Path('/source/" + relative + "');d={};exec(compile(p.read_text(),str(p),'exec'),d);assert d['fixture_value']==42"
        command = ['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges:true', '--user', '10001:10001',
                   '--mount', 'type=bind,source=' + str(release) + ',target=/source,readonly',
                   '--entrypoint', 'python', image, '-I', '-S', '-c', code]
        before = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
        if before.returncode == 0 or 'PermissionError' not in before.stderr:
            raise ValueError('The private staged-source failure was not reproduced.')
        public_source_permissions(release, [relative])
        subprocess.run(command, check=True, capture_output=True, timeout=30)
        if private.stat().st_mode & 0o777 != 0o600:
            raise ValueError('The source correction changed private intent permissions.')
    return {'unprivileged_uid': 10001, 'private_source_rejected': True,
            'verified_public_source_imported': True, 'private_state_unchanged': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.image)))


if __name__ == '__main__':
    main()
